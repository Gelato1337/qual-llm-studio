"""Gradio UI — Qual LLM Studio v5.

Tab order:
  1. Data        — load + structure + segment + save dataset
  2. Chat        — model chat + recipe maker
  3. Runs        — pick dataset + recipe, click run
  4. Quick Eval  — 5-sample alignment check
  5. Advanced    — full eval, devset/testset, agreement, optimizer
  6. Settings    — Ollama host + models

Wiring strategy: the Settings dropdowns are built FIRST (before any tab),
inside a hidden state container. They get "rendered into the Settings
tab" via component re-rendering at the end. Other tabs receive these
components as inputs to their handlers — when the user clicks Run, the
LIVE value of the Settings dropdown is read, never a stale gr.State.
"""

from __future__ import annotations

import json
import logging
import traceback
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


def build_ui() -> gr.Blocks:
    paths.ensure_dirs()
    errors.configure(paths.RUNS_DIR)

    ws = Workspace(paths.WORKSPACE_DIR).load()
    initial_recipes = list_recipes(paths.RECIPES_DIR)

    with gr.Blocks(title="Qual LLM Studio") as ui:
        # ---- Persistent state ----
        docs_state = gr.State(value=ws.docs_df if ws.docs_df is not None else None)
        sources_state = gr.State(value=[])
        recipes_state = gr.State(value=initial_recipes)
        chat_history_state = gr.State(value=ws.chat_history)
        recipe_tests_state = gr.State(value=ws.recipe_tests)
        inference_state = gr.State(value={
            "client": OllamaClient(host=ws.state.host) if ws.state.host else None,
            "model": ws.state.model,
            "host": ws.state.host or DEFAULT_HOST,
        })

        gr.Markdown("# Qual LLM Studio")

        # Banner — recomputed on inference_state changes
        initial_msg, _ = connection_status({"host": ws.state.host, "model": ws.state.model})
        banner = gr.Markdown(initial_msg or "✓ Ready")

        # ---- Tabs in user-visible order ----
        with gr.Tab("1. Data"):
            build_data_tab(
                docs_state=docs_state,
                sources_state=sources_state,
                inference_state=inference_state,
            )

        # We need Chat and Runs tabs to know about the Settings dropdowns
        # for handler wiring. Declare Settings dropdowns now (inside a
        # placeholder Tab that we'll later move components into).

        # Strategy: declare the Settings tab last visually. Inside it,
        # declare settings_host, settings_model, etc. Other tabs use them
        # as INPUTS to handlers — Gradio resolves component refs lazily,
        # so this works.

        with gr.Tab("2. Chat / Recipes") as chat_tab:
            chat_tab_placeholder = gr.State(value=None)
            # Defer build until settings components exist
            pass

        with gr.Tab("3. Runs") as runs_tab:
            runs_tab_placeholder = gr.State(value=None)
            pass

        with gr.Tab("4. Quick Eval"):
            build_quick_eval_tab(recipes_state=recipes_state)

        with gr.Tab("5. Advanced"):
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

        with gr.Tab("6. Settings"):
            gr.Markdown(
                "### Ollama connection\n"
                "Specify where Ollama is reachable. Click **Check** to verify."
            )
            with gr.Row():
                settings_host = gr.Textbox(
                    value=ws.state.host or DEFAULT_HOST,
                    label="Ollama host", scale=3,
                )
                check_btn = gr.Button("Check connection", scale=1)
            host_status_md = gr.Markdown()
            with gr.Row():
                settings_model = gr.Dropdown(
                    label="Default model (used unless overridden)",
                    choices=[ws.state.model] if ws.state.model else [],
                    value=ws.state.model or None,
                    interactive=True, allow_custom_value=True, scale=3,
                )

            with gr.Accordion("Per-task model overrides", open=False):
                gr.Markdown(
                    "Optional: pick different models for the LLM judge or DSPy "
                    "optimizer. Leave blank to use the default model."
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
                resources_md = gr.Markdown(
                    "_Click 'Detect' to scan available resources._"
                )
                detect_resources_btn = gr.Button("Detect", size="sm")

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

            with gr.Row():
                pull_in = gr.Textbox(
                    label="Pull a new model from Ollama Hub",
                    placeholder="e.g. qwen3:4b, gemma3:4b",
                    scale=3,
                )
                pull_btn = gr.Button("Pull", scale=1)
            pull_status_md = gr.Markdown()

        # ---- Now retroactively populate Chat and Runs tabs ----
        # Gradio attaches all components to the Blocks at construction time,
        # so we can `with chat_tab:` to add components into that tab block
        # post-hoc.
        with chat_tab:
            build_chat_tab(
                recipes_state=recipes_state,
                chat_history_state=chat_history_state,
                recipe_tests_state=recipe_tests_state,
                docs_state=docs_state,
                settings_host=settings_host,
                settings_model=settings_model,
            )

        with runs_tab:
            build_runs_tab(
                recipes_state=recipes_state,
                docs_state=docs_state,
                settings_host=settings_host,
                settings_model=settings_model,
            )

        # ---- Settings handlers (post-build wiring) ----
        check_btn.click(handle_check_ollama, settings_host, [host_status_md, settings_model])
        pull_btn.click(handle_pull_model, [settings_host, pull_in], [pull_status_md, settings_model])

        def _populate_overrides(host: str, default_model: str):
            client = OllamaClient(host=host or DEFAULT_HOST)
            if not client.is_alive(timeout=2.0):
                return gr.update(choices=[]), gr.update(choices=[])
            try:
                models = client.list_models()
            except Exception:  # noqa: BLE001
                return gr.update(choices=[]), gr.update(choices=[])
            return gr.update(choices=models), gr.update(choices=models)

        check_btn.click(_populate_overrides, [settings_host, settings_model],
                        [judge_model_dd, optimizer_model_dd])

        def _update_inference_state(host, model, judge_model, optimizer_model):
            client = OllamaClient(host=host or DEFAULT_HOST)
            state = {
                "client": client,
                "model": model or "",
                "host": host or DEFAULT_HOST,
                "judge_model": judge_model or "",
                "optimizer_model": optimizer_model or "",
            }
            _persist_workspace(host=state["host"], model=state["model"])
            return state

        for trigger in (settings_host, settings_model, judge_model_dd, optimizer_model_dd):
            trigger.change(
                _update_inference_state,
                [settings_host, settings_model, judge_model_dd, optimizer_model_dd],
                inference_state,
            )

        def _refresh_banner(state):
            msg, ok = connection_status(state)
            return msg or "✓ Ready"
        inference_state.change(_refresh_banner, inference_state, banner)

        # ---- Persistence wiring ----
        def _persist_docs(df):
            _persist_workspace(docs_df=df)
            return df
        docs_state.change(_persist_docs, docs_state, docs_state)

        def _persist_chat(history):
            _persist_workspace(chat_history=history)
            return history
        chat_history_state.change(_persist_chat, chat_history_state, chat_history_state)

        def _persist_tests(tests):
            _persist_workspace(recipe_tests=tests)
            return tests
        recipe_tests_state.change(_persist_tests, recipe_tests_state, recipe_tests_state)

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
