"""Gradio UI — Qual LLM Studio v5.

Tabs (whimsical names, permanent):
  1. Tools & Ingredients — output language + data import/processing
  2. Recipe              — model chat + recipe maker
  3. Cooking             — pick dataset + recipe, click run
  4. Taste Test          — 5-sample alignment check
  5. Cookbook            — full eval, devset/testset, agreement, optimizer

Settings panel: toggled by the ⚙ button in the header. Opens by hiding the
title and main content; closes by pressing ⚙ again or Esc.
"""

from __future__ import annotations

import logging
from pathlib import Path

import gradio as gr
import pandas as pd

from . import drive, errors, paths
from .inference import DEFAULT_HOST, OllamaClient
from .llm_state import connection_status
from .recipe import list_recipes
from .runs import list_runs
from .ui_chat_tab import build_chat_tab
from .ui_data_tab import build_data_tab
from .ui_eval_tab import build_eval_tab
from .ui_quick_eval_tab import build_quick_eval_tab
from .ui_runs_tab import build_runs_tab
from .workspace import SourceMeta, Workspace

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# JS injected at page load
# ---------------------------------------------------------------------------

_CSS = """
#settings-toggle-btn {
    position: fixed !important;
    top: 1rem !important;
    right: 1rem !important;
    z-index: 10000 !important;
}
"""

# Esc key closes settings by clicking the toggle button — but only when the
# settings panel is currently visible (checked via offsetParent).
_INIT_JS = """
() => {
    document.addEventListener('keydown', (e) => {
        if (e.key !== 'Escape') return;
        const panel = document.getElementById('settings-panel');
        if (!panel || panel.offsetParent === null) return;
        const btn = document.querySelector('#settings-toggle-btn button')
                 || document.getElementById('settings-toggle-btn');
        if (btn) btn.click();
    });
}
"""

_LANGUAGES = [
    "English", "Finnish", "Swedish", "Norwegian", "Danish",
    "German", "French", "Spanish", "Portuguese", "Italian",
    "Dutch", "Polish", "Russian", "Chinese (Simplified)", "Japanese",
]

# ---------------------------------------------------------------------------
# Shared handlers
# ---------------------------------------------------------------------------


def _persist_workspace(
    *, host=None, model=None, sources_state=None, docs_df=None,
    chat_history=None, recipe_tests=None,
):
    ws = Workspace(paths.WORKSPACE_DIR).load()
    if host is not None: ws.state.host = host
    if model is not None: ws.state.model = model
    if sources_state is not None:
        ws.state.sources_meta = [
            SourceMeta(
                name=s.name,
                kind="tabular" if s.is_tabular else "documents",
                n_rows=len(s.dataframe) if s.is_tabular else len(s.documents or []),
                columns=list(s.dataframe.columns) if s.is_tabular else [],
            )
            for s in sources_state
        ]
    if docs_df is not None: ws.docs_df = docs_df
    if chat_history is not None: ws.chat_history = chat_history
    if recipe_tests is not None: ws.recipe_tests = recipe_tests
    ws.save()


@errors.safe_handler
def handle_check_ollama(host: str):
    if not host: host = DEFAULT_HOST
    client = OllamaClient(host=host)
    if not client.is_alive(timeout=2.0):
        return f"❌ Ollama unreachable at `{host}`.", gr.update(choices=[])
    version = client.version()
    try:
        models = client.list_models()
    except Exception as exc:  # noqa: BLE001
        return f"⚠️ Connected, but list_models failed: {exc}", gr.update(choices=[])
    version_msg = f" (v{version})" if version else ""
    if not models:
        return (
            f"⚠️ Connected to Ollama{version_msg} but **no models pulled**.",
            gr.update(choices=[]),
        )
    return (
        f"✓ Connected{version_msg}. **{len(models)}** model(s) available.",
        gr.update(choices=models, value=models[0]),
    )


