"""Quick Eval — schema-driven, per-field human review.

Workflow:
  1. Pick a results CSV from `results/`
  2. (Auto) infer the recipe from the filename
  3. Click "Pick samples" (default 5)
  4. For each sample: see field widgets per output field, plus source
     text if any free-text field is present. Each enum field gets a
     radio with the recipe's allowed options. Each text field gets a
     ✓/⚠/✗ radio.
  5. Submit → next sample
  6. After last sample: per-field alignment summary, saved into CSV

Implementation note: Gradio components are static — we can't materialize
N radios per sample. We pre-declare a pool of MAX_FIELDS radios (8) and
reconfigure each sample which ones are visible and what labels/choices
they have. Same trick for the source text panel (one Markdown, content
changes per sample).

Saved columns:
  human_<field>             # for enum/list/number: "ok" or corrected value
  human_<field>_judgment    # for text: good | partial | bad
  quick_eval_comment
  quick_eval_session_id
"""

from __future__ import annotations

import logging
import random
import uuid
from pathlib import Path
from typing import Any

import gradio as gr
import pandas as pd

from . import errors, paths
from .recipe import Recipe, FieldSpec

log = logging.getLogger(__name__)

# Pre-declared widget pool size. Recipes with more fields than this get
# the first MAX_FIELDS reviewed; extras silently skipped (we'll log a warning).
MAX_FIELDS = 8


# ---------------------------------------------------------------------------
# Results discovery
# ---------------------------------------------------------------------------


def _list_results_choices() -> list[tuple[str, str]]:
    out = []
    for p in paths.list_results():
        try:
            df = pd.read_csv(p, nrows=0)
            ncols = len(df.columns)
        except Exception:  # noqa: BLE001
            ncols = "?"
        out.append((f"{p.stem} ({ncols} cols)", str(p)))
    return out


def _infer_recipe_name(results_path: str) -> str | None:
    stem = Path(results_path).stem
    parts = stem.split("__")
    if len(parts) >= 3:
        return parts[1]
    return None


def _find_recipe_for(results_path: str, recipes: list[Recipe]) -> Recipe | None:
    name = _infer_recipe_name(results_path)
    if not name:
        return None
    for r in recipes:
        if r.name == name:
            return r
    return None


def _is_quote_field(field_spec: FieldSpec, recipe: Recipe) -> bool:
    if field_spec.name.lower() == "quote":
        return True
    for step in recipe.post_process:
        if step.type == "ground_quotes":
            if step.params.get("quote_field", "quote") == field_spec.name:
                return True
    return False


def _eval_fields(recipe: Recipe) -> list[FieldSpec]:
    if not recipe.output or not recipe.output.fields:
        return []
    requested = set((recipe.eval_config or {}).get("fields") or [])
    out = []
    for f in recipe.output.fields:
        if requested and f.name not in requested:
            continue
        out.append(f)
    return out[:MAX_FIELDS]


def _try_load_source_dataset(results_path: str) -> dict[str, str]:
    stem = Path(results_path).stem
    parts = stem.split("__")
    if len(parts) < 3:
        return {}
    dataset_name = parts[0]
    candidate = paths.DATASETS_DIR / f"{dataset_name}.csv"
    if not candidate.exists():
        return {}
    try:
        df = pd.read_csv(candidate)
    except Exception:  # noqa: BLE001
        return {}
    if "id" not in df.columns:
        return {}
    text_col = "text" if "text" in df.columns else None
    if text_col is None:
        for c in df.columns:
            if c != "id":
                text_col = c
                break
    if text_col is None:
        return {}
    return {str(row["id"]): str(row[text_col]) for _, row in df.iterrows()}


def _resolve_source_text(row: pd.Series, dataset_lookup: dict[str, str]) -> str:
    doc_id = row.get("doc_id") or row.get("id")
    if doc_id and dataset_lookup.get(str(doc_id)):
        return dataset_lookup[str(doc_id)]
    for c in ("text", "source_text", "input_text"):
        if c in row.index and row.get(c):
            return str(row[c])
    return ""


# ---------------------------------------------------------------------------
# Widget configuration per field
# ---------------------------------------------------------------------------


