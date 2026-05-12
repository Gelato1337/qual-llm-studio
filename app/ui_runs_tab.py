"""Runs tab — pick dataset + recipe + run.

This is the v5 execution surface. The researcher:

  1. Picks a dataset CSV from the `datasets/` directory
  2. Picks a recipe (from `recipes/custom/`)
  3. Optionally edits sampling (limit to first N rows for dry-run)
  4. Clicks Run
  5. Results land in `results/<dataset>__<recipe>__<ts>.csv`

Critical wiring rule: the model & host come **directly from the Settings
tab's components**, not from a possibly-stale gr.State. This fixes the
"Set a model" phantom error.
"""

from __future__ import annotations

import contextlib
import io
import json
import logging
import sys
import traceback
from pathlib import Path

import gradio as gr
import pandas as pd

from . import errors, paths
from .dispatcher import run as dispatcher_run
from .inference import DEFAULT_HOST, OllamaClient, OllamaError
from .recipe import Recipe, list_recipes
from .runs import create_run_folder, write_results

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


CURRENT_DATASET_SENTINEL = "__current_in_memory__"


def _list_dataset_choices(docs_df=None) -> list[tuple[str, str]]:
    """Return [(label, path), ...] for the dropdown.

    Includes the current in-memory dataset (from docs_state) at the top
    if it has data, so researchers can run a recipe without an explicit
    'Save dataset' click first.
    """
    out: list[tuple[str, str]] = []
    if docs_df is not None and len(docs_df) > 0:
        out.append(
            (f"⚡ Current (unsaved, {len(docs_df):,} rows from Data tab)",
             CURRENT_DATASET_SENTINEL),
        )
    for p in paths.list_datasets():
        try:
            df = pd.read_csv(p, nrows=0)
            ncols = len(df.columns)
        except Exception:  # noqa: BLE001
            ncols = "?"
        out.append((f"{p.stem} ({ncols} cols)", str(p)))
    return out


def _resolve_dataset(dataset_path: str, docs_df) -> pd.DataFrame | None:
    """Resolve a dataset_path value to a DataFrame.

    Sentinel → docs_df. Otherwise read from disk. None on failure.
    """
    if not dataset_path:
        return None
    if dataset_path == CURRENT_DATASET_SENTINEL:
        if docs_df is None or len(docs_df) == 0:
            return None
        return docs_df.copy()
    try:
        return pd.read_csv(dataset_path)
    except Exception:  # noqa: BLE001
        return None


def _list_recipe_choices(recipes: list[Recipe]) -> list[tuple[str, str]]:
    out = []
    for r in recipes:
        out.append((r.name, r.name))
    return out


def _find_recipe(recipes: list[Recipe], name: str) -> Recipe | None:
    for r in recipes:
        if r.name == name:
            return r
    return None


@contextlib.contextmanager
def _suppress_stdout_to_log():
    """Capture stdout so noisy library output goes to logs, not the UI."""
    old = sys.stdout
    buf = io.StringIO()
    sys.stdout = buf
    try:
        yield buf
    finally:
        sys.stdout = old
        captured = buf.getvalue()
        if captured.strip():
            log.info("captured stdout from runner:\n%s", captured)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


@errors.safe_handler
def refresh_lists(recipes_state: list[Recipe], docs_df):
    return (
        gr.update(choices=_list_dataset_choices(docs_df)),
        gr.update(choices=_list_recipe_choices(recipes_state)),
    )


@errors.safe_handler
def refresh_dataset_dropdown(docs_df):
    return gr.update(choices=_list_dataset_choices(docs_df))


@errors.safe_handler
def preview_dataset(dataset_path: str, docs_df):
    if not dataset_path:
        return pd.DataFrame(), ""
    df = _resolve_dataset(dataset_path, docs_df)
    if df is None:
        if dataset_path == CURRENT_DATASET_SENTINEL:
            return pd.DataFrame(), "No data loaded — go to **Data** tab and click Apply first."
        return pd.DataFrame(), "Failed to read dataset."
    source = "current in-memory" if dataset_path == CURRENT_DATASET_SENTINEL else "disk"
    summary = (
        f"**{len(df):,}** rows ({source}) · "
        f"**{len(df.columns)}** columns: "
        + ", ".join(f"`{c}`" for c in df.columns[:8])
        + (f" + {len(df.columns) - 8} more" if len(df.columns) > 8 else "")
    )
    return df.head(5), summary


@errors.safe_handler
def preview_recipe(recipe_name: str, recipes_state: list[Recipe]):
    r = _find_recipe(recipes_state, recipe_name)
    if r is None:
        return ""
    return r.render_as_text()