@errors.safe_handler
def handle_pull_model(host: str, model: str, progress=gr.Progress()):
    if not model or not model.strip():
        errors.error("Enter a model name.")
    if not host: host = DEFAULT_HOST
    client = OllamaClient(host=host)
    if not client.is_alive(timeout=2.0):
        errors.error(f"Ollama unreachable at {host}.")

    final_event: dict = {}
    try:
        for event in client.pull_and_verify(model):
            if event.get("_qls_status") in ("ok", "failed"):
                final_event = event
                continue
            status = event.get("status", "")
            if "completed" in event and "total" in event and event["total"]:
                frac = event["completed"] / event["total"]
                progress(frac, desc=f"{status}: {event['completed']:,}/{event['total']:,}")
    except Exception as exc:  # noqa: BLE001
        errors.error(f"Pull request failed: {exc}")

    if final_event.get("_qls_status") == "ok":
        models = client.list_models()
        return (
            f"✓ Pulled **{model}** successfully.",
            gr.update(choices=models, value=model),
        )
    err_msg = final_event.get("error", "unknown reason")
    errors.error(f"Pull failed for `{model}`: {err_msg}")


@errors.safe_handler
def handle_refresh_runs():
    runs = list_runs(paths.RUNS_DIR)
    runs = [r for r in runs if not Path(r["folder"]).parent.name.startswith("_")]
    if not runs:
        return pd.DataFrame(columns=["pipeline", "name", "rows", "model", "folder"])
    rows = []
    for r in runs:
        cfg = r.get("config") or {}
        rows.append({
            "pipeline": r["pipeline"], "name": r["name"],
            "rows": cfg.get("n_output_rows", "?"),
            "model": cfg.get("model", "?"),
            "folder": r["folder"],
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# UI builder
# ---------------------------------------------------------------------------


def build_ui() -> gr.Blocks:
    paths.ensure_dirs()
    errors.configure(paths.RUNS_DIR)

    ws = Workspace(paths.WORKSPACE_DIR).load()
    initial_recipes = list_recipes(paths.RECIPES_DIR)

    with gr.Blocks(title="Qual LLM Studio", css=_CSS, js=_INIT_JS) as ui:

        # ---- Persistent state ----
        docs_state         = gr.State(value=ws.docs_df if ws.docs_df is not None else None)
        sources_state      = gr.State(value=[])
        recipes_state      = gr.State(value=initial_recipes)
        chat_history_state = gr.State(value=ws.chat_history)
        recipe_tests_state = gr.State(value=ws.recipe_tests)
        inference_state    = gr.State(value={
            "client": OllamaClient(host=ws.state.host) if ws.state.host else None,
            "model":  ws.state.model,
            "host":   ws.state.host or DEFAULT_HOST,
            "output_language": "English",
        })
        settings_open_state = gr.State(value=False)

        # ---- Header (always visible) ----
        with gr.Row(equal_height=True):
            title_md = gr.Markdown("# Qual LLM Studio")
            settings_btn = gr.Button(
                "⚙", size="sm", scale=0, min_width=48,
                elem_id="settings-toggle-btn",
            )

        initial_msg, _ = connection_status({"host": ws.state.host, "model": ws.state.model})
        banner = gr.Markdown(initial_msg or "✓ Ready")

        # ---- Settings panel ----
        # Declared before the tabs so settings_host / settings_model exist
        # as component references when build_chat_tab / build_runs_tab are called.
        with gr.Group(visible=False, elem_id="settings-panel") as settings_group:
            gr.Markdown("## Settings")

            gr.Markdown("### LLM Provider")
            provider_radio = gr.Radio(
                choices=[
                    "Ollama (Local)",
                    "OpenAI (coming soon)",
                    "Anthropic (coming soon)",
                ],
                value="Ollama (Local)",
                label=None,
            )

            with gr.Row():
                settings_host = gr.Textbox(
                    value=ws.state.host or DEFAULT_HOST,
                    label="Ollama host",
                    scale=3,
                )
                check_btn = gr.Button("Check connection", scale=1)
            host_status_md = gr.Markdown()

            settings_model = gr.Dropdown(
                label="Model",
                choices=[ws.state.model] if ws.state.model else [],
                value=ws.state.model or None,
                interactive=True,
                allow_custom_value=True,
            )

            api_key_in = gr.Textbox(
                label="API Key (coming soon — required for cloud providers)",
                placeholder="sk-...",
                type="password",
                interactive=False,
            )

            with gr.Accordion("Per-task model overrides", open=False):
                gr.Markdown(
                    "Optional: use different models for the LLM judge or DSPy optimizer. "
                    "Leave blank to use the default model."
                )
                with gr.Row():
                    judge_model_dd = gr.Dropdown(
                        label="LLM judge model", choices=[], allow_custom_value=True,
                    )
                    optimizer_model_dd = gr.Dropdown(
                        label="DSPy optimizer model", choices=[], allow_custom_value=True,
                    )

            with gr.Accordion("Compute resources", open=False):
                gr.Markdown(
                    "Detected automatically; used by the Runs tab to suggest a parallelism level. "
                    "On CPU-only: small concurrency. On 1 GPU: ~12. On N GPUs: ~12*N capped at 64. "
                    "You can always override per-run on the Runs tab."
                )
                resources_md = gr.Markdown("_Click 'Detect' to scan available resources._")
                detect_resources_btn = gr.Button("Detect", size="sm")

            with gr.Accordion("Pull a model from Ollama Hub", open=False):
                with gr.Row():
                    pull_in = gr.Textbox(
                        label="Model name",
                        placeholder="e.g. qwen3:4b, gemma3:4b",
                        scale=3,
                    )
                    pull_btn = gr.Button("Pull", scale=1)
                pull_status_md = gr.Markdown()

        # ---- Main content ----
        def _tab_header(cooking: str, data: str):
            gr.HTML(f"""
            <div style="padding: 1.25rem 0 1rem; border-bottom: 2px solid var(--border-color-primary, #e5e7eb); margin-bottom: 1rem;">
              <h1 style="margin: 0; font-size: 2rem; font-weight: 800; line-height: 1.1; color: var(--body-text-color);">
                {cooking}
                <span style="font-weight: 400; font-size: 1.25rem; color: var(--body-text-color-subdued, #6b7280);">
                  &nbsp;/&nbsp;{data}
                </span>
              </h1>
            </div>
            """)

        with gr.Group(visible=True) as main_group:

            with gr.Tab("1. Ingredients"):
                _tab_header("Ingredients", "Data")
                gr.Markdown("### Output")
                language_dd = gr.Dropdown(
                    label="Default output language",
                    choices=_LANGUAGES,
                    value="English",
                    allow_custom_value=True,
                )
                gr.Markdown("---")
                build_data_tab(
                    docs_state=docs_state,
                    sources_state=sources_state,
                    inference_state=inference_state,
                )

            with gr.Tab("2. Recipe"):
                _tab_header("Recipe Builder", "Prompt Studio")
                build_chat_tab(
                    recipes_state=recipes_state,
                    chat_history_state=chat_history_state,
                    recipe_tests_state=recipe_tests_state,
                    docs_state=docs_state,
                    settings_host=settings_host,
                    settings_model=settings_model,
                )

            with gr.Tab("3. Cooking"):
                _tab_header("Cooking", "Runs")
                build_runs_tab(
                    recipes_state=recipes_state,
                    docs_state=docs_state,
                    settings_host=settings_host,
                    settings_model=settings_model,
                )

            with gr.Tab("4. Tasting"):
                _tab_header("Tasting", "Evaluation")
                build_quick_eval_tab(recipes_state=recipes_state)

            with gr.Tab("5. Journal"):
                _tab_header("Journal", "Run History")
                with gr.Tab("Past runs"):
                    gr.Markdown("All runs (`runs/<pipeline>/<name>/`).")
                    refresh_btn = gr.Button("Refresh runs list")
                    runs_table = gr.Dataframe(label="Past runs", wrap=True)
                    refresh_btn.click(handle_refresh_runs, None, runs_table)

                with gr.Tab("Full eval (devset/testset)"):
                    build_eval_tab(
                        inference_state=inference_state,
                        runs_root=str(paths.RUNS_DIR),
                        recipes_root=str(paths.RECIPES_DIR),
                    )

        # ---- Wiring: settings toggle ----

        def _toggle_settings(is_open):
            new_open = not is_open
            return (
                new_open,
                gr.update(visible=new_open),       # settings_group
                gr.update(visible=not new_open),   # main_group
                gr.update(visible=not new_open),   # title_md
                gr.update(visible=not new_open),   # banner
            )

        settings_btn.click(
            _toggle_settings, settings_open_state,
            [settings_open_state, settings_group, main_group, title_md, banner],
        )

        # ---- Wiring: provider lock ----

        def _lock_provider(choice):
            if choice != "Ollama (Local)":
                return gr.update(value="Ollama (Local)")
            return gr.update()

        provider_radio.change(_lock_provider, provider_radio, provider_radio)

        # ---- Wiring: connection & models ----

        check_btn.click(
            handle_check_ollama, settings_host, [host_status_md, settings_model],
        )
        pull_btn.click(
            handle_pull_model, [settings_host, pull_in], [pull_status_md, settings_model],
        )

        def _populate_overrides(host: str, default_model: str):
            client = OllamaClient(host=host or DEFAULT_HOST)
            if not client.is_alive(timeout=2.0):
                return gr.update(choices=[]), gr.update(choices=[])
            try:
                models = client.list_models()
            except Exception:  # noqa: BLE001
                return gr.update(choices=[]), gr.update(choices=[])
            return gr.update(choices=models), gr.update(choices=models)

        check_btn.click(
            _populate_overrides, [settings_host, settings_model],
            [judge_model_dd, optimizer_model_dd],
        )

        def _update_inference_state(host, model, judge_model, optimizer_model, language):
            client = OllamaClient(host=host or DEFAULT_HOST)
            state = {
                "client": client,
                "model": model or "",
                "host": host or DEFAULT_HOST,
                "judge_model": judge_model or "",
                "optimizer_model": optimizer_model or "",
                "output_language": language or "English",
            }
            _persist_workspace(host=state["host"], model=state["model"])
            return state

        for trigger in (settings_host, settings_model, judge_model_dd, optimizer_model_dd,
                        language_dd):
            trigger.change(
                _update_inference_state,
                [settings_host, settings_model, judge_model_dd, optimizer_model_dd, language_dd],
                inference_state,
            )

        def _do_detect_resources(host: str):
            from . import resources as _res
            r = _res.detect(ollama_host=host or DEFAULT_HOST)
            backend_label = {
                "cpu": "CPU only",
                "nvidia": f"{r.n_gpus} NVIDIA GPU(s)",
                "amd": f"{r.n_gpus} AMD GPU(s)",
                "unknown_gpu": "GPU detected (count unknown)",
            }.get(r.backend, r.backend)
            return (
                f"**Detected:** {backend_label} · {r.cpu_count} CPU cores  \n"
                f"**Suggested parallelism:** {r.suggested_parallelism}  \n"
                f"_{r.detection_notes}_"
            )

        detect_resources_btn.click(_do_detect_resources, settings_host, resources_md)

        # ---- Wiring: banner ----

        def _refresh_banner(state):
            msg, _ = connection_status(state)
            return msg or "✓ Ready"

        inference_state.change(_refresh_banner, inference_state, banner)

        # ---- Wiring: persistence ----

        docs_state.change(
            lambda df: (_persist_workspace(docs_df=df), df)[1], docs_state, docs_state,
        )
        chat_history_state.change(
            lambda h: (_persist_workspace(chat_history=h), h)[1],
            chat_history_state, chat_history_state,
        )
        recipe_tests_state.change(
            lambda t: (_persist_workspace(recipe_tests=t), t)[1],
            recipe_tests_state, recipe_tests_state,
        )

    return ui


def main(host: str = "0.0.0.0", port: int = 7860, share: bool = False):
    ui = build_ui()
    ui.queue().launch(
        server_name=host, server_port=port, share=share,
        theme=gr.themes.Soft(),
    )


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=7860)
    parser.add_argument("--share", action="store_true")
    args = parser.parse_args()
    main(host=args.host, port=args.port, share=args.share)