def _build_field_widget_config(
    field_spec: FieldSpec, llm_value: Any, recipe: Recipe
) -> dict:
    """Return a dict describing how this field's widget should look this turn.

    Keys:
      mode: 'enum' | 'list_enum' | 'number' | 'text_3state'
      label: str — what to show above the widget
      choices: list — for radio/checkbox
      default: value preselected
      llm_value_display: str — the LLM's actual output (shown in label)
      show_source: bool — should source text be visible?
    """
    llm_str = "" if llm_value is None or (isinstance(llm_value, float) and pd.isna(llm_value)) else str(llm_value)
    config = {
        "field_name": field_spec.name,
        "llm_value_display": llm_str,
        "show_source": False,
    }

    if field_spec.type == "enum" and field_spec.allowed:
        # Radio with: ✓ correct + each allowed option as "wrong: <option>"
        # Simpler: just show ✓ correct or pick the right one
        choices = ["✓ correct"] + [str(o) for o in field_spec.allowed]
        config["mode"] = "enum"
        config["choices"] = choices
        config["default"] = "✓ correct"
        config["label"] = (
            f"**{field_spec.name}** ({field_spec.type}) "
            f"— LLM picked: `{llm_str}`"
        )
    elif field_spec.type == "list" and field_spec.item_type == "enum":
        # Checkbox group with allowed options + extra "✓ correct" pseudo
        config["mode"] = "list_enum"
        config["choices"] = [str(o) for o in (field_spec.allowed or [])]
        # Default selection: parse the LLM's list and pre-tick those
        try:
            import ast
            parsed = ast.literal_eval(llm_str) if llm_str.startswith("[") else None
        except (ValueError, SyntaxError):
            parsed = None
        config["default"] = parsed if isinstance(parsed, list) else []
        config["label"] = (
            f"**{field_spec.name}** ({field_spec.type}[enum]) "
            f"— LLM picked: `{llm_str}`. "
            "Edit checkboxes to correct."
        )
    elif field_spec.type == "number":
        config["mode"] = "number"
        try:
            config["default"] = float(llm_str) if llm_str else 0
        except ValueError:
            config["default"] = 0
        mn = field_spec.min if field_spec.min is not None else "-∞"
        mx = field_spec.max if field_spec.max is not None else "∞"
        config["label"] = (
            f"**{field_spec.name}** (number, range {mn}..{mx}) "
            f"— LLM said: `{llm_str}`. "
            "Edit if wrong."
        )
    else:
        # text → ✓ / ⚠ / ✗
        config["mode"] = "text_3state"
        config["choices"] = ["✓ good", "⚠ partial", "✗ bad"]
        config["default"] = "✓ good"
        is_quote = _is_quote_field(field_spec, recipe)
        suffix = " (quote — verify it's actually in source)" if is_quote else ""
        # Truncate displayed LLM text if very long
        llm_display = llm_str if len(llm_str) <= 300 else llm_str[:300] + "…"
        config["label"] = (
            f"**{field_spec.name}** (text){suffix}\n\n"
            f"LLM output: _{llm_display}_"
        )
        config["show_source"] = True
    return config


def _decode_widget_response(field_spec: FieldSpec, raw_value: Any) -> Any:
    """Convert what the widget returned into the value we save in CSV."""
    if field_spec.type == "enum":
        if raw_value in (None, "✓ correct"):
            return "ok"
        return str(raw_value)
    elif field_spec.type == "list" and field_spec.item_type == "enum":
        # raw is a list from the checkbox group
        return raw_value if isinstance(raw_value, list) else []
    elif field_spec.type == "number":
        try:
            return float(raw_value) if raw_value is not None else None
        except (ValueError, TypeError):
            return raw_value
    else:
        # text → "good"/"partial"/"bad"
        if raw_value == "✓ good":
            return "good"
        if raw_value == "⚠ partial":
            return "partial"
        if raw_value == "✗ bad":
            return "bad"
        return "good"


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


@errors.safe_handler
def refresh_results_list():
    return gr.update(choices=_list_results_choices())


