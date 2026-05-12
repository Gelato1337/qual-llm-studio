"""Chat tab — model chat + recipe authoring.

Two modes in one place:

  Free chat:
    Talk to the model. Streaming. History persists.

  Recipe maker:
    Pick or create a recipe. See it as readable text (NOT JSON).
    Pick a sample row from a dataset. The prompt renders with the row.
    Hit Test. See the actual rendered prompt and the model's response.
    Edit the recipe text and hit Test again to compare prompt A vs B.
    Save back to recipes/.

The recipe-maker view shows:
  * Recipe text (editable Markdown-ish — Recipe.render_as_text format)
  * Save / Save as new buttons
  * Sample row picker (from any saved dataset)
  * Rendered prompt preview (read-only)
  * Test button + response display + parsed JSON display
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import gradio as gr
import pandas as pd

from . import errors, paths
from .inference import OllamaClient
from .json_parse import parse_json
from .prompting import render_prompt, find_column_placeholders
from .recipe import Recipe, list_recipes

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Free chat
# ---------------------------------------------------------------------------


def _free_chat_send(user_msg: str, history: list[dict], host: str, model: str):
    if not user_msg or not user_msg.strip():
        return history, ""

    if not (model or "").strip():
        history.append({"role": "user", "content": user_msg})
        history.append({"role": "assistant", "content": "[Set a model in Settings first.]"})
        yield history, ""
        return

    client = OllamaClient(host=host)
    history.append({"role": "user", "content": user_msg})
    history.append({"role": "assistant", "content": ""})
    yield history, ""

    messages = [{"role": h["role"], "content": h["content"]} for h in history[:-1]]
    accumulated = ""
    try:
        for chunk in client.chat_stream(messages=messages, model=model):
            accumulated += chunk
            history[-1]["content"] = accumulated
            yield history, ""
    except Exception as exc:  # noqa: BLE001
        history[-1]["content"] = f"[ERROR] {exc}"
        yield history, ""


@errors.safe_handler
def _free_chat_clear():
    return [], ""


# ---------------------------------------------------------------------------
# Recipe maker
# ---------------------------------------------------------------------------


@errors.safe_handler
def _list_recipe_choices(recipes: list[Recipe]):
    """Build dropdown choices for the recipe picker."""
    return [(r.name, r.name) for r in recipes]


def _find_recipe(recipes: list[Recipe], name: str) -> Recipe | None:
    for r in recipes:
        if r.name == name:
            return r
    return None


@errors.safe_handler
def _on_pick_recipe(recipe_name: str, recipes: list[Recipe]):
    """Render the picked recipe as text for editing."""
    if not recipe_name:
        return "", "Pick a recipe."
    r = _find_recipe(recipes, recipe_name)
    if r is None:
        return "", f"Recipe {recipe_name!r} not found."
    return r.render_as_text(), ""


CURRENT_DATASET_SENTINEL = "__current_in_memory__"


def _list_dataset_choices_for_chat(docs_df=None) -> list[tuple[str, str]]:
    """Build dataset choices for the Chat tab dropdown.

    Includes:
      1. The currently-loaded in-memory dataset (if any) as the first option,
         tagged with row count so researchers can see what's there
      2. All saved datasets from `datasets/`

    The in-memory option uses a sentinel value; handlers must check for it
    and read from docs_state instead of pd.read_csv.
    """
    choices: list[tuple[str, str]] = []
    if docs_df is not None and len(docs_df) > 0:
        choices.append(
            (f"⚡ Current (unsaved, {len(docs_df):,} rows from Data tab)",
             CURRENT_DATASET_SENTINEL),
        )
    for p in paths.list_datasets():
        choices.append((p.stem, str(p)))
    return choices


def _load_dataset_for_chat(dataset_path: str, docs_df=None) -> pd.DataFrame | None:
    """Resolve a dataset_path value to an actual DataFrame.

    Handles the in-memory sentinel ("__current_in_memory__") by returning
    docs_df directly; otherwise reads from disk.
    """
    if not dataset_path:
        return None
    if dataset_path == CURRENT_DATASET_SENTINEL:
        if docs_df is None or len(docs_df) == 0:
            return None
        return docs_df
    try:
        return pd.read_csv(dataset_path)
    except Exception:  # noqa: BLE001
        return None


@errors.safe_handler
def _refresh_datasets_for_chat(docs_df):
    return gr.update(choices=_list_dataset_choices_for_chat(docs_df))


@errors.safe_handler
def _on_pick_dataset(dataset_path: str, docs_df):
    """Load a few preview rows so the user can pick which to test against."""
    if not dataset_path:
        return gr.update(choices=[], value=None), ""

    df = _load_dataset_for_chat(dataset_path, docs_df)
    if df is None:
        if dataset_path == CURRENT_DATASET_SENTINEL:
            return gr.update(choices=[]), "No data loaded yet — go to **Data** tab and click Apply first."
        return gr.update(choices=[]), f"Failed to read dataset."
    if len(df) == 0:
        return gr.update(choices=[]), "Dataset is empty."

    # Show first 30 rows in the picker
    head = df.head(30)
    choices = []
    for i, row in head.iterrows():
        rid = str(row.get("id", row.get("doc_id", i)))
        # Pick a textish column for the label
        text_col = "text" if "text" in df.columns else df.columns[0]
        sample = str(row.get(text_col, ""))[:80].replace("\n", " ")
        choices.append((f"{i}: {rid} — {sample}", str(i)))

    source = "current in-memory" if dataset_path == CURRENT_DATASET_SENTINEL else "disk"
    summary = (
        f"**{len(df)}** rows ({source}) · "
        f"columns: {', '.join('`' + c + '`' for c in df.columns[:8])}"
        + (f" + {len(df.columns) - 8} more" if len(df.columns) > 8 else "")
    )
    return gr.update(choices=choices, value=choices[0][1] if choices else None), summary


@errors.safe_handler
def _render_preview(recipe_text: str, dataset_path: str, row_idx_str: str,
                    base_recipe_name: str, recipes: list[Recipe], docs_df=None):
    """Render the recipe's prompt against the picked dataset row.

    Returns the rendered prompt plus a list of any unresolved placeholders.
    Resolves dataset_path either from disk or from the in-memory docs_state
    (when sentinel is used).
    """
    if not recipe_text or not recipe_text.strip():
        return "", "_No recipe loaded._"
    if not dataset_path or not row_idx_str:
        return recipe_text, "_Pick a dataset and a row to render the prompt._"

    base = _find_recipe(recipes, base_recipe_name) if base_recipe_name else None
    try:
        recipe = Recipe.parse_from_text(recipe_text, base=base)
    except Exception as exc:  # noqa: BLE001
        return "", f"Recipe text didn't parse: {exc}"

    df = _load_dataset_for_chat(dataset_path, docs_df)
    if df is None:
        return "", "Dataset not available — pick another or click Apply on Data tab."
    try:
        idx = int(row_idx_str)
    except ValueError:
        return "", "Bad row index."
    if idx < 0 or idx >= len(df):
        return "", "Row index out of range."
    row = df.iloc[idx]

    rendered = render_prompt(
        recipe.prompt, row,
        target_col=recipe.inputs.target,
        compare_col=recipe.inputs.compare,
        extra=recipe.inputs.extra,
    )
    needed_cols = find_column_placeholders(recipe.prompt)
    missing = [c for c in needed_cols if c not in df.columns]
    info = (
        f"**Target column:** `{recipe.inputs.target}` "
        f"(in dataset: {'yes' if recipe.inputs.target in df.columns else 'NO'})"
    )
    if needed_cols:
        info += f"  ·  {{column}} placeholders: {needed_cols}"
    if missing:
        info += f"\n\n⚠️ **Missing columns in dataset:** {missing}"
    return rendered, info


def _test_send(rendered_prompt: str, recipe_text: str, dataset_path: str,
                row_idx_str: str, host: str, model: str,
                tests: list[dict], recipes: list[Recipe], base_recipe_name: str):
    """Stream a one-shot model call against the rendered prompt.

    Each test is an INDEPENDENT call (no conversation history). Last 3
    tests are kept side-by-side so the user can compare prompt A vs B."""
    if not (model or "").strip():
        tests.append({
            "prompt": rendered_prompt,
            "recipe_name": base_recipe_name,
            "response": "[Set a model in Settings first.]",
        })
        yield tests, _format_tests(tests)
        return

    if not rendered_prompt or not rendered_prompt.strip():
        # Try to render fresh from the recipe + sample row
        if not dataset_path or not row_idx_str:
            tests.append({
                "prompt": "",
                "recipe_name": base_recipe_name,
                "response": "[Pick a dataset and a row first.]",
            })
            yield tests, _format_tests(tests)
            return

    client = OllamaClient(host=host)
    if not client.is_alive(timeout=2.0):
        tests.append({
            "prompt": rendered_prompt,
            "recipe_name": base_recipe_name,
            "response": f"[Ollama unreachable at {host}.]",
        })
        yield tests, _format_tests(tests)
        return

    test_entry = {
        "prompt": rendered_prompt,
        "recipe_name": base_recipe_name,
        "response": "",
    }
    tests.append(test_entry)

    accumulated = ""
    try:
        for chunk in client.chat_stream(prompt=rendered_prompt, model=model):
            accumulated += chunk
            test_entry["response"] = accumulated
            yield tests, _format_tests(tests)
    except Exception as exc:  # noqa: BLE001
        test_entry["response"] = f"[ERROR] {exc}"
        yield tests, _format_tests(tests)
        return

    parsed = parse_json(accumulated)
    test_entry["parsed"] = parsed.data if parsed.ok else None
    test_entry["parse_method"] = parsed.method
    yield tests, _format_tests(tests)


def _format_tests(tests: list[dict]) -> str:
    if not tests:
        return "_No tests run yet._"
    parts = []
    for i, t in enumerate(reversed(tests[-3:])):
        n = len(tests) - i
        parts.append(f"### Test {n} · `{t.get('recipe_name', '?')}`")
        parts.append("**Response:**")
        parts.append(f"```\n{(t.get('response') or '')[:1500]}\n```")
        if t.get("parsed") is not None:
            parts.append(f"**Parsed JSON** ({t.get('parse_method', '?')}):")
            parts.append(f"```json\n{json.dumps(t['parsed'], indent=2)[:800]}\n```")
        parts.append("---")
    return "\n\n".join(parts)


@errors.safe_handler
def _save_recipe(recipe_text: str, save_name: str, recipes: list[Recipe],
                  base_recipe_name: str):
    """Save the recipe text as a JSON file. Returns updated recipes list
    and a status message."""
    if not save_name or not save_name.strip():
        errors.error("Type a name for the new recipe.")
    safe = paths.safe_filename(save_name.strip())

    base = _find_recipe(recipes, base_recipe_name) if base_recipe_name else None
    try:
        recipe = Recipe.parse_from_text(recipe_text, base=base)
        recipe.name = safe  # use the requested name, override what's in the text
    except Exception as exc:  # noqa: BLE001
        errors.error(f"Recipe text didn't parse: {exc}")

    issues = recipe.validate()
    if issues:
        errors.error("Recipe invalid:\n - " + "\n - ".join(issues))

    target = paths.RECIPES_DIR / "custom" / f"{safe}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        errors.error(
            f"A recipe at `{target.relative_to(paths.RECIPES_DIR)}` already exists. "
            "Pick a different name or delete that file."
        )
    target.write_text(json.dumps(recipe.to_dict(), indent=2, ensure_ascii=False))

    # Reload all recipes so dropdowns see the new one
    new_recipes = list_recipes(paths.RECIPES_DIR)
    return (
        new_recipes,
        f"✓ Saved to `{target.relative_to(paths.REPO_ROOT)}`",
        gr.update(choices=_list_recipe_choices(new_recipes), value=safe),
    )


@errors.safe_handler
def _refresh_recipes():
    recipes = list_recipes(paths.RECIPES_DIR)
    return recipes, gr.update(choices=_list_recipe_choices(recipes))


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def build_chat_tab(
    *,
    recipes_state: gr.State,
    chat_history_state: gr.State,
    recipe_tests_state: gr.State,
    docs_state: gr.State,
    settings_host: gr.Textbox,
    settings_model: gr.Dropdown,
):
    gr.Markdown(
        "### Chat / Recipe Maker\n"
        "Free chat with the model, OR pick a recipe and test it on a sample row."
    )

    mode_radio = gr.Radio(
        choices=["Free chat", "Recipe maker"],
        value="Free chat",
        label="Mode",
    )

    # ---- Free chat ----
    with gr.Group(visible=True) as free_group:
        free_chatbot = gr.Chatbot(label="Chat", height=420)
        with gr.Row():
            free_input = gr.Textbox(
                placeholder="Talk to the model…", lines=2, scale=4, container=False,
            )
            free_send_btn = gr.Button("Send", scale=1, variant="primary")
            free_clear_btn = gr.Button("Clear", scale=1, size="sm")

    # ---- Recipe maker ----
    with gr.Group(visible=False) as maker_group:
        with gr.Row():
            recipe_dd = gr.Dropdown(
                label="Pick recipe",
                choices=[],
                scale=3,
                allow_custom_value=False,
            )
            reload_recipes_btn = gr.Button("🔄 Reload recipes", scale=1, size="sm")
        recipe_status = gr.Markdown()

        with gr.Row():
            with gr.Column(scale=3):
                gr.Markdown("#### Recipe (editable)")
                recipe_text = gr.Code(
                    label="Recipe view",
                    language="markdown",
                    lines=18,
                )
                with gr.Row():
                    save_name_in = gr.Textbox(label="Save as (filename without .json)", scale=3)
                    save_btn = gr.Button("Save", variant="secondary", scale=1)
                save_status = gr.Markdown()

            with gr.Column(scale=3):
                gr.Markdown("#### Test on a sample row")
                with gr.Row():
                    dataset_dd = gr.Dropdown(
                        label="Dataset",
                        choices=_list_dataset_choices_for_chat(docs_state.value),
                        scale=2,
                    )
                    sample_dd = gr.Dropdown(label="Row", choices=[], scale=2)
                    refresh_datasets_btn = gr.Button(
                        "🔄", scale=1, size="sm",
                        elem_id="refresh_chat_datasets",
                    )
                dataset_status = gr.Markdown(
                    "_Tip: ⚡ Current is whatever you last clicked Apply on in the Data tab "
                    "— no need to save first._"
                )

                gr.Markdown("##### Rendered prompt (this is what's sent to the model)")
                rendered_prompt = gr.Textbox(label="Rendered prompt", lines=8)
                rendered_info = gr.Markdown()
                test_btn = gr.Button("Test", variant="primary")

        gr.Markdown("#### Test results")
        test_results_md = gr.Markdown()

    def _switch_mode(mode):
        return (
            gr.update(visible=mode == "Free chat"),
            gr.update(visible=mode == "Recipe maker"),
        )
    mode_radio.change(_switch_mode, mode_radio, [free_group, maker_group])

    # Free chat handlers
    free_send_btn.click(
        _free_chat_send,
        [free_input, chat_history_state, settings_host, settings_model],
        [free_chatbot, free_input],
    )
    free_input.submit(
        _free_chat_send,
        [free_input, chat_history_state, settings_host, settings_model],
        [free_chatbot, free_input],
    )
    free_clear_btn.click(_free_chat_clear, None, [free_chatbot, free_input])

    # Recipe maker handlers
    recipe_dd.change(_on_pick_recipe, [recipe_dd, recipes_state], [recipe_text, recipe_status])
    dataset_dd.change(_on_pick_dataset, [dataset_dd, docs_state], [sample_dd, dataset_status])

    # Refresh datasets dropdown — combines on-disk + in-memory
    refresh_datasets_btn.click(
        _refresh_datasets_for_chat, docs_state, dataset_dd,
    )

    # Auto-refresh dropdown choices whenever docs_state changes
    # (e.g. researcher just clicked Apply on Data tab)
    docs_state.change(_refresh_datasets_for_chat, docs_state, dataset_dd)

    # Re-render preview when any input changes
    def _do_render(recipe_text, dataset_path, row_idx, recipe_name, recipes, docs_df):
        return _render_preview(recipe_text, dataset_path, row_idx, recipe_name, recipes, docs_df)
    for trigger in (recipe_text, dataset_dd, sample_dd, recipe_dd):
        trigger.change(
            _do_render,
            [recipe_text, dataset_dd, sample_dd, recipe_dd, recipes_state, docs_state],
            [rendered_prompt, rendered_info],
        )

    test_btn.click(
        _test_send,
        [rendered_prompt, recipe_text, dataset_dd, sample_dd,
         settings_host, settings_model, recipe_tests_state, recipes_state, recipe_dd],
        [recipe_tests_state, test_results_md],
    )

    save_btn.click(
        _save_recipe, [recipe_text, save_name_in, recipes_state, recipe_dd],
        [recipes_state, save_status, recipe_dd],
    )

    reload_recipes_btn.click(_refresh_recipes, None, [recipes_state, recipe_dd])

    # Initial population
    if recipes_state.value:
        recipe_dd.choices = _list_recipe_choices(recipes_state.value)

    # Re-render chatbot from persisted history on state change
    def _hydrate_chat(history):
        return history or []
    chat_history_state.change(_hydrate_chat, chat_history_state, free_chatbot)