def run_recipe(
    dataset_path: str,
    recipe_name: str,
    sample_n,
    n_parallel,
    host: str,
    model: str,
    recipes_state: list[Recipe],
    docs_state_value,
    progress=gr.Progress(track_tqdm=True),
):
    """Run handler. Reads model + host directly from Settings components.

    NOT decorated with @safe_handler because we yield progress updates;
    we do our own try/except.

    n_parallel:
      0 or 'auto'  → auto-detect via app.resources.detect()
      positive int → use that many threads

    dataset_path:
      Either an absolute path to a saved CSV, or the
      CURRENT_DATASET_SENTINEL ("__current_in_memory__"), which means
      "use docs_state directly without re-saving."
    """
    try:
        if not dataset_path:
            raise gr.Error("Pick a dataset first.")
        if not recipe_name:
            raise gr.Error("Pick a recipe first.")
        if not (model or "").strip():
            raise gr.Error(
                "No model selected. Settings tab → pick a model from the dropdown."
            )
        if not (host or "").strip():
            host = DEFAULT_HOST

        # Resolve parallelism
        try:
            n_parallel_int = int(n_parallel) if n_parallel else 0
        except (ValueError, TypeError):
            n_parallel_int = 0
        if n_parallel_int <= 0:
            from . import resources as _res
            detected = _res.detect(ollama_host=host)
            n_parallel_int = detected.suggested_parallelism
            log.info("auto-detected parallelism=%d (%s)",
                     n_parallel_int, detected.detection_notes)

        recipe = _find_recipe(recipes_state, recipe_name)
        if recipe is None:
            raise gr.Error(f"Recipe {recipe_name!r} not found.")

        validation = recipe.validate()
        if validation:
            raise gr.Error("Recipe invalid:\n - " + "\n - ".join(validation))

        # Load dataset (in-memory or from disk)
        docs = _resolve_dataset(dataset_path, docs_state_value)
        if docs is None:
            if dataset_path == CURRENT_DATASET_SENTINEL:
                raise gr.Error(
                    "No in-memory dataset — go to Data tab and click Apply first."
                )
            raise gr.Error(f"Failed to read dataset from {dataset_path}.")

        if "id" not in docs.columns:
            docs = docs.copy()
            docs.insert(0, "id", [f"row_{i}" for i in range(len(docs))])
        if "text" not in docs.columns:
            # We allow this because some recipes use {{column}} placeholders
            # that don't depend on a `text` column. Just warn.
            errors.warn(
                "Dataset has no `text` column. The recipe must reference its "
                "columns explicitly via {{column_name}} placeholders."
            )

        # Sample-N
        sample_n = int(sample_n) if sample_n else 0
        if sample_n > 0 and len(docs) > sample_n:
            docs = docs.head(sample_n).reset_index(drop=True)

        # Pre-flight LLM check
        client = OllamaClient(host=host)
        if not client.is_alive(timeout=2.0):
            raise gr.Error(
                f"Ollama unreachable at `{host}`. "
                "Start it (`ollama serve`) or fix the host in Settings."
            )
        if not client.has_model(model):
            raise gr.Error(
                f"Model `{model}` is not pulled. Settings → Pull this model first."
            )

        yield (None, f"Starting run on {len(docs)} rows · {n_parallel_int} parallel thread(s)…", "")

        # Set up dest paths. For in-memory datasets, use a tagged name so
        # the results file isn't called "__current_in_memory__".
        if dataset_path == CURRENT_DATASET_SENTINEL:
            dataset_name = "current"
        else:
            dataset_name = Path(dataset_path).stem
        results_filename = paths.build_results_filename(dataset_name, recipe.name)
        results_path = paths.RESULTS_DIR / results_filename
        run_folder = create_run_folder(paths.RUNS_DIR, recipe.type.replace(".", "_"), model)

        # Run
        try:
            with _suppress_stdout_to_log():
                results, logs = dispatcher_run(
                    docs, recipe,
                    client=client, model=model,
                    save_dir=run_folder, ollama_host=host,
                    n_parallel=int(n_parallel_int),
                )
        except OllamaError as exc:
            raise gr.Error(f"Ollama error: {exc}")
        except Exception as exc:  # noqa: BLE001
            tb = traceback.format_exc()
            (run_folder / "FAILED.txt").write_text(tb)
            log.error("recipe run failed: %s\n%s", exc, tb)
            raise gr.Error(f"Run failed: {type(exc).__name__}: {exc}")

        # Write results CSV (the canonical output)
        results_path.parent.mkdir(parents=True, exist_ok=True)
        results.to_csv(results_path, index=False)

        # Mirror to run folder for diagnostics
        config = {
            "recipe": recipe.to_dict(),
            "model": model,
            "ollama_host": host,
            "dataset_path": dataset_path,
            "n_input_rows": len(docs),
            "n_output_rows": len(results),
        }
        write_results(run_folder, results, config, raw_responses=logs)

        summary = (
            f"✓ Run complete · **{len(results)}** rows from **{len(docs)}** input · "
            f"saved to `{results_path.name}`"
        )
        yield (results.head(20), summary, str(results_path))
    except gr.Error:
        raise
    except Exception as exc:  # noqa: BLE001
        tb = traceback.format_exc()
        log.error("unexpected error in run handler: %s\n%s", exc, tb)
        raise gr.Error(f"Unexpected error: {type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def build_runs_tab(
    *,
    recipes_state: gr.State,
    docs_state: gr.State,
    settings_host: gr.Textbox,
    settings_model: gr.Dropdown,
):
    gr.Markdown(
        "### Runs\n"
        "Pick a dataset and a recipe, click Run. "
        "Results land in `results/` as a CSV named `<dataset>__<recipe>__<timestamp>.csv`. "
        "The ⚡ Current option uses whatever's currently in the Data tab — "
        "no need to save to disk first."
    )

    with gr.Row():
        dataset_dd = gr.Dropdown(
            label="Dataset",
            choices=_list_dataset_choices(docs_state.value),
            scale=3,
        )
        recipe_dd = gr.Dropdown(
            label="Recipe (from recipes/)",
            choices=[],
            scale=3,
        )
        refresh_btn = gr.Button("🔄 Refresh", scale=1)

    with gr.Row():
        sample_n = gr.Number(
            label="Sample first N rows (0 = all). Use 5-20 for a fast dry run.",
            value=0, precision=0, scale=2,
        )
        n_parallel_in = gr.Number(
            label="Parallel threads (0 = auto-detect)",
            value=0, precision=0, scale=1,
        )
        detect_btn = gr.Button("Detect resources", scale=1, size="sm")
    resource_md = gr.Markdown()

    def _detect_resources(host):
        from . import resources as _res
        r = _res.detect(ollama_host=host or DEFAULT_HOST)
        backend = {"cpu": "CPU only", "nvidia": f"{r.n_gpus} NVIDIA GPU(s)",
                   "amd": f"{r.n_gpus} AMD GPU(s)",
                   "unknown_gpu": "GPU detected (count unknown)"}.get(r.backend, r.backend)
        return (
            f"**Detected:** {backend} · {r.cpu_count} CPU cores  \n"
            f"**Suggested parallelism:** {r.suggested_parallelism}  \n"
            f"_{r.detection_notes}_"
        )
    detect_btn.click(_detect_resources, settings_host, resource_md)

    with gr.Row():
        with gr.Column(scale=2):
            gr.Markdown("#### Dataset preview")
            dataset_preview = gr.Dataframe(label="First 5 rows", wrap=True)
            dataset_summary = gr.Markdown()
        with gr.Column(scale=3):
            gr.Markdown("#### Recipe preview")
            recipe_preview = gr.Markdown()

    run_btn = gr.Button("▶ Run", variant="primary", size="lg")
    run_status = gr.Markdown()
    results_preview = gr.Dataframe(label="Results (first 20 rows)", wrap=True)
    last_result_path = gr.State(value="")

    # Wiring
    refresh_btn.click(refresh_lists, [recipes_state, docs_state], [dataset_dd, recipe_dd])
    dataset_dd.change(preview_dataset, [dataset_dd, docs_state], [dataset_preview, dataset_summary])
    recipe_dd.change(preview_recipe, [recipe_dd, recipes_state], recipe_preview)

    # Auto-refresh dataset dropdown when docs_state changes (e.g. researcher
    # just hit Apply on the Data tab)
    docs_state.change(refresh_dataset_dropdown, docs_state, dataset_dd)

    # CRUCIAL: model and host come from the Settings dropdowns directly
    run_btn.click(
        run_recipe,
        [dataset_dd, recipe_dd, sample_n, n_parallel_in,
         settings_host, settings_model, recipes_state, docs_state],
        [results_preview, run_status, last_result_path],
    )

    # On tab build, pre-populate the recipe dropdown from current state
    if recipes_state.value:
        recipe_dd.choices = _list_recipe_choices(recipes_state.value)