@errors.safe_handler
def on_pick_results(results_path: str, recipes_state: list[Recipe]):
    if not results_path:
        return ({}, "_Pick a results file._")
    recipe = _find_recipe_for(results_path, recipes_state)
    if recipe is None:
        return (
            {},
            f"⚠️ Couldn't find recipe `{_infer_recipe_name(results_path)}`. "
            "Reload recipes from Chat tab.",
        )
    fields = _eval_fields(recipe)
    if not fields:
        return ({}, "⚠️ Recipe has no output fields to evaluate.")

    field_lines = []
    for f in fields:
        line = f"- **{f.name}** ({f.type})"
        if f.allowed:
            line += f" — {f.allowed}"
        if _is_quote_field(f, recipe):
            line += " — _quote field, source shown_"
        field_lines.append(line)

    try:
        df = pd.read_csv(results_path)
        n_rows = len(df)
    except Exception as exc:  # noqa: BLE001
        return ({}, f"Failed to read: {exc}")

    if len(recipe.output.fields) > MAX_FIELDS:
        field_lines.append(
            f"\n⚠️ Recipe has {len(recipe.output.fields)} fields; only first "
            f"{MAX_FIELDS} are reviewable in Quick Eval."
        )

    summary = (
        f"**Recipe:** `{recipe.name}` · **Rows in results:** {n_rows}\n\n"
        f"**Fields to review:**\n" + "\n".join(field_lines)
    )
    state = {"results_path": results_path, "recipe_name": recipe.name}
    return state, summary


def _empty_field_updates():
    """Return updates that hide all field widgets — used when no sample active."""
    return [gr.update(visible=False)] * MAX_FIELDS


def _build_per_sample_view(state: dict, recipe: Recipe, df: pd.DataFrame):
    """Build the per-field updates for the current sample.

    Returns a flat list matching the wired output components:
      [progress_md_value,
       source_md_value,
       summary_md_update,
       submit_btn_update,
       comment_in_value,
       *enum_radio_updates (MAX_FIELDS),
       *list_check_updates (MAX_FIELDS),
       *number_in_updates (MAX_FIELDS),
       *text_radio_updates (MAX_FIELDS)]

    Each widget update is gr.update(label=..., choices=..., value=..., visible=...).
    For widgets that aren't used this sample, visible=False.
    """
    fields = _eval_fields(recipe)
    n_total = len(state["row_indices"])
    current = state["current"]

    if current >= n_total:
        # Done — render summary
        labels = state["labels"]
        per_field_lines = []
        for f in fields:
            if f.type in ("enum",):
                ok = sum(1 for L in labels.values() if L.get(f.name) == "ok")
                per_field_lines.append(
                    f"- **{f.name}** ({f.type}): {ok}/{n_total} confirmed correct"
                )
            elif f.type == "list":
                ok_count = sum(1 for L in labels.values() if L.get(f.name) is not None)
                per_field_lines.append(
                    f"- **{f.name}** ({f.type}): {ok_count}/{n_total} reviewed"
                )
            elif f.type == "number":
                changed = sum(1 for L in labels.values()
                              if L.get(f.name) is not None
                              and isinstance(L.get(f.name), (int, float)))
                per_field_lines.append(
                    f"- **{f.name}** (number): {n_total - changed}/{n_total} confirmed, "
                    f"{changed} corrected"
                )
            else:
                good = sum(1 for L in labels.values() if L.get(f.name) == "good")
                partial = sum(1 for L in labels.values() if L.get(f.name) == "partial")
                bad = sum(1 for L in labels.values() if L.get(f.name) == "bad")
                per_field_lines.append(
                    f"- **{f.name}** (text): ✓ {good}  ·  ⚠ {partial}  ·  ✗ {bad}"
                )
        summary = (
            f"## ✓ Quick eval complete\n\n"
            f"**Samples reviewed:** {n_total}\n\n"
            f"**Per-field results:**\n" + "\n".join(per_field_lines) + "\n\n"
            f"_(Saved to `{Path(state['results_path']).name}` · session: `{state['session_id']}`)_"
        )
        return [
            "",                                                # progress
            "",                                                # source
            gr.update(visible=True, value=summary),            # summary
            gr.update(visible=False),                          # submit
            "",                                                # comment
            *_empty_field_updates(),                           # enum radios
            *_empty_field_updates(),                           # list checks
            *_empty_field_updates(),                           # number inputs
            *_empty_field_updates(),                           # text radios
        ]

    row_idx = state["row_indices"][current]
    row = df.iloc[row_idx]
    progress = f"_Sample {current + 1} of {n_total} · row id `{row.get('doc_id', row_idx)}`_"

    # Source text — only show if any text field exists in this recipe
    has_text = any(f.type not in ("enum", "list", "number") for f in fields)
    source_md = ""
    if has_text:
        ds_lookup = _try_load_source_dataset(state["results_path"])
        source = _resolve_source_text(row, ds_lookup)
        if source:
            if len(source) > 1500:
                source = source[:1500] + "…"
            source_md = f"**Source text:**\n\n```\n{source}\n```"
        else:
            source_md = (
                "_(Source text not found. The original dataset CSV needs to be in "
                "`datasets/` for source-aware text-field eval.)_"
            )

    # Per-field widget configs
    enum_updates = []
    list_updates = []
    number_updates = []
    text_updates = []

    for f in fields:
        cfg = _build_field_widget_config(f, row.get(f.name), recipe)
        mode = cfg["mode"]
        if mode == "enum":
            enum_updates.append(gr.update(
                label=cfg["label"], choices=cfg["choices"],
                value=cfg["default"], visible=True,
            ))
            list_updates.append(gr.update(visible=False))
            number_updates.append(gr.update(visible=False))
            text_updates.append(gr.update(visible=False))
        elif mode == "list_enum":
            enum_updates.append(gr.update(visible=False))
            list_updates.append(gr.update(
                label=cfg["label"], choices=cfg["choices"],
                value=cfg["default"], visible=True,
            ))
            number_updates.append(gr.update(visible=False))
            text_updates.append(gr.update(visible=False))
        elif mode == "number":
            enum_updates.append(gr.update(visible=False))
            list_updates.append(gr.update(visible=False))
            number_updates.append(gr.update(
                label=cfg["label"], value=cfg["default"], visible=True,
            ))
            text_updates.append(gr.update(visible=False))
        else:  # text_3state
            enum_updates.append(gr.update(visible=False))
            list_updates.append(gr.update(visible=False))
            number_updates.append(gr.update(visible=False))
            text_updates.append(gr.update(
                label=cfg["label"], choices=cfg["choices"],
                value=cfg["default"], visible=True,
            ))

    # Pad with hidden updates
    while len(enum_updates) < MAX_FIELDS:
        enum_updates.append(gr.update(visible=False))
        list_updates.append(gr.update(visible=False))
        number_updates.append(gr.update(visible=False))
        text_updates.append(gr.update(visible=False))

    return [
        progress,                                          # progress
        source_md,                                         # source
        gr.update(visible=False),                          # summary
        gr.update(visible=True),                           # submit
        "",                                                # comment (cleared)
        *enum_updates,
        *list_updates,
        *number_updates,
        *text_updates,
    ]


