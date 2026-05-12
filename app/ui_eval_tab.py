"""Eval tab UI.

Three flows:

  Quick check
    Pick a past run + a recipe → "Quick check 5 random samples" → human
    judge label per sample → see direction. No setup, no commitment.

  Full eval
    Pick run + recipe → optionally split into devset / testset → pick a
    sampling strategy → both human and LLM judge → see agreement metrics.
    All judgments persist in sqlite under the run folder.

  Optimize
    After enough good/bad judgments are collected, click Optimize → DSPy
    runs against good examples → new prompt produced → saved as a new
    recipe `<name>_optimized_v<N>.json`. Researcher reviews and runs.

Testset is gated: by default only dev partition. Promoting to testset
shows a confirmation, and after testset eval the recipe is locked
(visually flagged + on-disk lock).
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import gradio as gr
import pandas as pd

from . import errors
from .eval import optimizer as opt_mod
from .eval.session import EvalSession
from .inference import OllamaClient
from .recipe import Recipe, list_recipes
from .runs import list_runs

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers — load runs + their associated recipe
# ---------------------------------------------------------------------------


def _enumerate_runs(runs_root: Path) -> list[dict]:
    """List runs that have a results.csv and a recipe in their config."""
    out = []
    for r in list_runs(runs_root):
        cfg = r.get("config", {})
        # Skip workspace dir
        if Path(r["folder"]).parent.name.startswith("_"):
            continue
        if not (Path(r["folder"]) / "results.csv").exists():
            continue
        out.append(r)
    return out


def _label_run(r: dict) -> str:
    cfg = r.get("config") or {}
    n_rows = cfg.get("n_output_rows", "?")
    model = cfg.get("model", "?")
    recipe_name = (cfg.get("recipe") or {}).get("name", "?")
    return f"{r['name']} · {recipe_name} · {model} · {n_rows} rows"


def _load_run_assets(run_folder: str) -> tuple[pd.DataFrame, dict]:
    folder = Path(run_folder)
    results = pd.read_csv(folder / "results.csv")
    cfg = json.loads((folder / "config.json").read_text())
    return results, cfg


# ---------------------------------------------------------------------------
# Tab state shape
# ---------------------------------------------------------------------------
#
# eval_state holds:
#   {
#     "run_folder": str,
#     "session": EvalSession (NOT JSON-serializable; held in memory),
#     "current_cycle": int,
#     "current_sample_ids": list[str],
#   }
#
# We keep it in gr.State; sessions don't persist across page reload, but
# the underlying sqlite does, so re-binding the session on tab-revisit
# recovers all judgments.


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


@errors.safe_handler
def _refresh_runs(runs_root_str: str):
    runs_root = Path(runs_root_str)
    runs = _enumerate_runs(runs_root)
    if not runs:
        return gr.update(choices=[], value=None), "No runs found. Run a recipe first."
    labels = [(_label_run(r), r["folder"]) for r in runs]
    return gr.update(choices=labels, value=labels[0][1]), f"Found {len(runs)} run(s)."


@errors.safe_handler
def _bind_session(run_folder: str):
    if not run_folder:
        errors.error("Pick a run first.")
    folder = Path(run_folder)
    if not folder.exists():
        errors.error(f"Run folder missing: {folder}")
    results, cfg = _load_run_assets(run_folder)
    recipe_dict = cfg.get("recipe", {})
    if not recipe_dict:
        errors.error(f"Run {folder.name} has no recipe in config.json.")

    session = EvalSession(folder, results, recipe_dict, source_df=None)
    session.partition()  # default: all dev
    counts = session.store.partition_counts()

    eval_cfg = recipe_dict.get("eval") or {}
    has_llm = bool((eval_cfg.get("llm_judge") or {}).get("criteria"))
    stratify_field = eval_cfg.get("stratify_by") or session._auto_stratify_field()
    label_options = eval_cfg.get("judge_label_options") or session.human_label_options

    locked = session.is_testset_locked()
    lock_banner = (
        "🔒 **This run's recipe has been TESTSET-EVALUATED.** "
        "Do not change the recipe and re-evaluate on the same testset; "
        "create a fresh testset on a new dataset instead."
    ) if locked else ""

    summary = (
        f"### Run loaded\n"
        f"- recipe: `{recipe_dict.get('name', '?')}`\n"
        f"- type: `{recipe_dict.get('type', '?')}`\n"
        f"- partitions: {counts}\n"
        f"- LLM judge configured: **{'yes' if has_llm else 'no'}**\n"
        f"- stratify field: {f'`{stratify_field}`' if stratify_field else '_(none — random sampling)_'}\n"
        f"- human label options: {label_options}"
    )

    eval_state = {
        "run_folder": run_folder,
        "session": session,
        "current_cycle": None,
        "current_sample_ids": [],
    }
    targetable_field_choices = []
    targetable_field_value_choices = []
    if stratify_field:
        targetable_field_choices = [stratify_field]
        unique = sorted({str(v) for v in results[stratify_field].dropna().unique()})
        targetable_field_value_choices = unique[:30]

    return (
        eval_state,
        summary,
        lock_banner,
        gr.update(choices=label_options, value=label_options[0] if label_options else None),
        gr.update(choices=targetable_field_choices,
                  value=stratify_field if stratify_field else None),
        gr.update(choices=targetable_field_value_choices, value=None),
    )


@errors.safe_handler
def _quick_check(eval_state: dict, n: int = 5):
    """Pick N random samples — the workshop-friendly default."""
    if not eval_state or not eval_state.get("session"):
        errors.error("Bind a run first.")
    session: EvalSession = eval_state["session"]
    ids, strat, cycle = session.sample(int(n), partition="dev", mode="random")
    eval_state["current_sample_ids"] = ids
    eval_state["current_cycle"] = cycle
    view = session.get_eval_view(ids)
    return eval_state, view, f"✓ Picked **{len(ids)}** random sample(s) for quick check."


@errors.safe_handler
def _full_sample(eval_state: dict, n: int, partition: str, strategy: str,
                  target_field: str | None, target_value: str | None):
    if not eval_state or not eval_state.get("session"):
        errors.error("Bind a run first.")
    session: EvalSession = eval_state["session"]

    if partition == "test" and not session.store.list_by_partition("test"):
        errors.error(
            "No testset assigned. Use the **Promote to testset** button below first."
        )

    mode = strategy
    kwargs = {"partition": partition, "mode": mode, "seed": int(time.time()) % 10000}
    if mode == "targeted":
        if not target_field or target_value in (None, ""):
            errors.error("Targeted sampling needs a field and value.")
        kwargs["target_field"] = target_field
        kwargs["target_value"] = target_value

    ids, strat, cycle = session.sample(int(n), **kwargs)
    eval_state["current_sample_ids"] = ids
    eval_state["current_cycle"] = cycle
    view = session.get_eval_view(ids)
    return eval_state, view, f"✓ Picked **{len(ids)}** sample(s) using **{strat}** from **{partition}**."


@errors.safe_handler
def _record_human(eval_state: dict, row_id: str, label: str, comment: str):
    if not eval_state or not eval_state.get("session"):
        errors.error("Bind a run first.")
    if not row_id or not label:
        errors.error("Pick a row id and a label.")
    session: EvalSession = eval_state["session"]
    session.record_human_judgment(
        row_id, label, comment=comment or "",
        cycle=eval_state.get("current_cycle"),
    )
    view = session.get_eval_view(eval_state.get("current_sample_ids", []))
    return view, f"✓ Recorded human judgment for `{row_id}`: **{label}**"


@errors.safe_handler
def _run_llm_judge(eval_state: dict, inference_state: dict):
    if not eval_state or not eval_state.get("session"):
        errors.error("Bind a run first.")
    session: EvalSession = eval_state["session"]
    if not session.has_llm_judge:
        errors.error(
            "This recipe has no LLM judge configured. Add an `eval.llm_judge` "
            "block to the recipe, or skip and use human judgment only."
        )
    if not inference_state or not inference_state.get("host"):
        errors.error("Settings tab: configure Ollama host first.")
    judge_model = inference_state.get("judge_model") or inference_state.get("model")
    if not judge_model:
        errors.error("Settings tab: pick a model (or a separate judge model).")

    client = OllamaClient(host=inference_state["host"])
    if not client.is_alive():
        errors.error(f"Ollama unreachable at {inference_state['host']}.")

    sample_ids = eval_state.get("current_sample_ids", [])
    if not sample_ids:
        errors.error("No samples picked. Pick samples first.")

    results = session.run_llm_judge(
        sample_ids, client=client, model=judge_model,
        cycle=eval_state.get("current_cycle"),
    )
    view = session.get_eval_view(sample_ids)
    n_err = sum(1 for r in results if r["label"] == "ERROR")
    msg = f"✓ LLM judge ran on **{len(results)}** sample(s)"
    if n_err:
        msg += f" — **{n_err}** failed (see _validation_status)"
    return view, msg


@errors.safe_handler
def _agreement(eval_state: dict):
    if not eval_state or not eval_state.get("session"):
        errors.error("Bind a run first.")
    session: EvalSession = eval_state["session"]
    stats = session.agreement_metrics()
    if stats.n_compared == 0:
        return ("_Need at least one row with both a human AND an LLM judgment._",
                gr.update(value=pd.DataFrame()))
    md = (
        f"### Agreement\n"
        f"- compared rows: **{stats.n_compared}**\n"
        f"- exact match rate: **{stats.exact_match_rate:.0%}**\n"
        f"- Cohen's κ: **{stats.cohens_kappa:.3f}**" if stats.cohens_kappa is not None
        else f"- Cohen's κ: n/a"
    )
    # Confusion matrix as a small DataFrame
    pairs = list(stats.confusion.items())
    if pairs:
        all_h = sorted({p[0][0] for p in pairs})
        all_l = sorted({p[0][1] for p in pairs})
        matrix = pd.DataFrame(0, index=all_h, columns=all_l)
        for (h, l), n in pairs:
            matrix.loc[h, l] = n
        matrix.index.name = "human \\ llm"
        matrix = matrix.reset_index()
    else:
        matrix = pd.DataFrame()
    return md, gr.update(value=matrix)


@errors.safe_handler
def _promote_testset(eval_state: dict, fraction: float):
    if not eval_state or not eval_state.get("session"):
        errors.error("Bind a run first.")
    session: EvalSession = eval_state["session"]
    if session.is_testset_locked():
        errors.error("Testset is already locked for this run.")
    fraction = max(0.05, min(0.5, float(fraction)))
    counts = session.partition(test_fraction=fraction, seed=42)
    return f"✓ Testset created: {counts}. **Don't change the recipe before testset eval.**"


@errors.safe_handler
def _lock_testset(eval_state: dict):
    if not eval_state or not eval_state.get("session"):
        errors.error("Bind a run first.")
    session: EvalSession = eval_state["session"]
    if session.is_testset_locked():
        errors.warn("Already locked.")
        return "🔒 Already locked."
    session.lock_testset(note="locked from UI")
    return (
        "🔒 **TESTSET LOCKED.** This recipe has been evaluated on the testset. "
        "Do not modify the recipe and re-evaluate on the same testset — that "
        "invalidates the result."
    )


@errors.safe_handler
def _run_optimizer(eval_state: dict, inference_state: dict, recipes_root: str):
    if not eval_state or not eval_state.get("session"):
        errors.error("Bind a run first.")
    if not opt_mod.is_available():
        errors.error(
            "DSPy isn't installed. Add `dspy-ai` to requirements.txt and "
            "`pip install dspy-ai` to enable the optimizer."
        )
    session: EvalSession = eval_state["session"]
    optimizer_model = (inference_state or {}).get("optimizer_model") or \
                      (inference_state or {}).get("model")
    if not optimizer_model:
        errors.error("Settings tab: pick a model (or separate optimizer model).")

    labeled = session.store.labeled_examples()
    recipe = Recipe.from_dict(session.recipe_dict)
    try:
        result = opt_mod.optimize(
            recipe,
            session.results_df,
            labeled,
            source_df=session.source_df,
            optimizer_model=optimizer_model,
            ollama_host=inference_state["host"],
            save_to=Path(recipes_root),
        )
    except Exception as exc:
        errors.error(f"Optimization failed: {exc}")

    msg = (
        f"✓ Optimization complete\n\n"
        f"- training examples: **{result.n_train_examples}**\n"
        f"- demos added to prompt: **{result.n_demos_added}**\n"
        f"- new recipe saved: `{result.new_recipe_path}`\n\n"
        f"Reload recipes on the **Recipes** tab to see it."
    )
    return msg


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def build_eval_tab(
    *,
    inference_state: gr.State,
    runs_root: str,
    recipes_root: str,
):
    eval_state = gr.State(value={})

    gr.Markdown(
        "### Eval\n"
        "Pick a run to evaluate. **Quick check** is the workshop default — "
        "5 random samples, one click. **Full eval** unlocks devset/testset, "
        "stratified sampling, LLM judge, and agreement metrics."
    )

    with gr.Row():
        runs_dd = gr.Dropdown(label="Run", choices=[], scale=4)
        refresh_btn = gr.Button("🔄 Refresh runs", scale=1)
    bind_btn = gr.Button("Bind to this run", variant="primary")
    bind_status = gr.Markdown()
    lock_banner = gr.Markdown()

    refresh_btn.click(_refresh_runs, gr.State(runs_root), [runs_dd, bind_status])

    # ---- Quick check ----
    with gr.Tab("Quick check"):
        gr.Markdown("Random small sample. No partitions, no LLM judge — just see how it's doing.")
        with gr.Row():
            quick_n = gr.Slider(2, 20, value=5, step=1, label="Samples")
            quick_btn = gr.Button("Pick samples", variant="primary")
        quick_status = gr.Markdown()
        quick_view = gr.Dataframe(label="Picked samples + judgments", wrap=True)

        quick_btn.click(_quick_check, [eval_state, quick_n], [eval_state, quick_view, quick_status])

    # ---- Full eval ----
    with gr.Tab("Full eval"):
        gr.Markdown("#### Sample")
        with gr.Row():
            full_n = gr.Slider(2, 100, value=10, step=1, label="N samples", scale=1)
            full_partition = gr.Radio(["dev", "test"], value="dev", label="Partition", scale=1)
            full_strategy = gr.Radio(
                ["auto", "stratified", "random", "targeted"],
                value="auto", label="Strategy", scale=2,
            )
        with gr.Row():
            full_target_field = gr.Dropdown(label="Target field (only for 'targeted')", choices=[], scale=2)
            full_target_value = gr.Dropdown(
                label="Target value", choices=[],
                allow_custom_value=True, scale=2,
            )
        full_pick_btn = gr.Button("Pick samples", variant="primary")
        full_status = gr.Markdown()
        full_view = gr.Dataframe(label="Picked samples + judgments", wrap=True)

        full_pick_btn.click(
            _full_sample,
            [eval_state, full_n, full_partition, full_strategy,
             full_target_field, full_target_value],
            [eval_state, full_view, full_status],
        )

        gr.Markdown("#### Human judgment")
        with gr.Row():
            human_row_id = gr.Textbox(label="Row ID (copy from table above)", scale=2)
            human_label = gr.Dropdown(label="Label", choices=[], scale=2)
            human_comment = gr.Textbox(label="Comment (optional)", scale=3)
            human_save_btn = gr.Button("Record", variant="secondary", scale=1)
        human_status = gr.Markdown()

        human_save_btn.click(
            _record_human, [eval_state, human_row_id, human_label, human_comment],
            [full_view, human_status],
        )

        gr.Markdown("#### LLM judge")
        llm_btn = gr.Button("Run LLM judge on the picked samples", variant="primary")
        llm_status = gr.Markdown()
        llm_btn.click(_run_llm_judge, [eval_state, inference_state], [full_view, llm_status])

        gr.Markdown("#### Agreement")
        agree_btn = gr.Button("Compute agreement (human vs LLM)")
        agree_md = gr.Markdown()
        agree_matrix = gr.Dataframe(label="Confusion matrix", wrap=True)
        agree_btn.click(_agreement, eval_state, [agree_md, agree_matrix])

    # ---- Testset ----
    with gr.Tab("Testset (advanced)"):
        gr.Markdown(
            "⚠️ **Use the testset only when you've finished iterating on dev.** "
            "Once you evaluate on testset and lock it, changing the recipe and "
            "re-evaluating on the same testset invalidates your result. "
            "Treat this like a final exam, not a dry run."
        )
        with gr.Row():
            promote_fraction = gr.Slider(0.1, 0.5, value=0.2, step=0.05,
                                          label="Test fraction (rest stays dev)")
            promote_btn = gr.Button("Promote a testset", variant="secondary")
        promote_status = gr.Markdown()
        promote_btn.click(_promote_testset, [eval_state, promote_fraction], promote_status)

        gr.Markdown("---")
        gr.Markdown("After running eval on testset, lock it to record the result.")
        lock_btn = gr.Button("🔒 Lock testset (irreversible)", variant="stop")
        lock_status = gr.Markdown()
        lock_btn.click(_lock_testset, eval_state, lock_status)

    # ---- Optimize ----
    with gr.Tab("Optimize"):
        gr.Markdown(
            "Use collected good/bad judgments to improve the recipe's prompt. "
            "Runs DSPy BootstrapFewShot against your 'good' examples and saves "
            "the optimized prompt as a new recipe.\n\n"
            "**Requires** at least 3 'good'/'correct' labels on dev samples."
        )
        opt_btn = gr.Button("Run DSPy optimizer", variant="primary")
        opt_status = gr.Markdown()
        opt_btn.click(
            _run_optimizer, [eval_state, inference_state, gr.State(recipes_root)], opt_status,
        )

    # Wire bind to outputs
    bind_btn.click(
        _bind_session, runs_dd,
        [eval_state, bind_status, lock_banner,
         human_label, full_target_field, full_target_value],
    )