@errors.safe_handler
def pick_samples(setup_state: dict, n: int, recipes_state: list[Recipe]):
    if not setup_state or "results_path" not in setup_state:
        errors.error("Pick a results file first.")
    results_path = setup_state["results_path"]
    recipe = _find_recipe_for(results_path, recipes_state)
    if recipe is None:
        errors.error("Recipe not found for this results file.")

    df = pd.read_csv(results_path)
    if len(df) == 0:
        errors.error("Results file is empty.")
    n = max(1, min(int(n), len(df)))
    indices = random.sample(range(len(df)), n)

    state = {
        "results_path": results_path,
        "recipe_name": recipe.name,
        "row_indices": indices,
        "current": 0,
        "labels": {},
        "comments": {},
        "session_id": str(uuid.uuid4())[:8],
    }
    return [state, *_build_per_sample_view(state, recipe, df)]


def submit_sample(state: dict, comment: str, recipes_state: list[Recipe],
                   *widget_values):
    """Record corrections for current sample, advance, persist if last.

    widget_values is positional:
      first MAX_FIELDS = enum radios, next MAX_FIELDS = list checkboxes,
      next MAX_FIELDS = number inputs, next MAX_FIELDS = text radios.
    """
    if not state or "results_path" not in state:
        errors.error("Pick samples first.")
    recipe = _find_recipe_for(state["results_path"], recipes_state)
    if recipe is None:
        errors.error("Recipe not found.")

    fields = _eval_fields(recipe)
    df = pd.read_csv(state["results_path"])
    current = state["current"]
    if current >= len(state["row_indices"]):
        return [state, *_build_per_sample_view(state, recipe, df)]
    row_idx = state["row_indices"][current]

    # Decode widget values per field
    enum_vals = list(widget_values[:MAX_FIELDS])
    list_vals = list(widget_values[MAX_FIELDS:2*MAX_FIELDS])
    number_vals = list(widget_values[2*MAX_FIELDS:3*MAX_FIELDS])
    text_vals = list(widget_values[3*MAX_FIELDS:4*MAX_FIELDS])

    labels: dict[str, Any] = {}
    for i, f in enumerate(fields):
        if f.type == "enum":
            labels[f.name] = _decode_widget_response(f, enum_vals[i])
        elif f.type == "list":
            labels[f.name] = _decode_widget_response(f, list_vals[i])
        elif f.type == "number":
            labels[f.name] = _decode_widget_response(f, number_vals[i])
        else:
            labels[f.name] = _decode_widget_response(f, text_vals[i])
    state["labels"][row_idx] = labels
    state["comments"][row_idx] = comment or ""
    state["current"] = current + 1

    # Persist on last sample
    if state["current"] >= len(state["row_indices"]):
        _persist_labels(state, recipe, fields)

    return [state, *_build_per_sample_view(state, recipe, df)]


def _persist_labels(state: dict, recipe: Recipe, fields: list[FieldSpec]) -> None:
    path = Path(state["results_path"])
    df = pd.read_csv(path)
    sid = state["session_id"]

    for f in fields:
        if f.type in ("enum", "list", "number"):
            col = f"human_{f.name}"
        else:
            col = f"human_{f.name}_judgment"
        if col not in df.columns:
            df[col] = pd.NA
    if "quick_eval_comment" not in df.columns:
        df["quick_eval_comment"] = pd.NA
    if "quick_eval_session_id" not in df.columns:
        df["quick_eval_session_id"] = pd.NA

    for row_idx, label_dict in state["labels"].items():
        for f in fields:
            if f.type in ("enum", "list", "number"):
                col = f"human_{f.name}"
            else:
                col = f"human_{f.name}_judgment"
            v = label_dict.get(f.name)
            if isinstance(v, list):
                v = str(v)
            df.at[row_idx, col] = v
        df.at[row_idx, "quick_eval_comment"] = state["comments"].get(row_idx, "")
        df.at[row_idx, "quick_eval_session_id"] = sid

    df.to_csv(path, index=False)
    log.info("quick eval: saved %d labels to %s (session %s)",
             len(state["labels"]), path, sid)


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def build_quick_eval_tab(*, recipes_state: gr.State):
    gr.Markdown(
        "### Quick Eval\n"
        "Pick a results CSV. The form mirrors the recipe's output schema — "
        "enum fields show radios with the same choices, text fields show ✓/⚠/✗ "
        "with source text. Labels save back into the results CSV.\n"
        "_(LLM judge lives in **Advanced**.)_"
    )

    setup_state = gr.State(value={})
    eval_state = gr.State(value={})

    with gr.Row():
        results_dd = gr.Dropdown(
            label="Results file", choices=_list_results_choices(), scale=4,
        )
        refresh_btn = gr.Button("🔄", scale=1, size="sm")
        sample_n = gr.Number(label="Sample N", value=5, precision=0, scale=1)
        pick_btn = gr.Button("Pick samples", variant="primary", scale=2)

    setup_summary = gr.Markdown()
    refresh_btn.click(refresh_results_list, None, results_dd)
    results_dd.change(on_pick_results, [results_dd, recipes_state],
                       [setup_state, setup_summary])

    progress_md = gr.Markdown()
    source_md = gr.Markdown()
    summary_pane = gr.Markdown(visible=False)

    # Pre-declare the widget pool. MAX_FIELDS of each kind.
    enum_radios = [gr.Radio(visible=False, label=f"enum_{i}") for i in range(MAX_FIELDS)]
    list_checks = [gr.CheckboxGroup(visible=False, label=f"list_{i}") for i in range(MAX_FIELDS)]
    number_ins = [gr.Number(visible=False, label=f"num_{i}") for i in range(MAX_FIELDS)]
    text_radios = [gr.Radio(visible=False, label=f"text_{i}") for i in range(MAX_FIELDS)]

    comment_in = gr.Textbox(label="Comment (optional)", lines=2)
    submit_btn = gr.Button("Submit and continue →", variant="primary", visible=False)

    out_components = [
        eval_state,
        progress_md, source_md, summary_pane, submit_btn, comment_in,
        *enum_radios, *list_checks, *number_ins, *text_radios,
    ]

    pick_btn.click(
        pick_samples,
        [setup_state, sample_n, recipes_state],
        out_components,
    )
    submit_btn.click(
        submit_sample,
        [eval_state, comment_in, recipes_state,
         *enum_radios, *list_checks, *number_ins, *text_radios],
        out_components,
    )
