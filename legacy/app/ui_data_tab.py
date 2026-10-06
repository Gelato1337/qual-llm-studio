"""Data tab UI.

Stages:
  1. Source       — files / paste / HuggingFace
                    - JSON files get a path picker before tabular structure
                    - Per-file remove button on the loaded list
                    - Per-file preview before Apply
  2. Structure    — for tabular sources, multi-select text/id/metadata cols
  3. Segmentation — strategy + params + per-source allowlist + live re-segment
  4. Sampling     — limit to first N rows for cheap dry-runs
  5. Apply        — full pipeline, persist to workspace, populate docs_state

State is held in workspace.Workspace and mirrored into gr.State for live use.
A "Reset all data" button wipes everything and re-applies on every change.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import gradio as gr
import pandas as pd

from . import errors, ingest
from .ingest.pipeline import IngestConfig, LoadedSource


# ---------------------------------------------------------------------------
# Internal helpers — these run in handlers, so they must be safe to fail loudly
# ---------------------------------------------------------------------------


@errors.safe_handler
def _load_files(files, current_sources: list[LoadedSource]):
    """Append uploaded files to the existing source list. Returns:
    (sources, status_md, source_choices_for_remove, segment_only_choices,
     col_dropdowns_text, col_dropdowns_id, col_dropdowns_meta)."""
    if not files:
        errors.warn("No files selected.")
        return current_sources, "", *_source_dropdown_updates(current_sources)

    paths = [f.name if hasattr(f, "name") else f for f in files]
    new_sources: list[LoadedSource] = list(current_sources)
    skipped: list[str] = []
    for p in paths:
        try:
            s = ingest.load_files_as_sources([p])[0]
            new_sources.append(s)
        except Exception as exc:  # noqa: BLE001
            skipped.append(f"{Path(p).name}: {exc}")

    msg_lines = [f"Loaded {len(paths) - len(skipped)} of {len(paths)} file(s)."]
    if skipped:
        msg_lines.append("Skipped:")
        msg_lines.extend(f"- {s}" for s in skipped)
    return new_sources, "\n".join(msg_lines), *_source_dropdown_updates(new_sources)


@errors.safe_handler
def _load_paste(text: str, mode: str, current_sources: list[LoadedSource]):
    if not text or not text.strip():
        errors.warn("Paste some text first.")
        return current_sources, "", *_source_dropdown_updates(current_sources)
    src = ingest.load_paste_as_source(text, mode)
    new_sources = list(current_sources) + [src]
    n = len(src.documents or [])
    return (
        new_sources,
        f"Loaded paste source: {n} document(s).",
        *_source_dropdown_updates(new_sources),
    )


@errors.safe_handler
def _load_hf(name: str, split: str, max_rows, current_sources: list[LoadedSource]):
    if not name or not name.strip():
        errors.error("Enter a HuggingFace dataset name.")
    src = ingest.load_huggingface_as_source(
        name.strip(),
        split=(split or "train").strip(),
        max_rows=int(max_rows) if max_rows else None,
    )
    new_sources = list(current_sources) + [src]
    return (
        new_sources,
        f"Loaded HF dataset {name} (split={split}): {len(src.dataframe)} rows.",
        *_source_dropdown_updates(new_sources),
    )


def _source_dropdown_updates(sources: list[LoadedSource]):
    """Update tuple for: source list display, segment_only choices,
    text columns, id column, metadata columns."""
    names = [s.name for s in sources]

    # Aggregate columns from all tabular sources
    cols: list[str] = []
    for s in sources:
        if s.is_tabular:
            for c in s.dataframe.columns:
                if c not in cols:
                    cols.append(c)

    return (
        gr.update(choices=names, value=[]),                          # source remove dropdown
        gr.update(choices=names, value=names),                       # segment_only (default = all)
        gr.update(choices=cols),                                     # text_columns
        gr.update(choices=cols),                                     # id_column
        gr.update(choices=cols),                                     # metadata_columns
    )


@errors.safe_handler
def _remove_sources(to_remove: list[str], current_sources: list[LoadedSource]):
    if not to_remove:
        errors.warn("Pick which sources to remove.")
        return current_sources, "No sources removed.", *_source_dropdown_updates(current_sources)
    new_sources = [s for s in current_sources if s.name not in to_remove]
    msg = f"Removed {len(current_sources) - len(new_sources)} source(s)."
    return new_sources, msg, *_source_dropdown_updates(new_sources)


@errors.safe_handler
def _clear_all(current_sources):
    return [], "Cleared all loaded sources.", *_source_dropdown_updates([])


@errors.safe_handler
def _peek_json(files):
    """For a single uploaded JSON file, show the user what's inside and a suggested path."""
    if not files:
        return "", ""
    path = files[0].name if hasattr(files[0], "name") else files[0]
    p = Path(path)
    if p.suffix.lower() not in {".json", ".jsonl"}:
        return "", ""
    info = ingest.json_loader.peek(p)
    summary = (
        f"**Detected:** {info['kind']}  ·  "
        f"**Suggested path:** `{info['suggested_path']}`\n\n"
        f"**Top-level keys:** {info['top_level_keys']}"
    )
    sample = "```json\n" + info["sample"] + "\n```"
    return summary, sample


@errors.safe_handler
def _apply_json_path(files, json_path: str, current_sources):
    """When a JSON file is uploaded with a custom path, rebuild that source
    using the path instead of the default '.'."""
    if not files:
        errors.warn("No files uploaded.")
        return current_sources, "", *_source_dropdown_updates(current_sources)
    paths = [f.name if hasattr(f, "name") else f for f in files]
    new_sources = list(current_sources)
    for p in paths:
        if Path(p).suffix.lower() not in {".json", ".jsonl"}:
            continue
        # Remove any previously-loaded source for this file before re-loading
        new_sources = [s for s in new_sources if s.name != Path(p).name]
        try:
            df = ingest.load_json_with_path(p, json_path=json_path or ".")
            src = LoadedSource(name=Path(p).name, dataframe=df)
            new_sources.append(src)
        except Exception as exc:  # noqa: BLE001
            errors.error(f"JSON path {json_path!r} failed: {exc}")
    return (
        new_sources,
        f"Applied path {json_path!r} to JSON file(s).",
        *_source_dropdown_updates(new_sources),
    )


@errors.safe_handler
def _preview_one(source_name: str, current_sources):
    """Show the head of a specific source so the user can sanity-check
    extraction before they hit Apply. Big PDF that came out as gibberish?
    They see it here, before running 1000 docs."""
    if not source_name:
        return "Pick a source above to preview."
    for s in current_sources:
        if s.name == source_name:
            if s.is_tabular:
                head = s.dataframe.head(5).to_string(max_colwidth=80)
                return f"**{s.name}** (tabular, {len(s.dataframe)} rows)\n\n```\n{head}\n```"
            docs = s.documents or []
            preview = "\n\n---\n\n".join(
                f"id: {d.id}\n\n{d.text[:500]}{'...' if len(d.text) > 500 else ''}"
                for d in docs[:3]
            )
            return f"**{s.name}** ({len(docs)} document(s))\n\n{preview}"
    return "Source not found."


@errors.safe_handler
def _apply_pipeline(
    sources: list[LoadedSource],
    text_cols: list[str],
    id_col: str | None,
    metadata_cols: list[str],
    segmenter: str,
    seg_params_json: str,
    segment_only: list[str],
    sample_n,
    inference_state: dict,
):
    """The Apply button. Runs the full ingestion pipeline."""
    if not sources:
        errors.error("Load at least one source first.")

    if any(s.is_tabular for s in sources) and not text_cols:
        errors.error("Pick at least one text column for the tabular source(s).")

    seg_params: dict[str, Any] = {}
    if seg_params_json and seg_params_json.strip():
        try:
            seg_params = json.loads(seg_params_json)
        except json.JSONDecodeError as exc:
            errors.error(f"Segmenter params not valid JSON: {exc}")

    cfg = IngestConfig(
        text_columns=text_cols or [],
        id_column=id_col or None,
        metadata_columns=metadata_cols or [],
        segmenter=segmenter,
        segmenter_params=seg_params,
        segment_only=segment_only or [],
        inference_client=(inference_state or {}).get("client"),
        inference_model=(inference_state or {}).get("model", ""),
    )

    df = ingest.ingest(sources, cfg)

    # Sample-N (applied after segmentation so chunks are sampled, not source rows)
    sample_n = int(sample_n) if sample_n else 0
    if sample_n > 0 and len(df) > sample_n:
        df = df.head(sample_n).reset_index(drop=True)

    p = ingest.preview(df, n=10)
    summary = (
        f"**{p['n_rows']:,}** rows  ·  "
        f"avg **{p['avg_chars']:,}** chars  ·  "
        f"min **{p['min_chars']:,}** · max **{p['max_chars']:,}**"
    )
    if sample_n > 0:
        summary += f"  ·  *(limited to first {sample_n})*"
    return df, summary, gr.update(value=p["head"])


_DEFAULT_PARAMS = {
    "none": {},
    "char_length": {"chunk_size": 5000, "overlap": 0},
    "paragraph": {"group_target_chars": 0},
    "regex": {"pattern": "^Chapter \\d+"},
    "llm_natural": {"wanted_length": 5000, "max_length": 6000},
}


def _on_segmenter_change(name: str):
    return json.dumps(_DEFAULT_PARAMS.get(name, {}), indent=2)


@errors.safe_handler
def _build_regex_helper_prompt(positives_text: str, negatives_text: str):
    positives = [line.strip() for line in (positives_text or "").splitlines() if line.strip()]
    negatives = [line.strip() for line in (negatives_text or "").splitlines() if line.strip()]
    if not positives:
        errors.warn("Add at least one positive example.")
        return ""
    return ingest.build_regex_prompt(positives, negatives or None)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


@errors.safe_handler
def _save_dataset(name: str, docs_df):
    if not name or not name.strip():
        errors.error("Type a name for the dataset.")
    if docs_df is None or len(docs_df) == 0:
        errors.error("Apply data first — there's nothing to save.")
    from . import paths as _paths
    safe = _paths.safe_filename(name.strip())
    target = _paths.DATASETS_DIR / f"{safe}.csv"
    if target.exists():
        errors.error(
            f"`datasets/{safe}.csv` already exists. Pick a different name "
            "or delete the old file first."
        )
    _paths.DATASETS_DIR.mkdir(parents=True, exist_ok=True)
    docs_df.to_csv(target, index=False)
    return f"✓ Saved to `datasets/{safe}.csv` ({len(docs_df):,} rows)"


@errors.safe_handler
def _open_dataset(dataset_path: str):
    """Load a dataset CSV directly into docs_state, bypassing the load->structure->segment pipeline."""
    if not dataset_path:
        errors.error("Pick a dataset.")
    df = pd.read_csv(dataset_path)
    p = ingest.preview(df, n=10)
    summary = (
        f"**{p['n_rows']:,}** rows  ·  "
        f"avg **{p['avg_chars']:,}** chars · "
        f"loaded from `{Path(dataset_path).name}`"
    )
    return df, summary, gr.update(value=p["head"])


@errors.safe_handler
def _refresh_datasets_dropdown():
    from . import paths as _paths
    return gr.update(choices=[(p.stem, str(p)) for p in _paths.list_datasets()])


def build_data_tab(
    *,
    docs_state: gr.State,
    sources_state: gr.State,
    inference_state: gr.State,
):
    """Build the Data tab. Caller is inside `with gr.Tab(...):`."""

    gr.Markdown("### 1. Source")
    with gr.Row():
        source_radio = gr.Radio(
            choices=["Files", "Paste", "HuggingFace"],
            value="Files",
            label="Source type",
        )
        clear_btn = gr.Button("Reset all data", variant="stop", size="sm")

    # Files
    with gr.Group(visible=True) as files_group:
        file_in = gr.File(
            label="Upload (CSV, XLSX, PDF, DOCX, TXT, JSON, JSONL)",
            file_count="multiple",
            file_types=[".csv", ".tsv", ".xlsx", ".xls", ".pdf", ".docx", ".txt",
                        ".json", ".jsonl"],
        )
        files_btn = gr.Button("Add files", variant="primary")

        # JSON path picker — only meaningfully relevant when at least one
        # JSON file is queued
        with gr.Accordion("JSON path picker (only for JSON / JSONL files)", open=False):
            gr.Markdown(
                "JSON files default to the root array `.`. If your records "
                "are nested (e.g. `[{data: {...}}, ...]`), set the path. "
                "Use `.field` to descend, `[*]` to iterate an array. "
                "Examples: `[*].data`, `.results.items`."
            )
            json_peek_summary = gr.Markdown()
            json_peek_sample = gr.Markdown()
            file_in.change(_peek_json, file_in, [json_peek_summary, json_peek_sample])
            with gr.Row():
                json_path_in = gr.Textbox(label="Path", value=".", scale=3)
                json_apply_btn = gr.Button("Apply path", variant="secondary", scale=1)

    # Paste
    with gr.Group(visible=False) as paste_group:
        paste_in = gr.Textbox(label="Paste text", lines=8)
        paste_mode = gr.Radio(["single", "lines"], value="single",
                              label="One document, or one per line?")
        paste_btn = gr.Button("Add paste", variant="primary")

    # HF
    with gr.Group(visible=False) as hf_group:
        with gr.Row():
            hf_name = gr.Textbox(label="Dataset name (e.g. 'imdb')", scale=3)
            hf_split = gr.Textbox(label="Split", value="train", scale=1)
            hf_max = gr.Number(label="Max rows", value=200, scale=1)
        hf_btn = gr.Button("Add HuggingFace", variant="primary")

    load_status = gr.Markdown()

    def _switch_source(choice):
        return (
            gr.update(visible=choice == "Files"),
            gr.update(visible=choice == "Paste"),
            gr.update(visible=choice == "HuggingFace"),
        )

    source_radio.change(_switch_source, source_radio, [files_group, paste_group, hf_group])

    # ---- Loaded source list ----
    gr.Markdown("### Loaded sources")
    sources_remove = gr.CheckboxGroup(
        label="Pick to remove",
        choices=[],
    )
    with gr.Row():
        remove_btn = gr.Button("Remove selected", size="sm")
        preview_one_dd = gr.Dropdown(
            label="Preview a specific source",
            choices=[],
            scale=2,
        )
    one_preview = gr.Markdown()
    preview_one_dd.change(_preview_one, [preview_one_dd, sources_state], one_preview)

    # ---- Stage 2: Structure ----
    gr.Markdown("### 2. Structure (tabular sources only)")
    with gr.Row():
        text_cols_in = gr.Dropdown(
            label="Text columns",
            choices=[], multiselect=True, scale=2,
        )
        id_col_in = gr.Dropdown(
            label="ID column (optional)",
            choices=[], scale=1,
        )
        metadata_cols_in = gr.Dropdown(
            label="Keep as metadata",
            choices=[], multiselect=True, scale=2,
        )

    # ---- Stage 3: Segmentation ----
    gr.Markdown("### 3. Segmentation")
    segmenter_in = gr.Dropdown(label="Strategy", choices=ingest.SEGMENTERS, value="none")
    seg_params_in = gr.Code(label="Parameters (JSON)", language="json", value="{}", lines=4)
    segment_only_in = gr.CheckboxGroup(
        label="Apply segmentation to (uncheck to skip)",
        choices=[],
    )
    segmenter_in.change(_on_segmenter_change, segmenter_in, seg_params_in)

    with gr.Accordion("Need help building a regex? Generate a prompt for an external LLM.", open=False):
        gr.Markdown(
            "Paste 3+ examples of separator strings, generate a prompt, "
            "send to ChatGPT/Claude/Gemini, paste the resulting regex back into "
            "the Parameters JSON above."
        )
        with gr.Row():
            regex_pos = gr.Textbox(label="Should match", lines=4,
                                    placeholder="Chapter 1\nChapter 17")
            regex_neg = gr.Textbox(label="Should NOT match (optional)", lines=4,
                                    placeholder="Section 1")
        regex_helper_btn = gr.Button("Generate prompt")
        regex_helper_out = gr.Textbox(label="Copy this into ChatGPT / Claude / Gemini",
                                       lines=12, interactive=False)
        regex_helper_btn.click(_build_regex_helper_prompt, [regex_pos, regex_neg], regex_helper_out)

    # ---- Stage 4: Sampling + Apply ----
    gr.Markdown("### 4. Build dataset")
    sample_n_in = gr.Number(
        label="Sample first N rows (0 = all). Use small N (5-20) when testing recipes.",
        value=0, precision=0,
    )
    apply_btn = gr.Button("Apply", variant="primary", size="lg")
    preview_summary = gr.Markdown()
    preview_df = gr.Dataframe(label="Preview (first 10 rows)", wrap=True)

    # ---- Stage 5: Save / Open dataset ----
    gr.Markdown("### 5. Save / Open dataset")
    gr.Markdown(
        "After Apply, save the result as a dataset CSV. Saved datasets are "
        "available on the Runs tab and the Chat tab without re-uploading."
    )
    with gr.Row():
        save_name_in = gr.Textbox(
            label="Save current dataset as (filename without .csv)",
            placeholder="my_interviews_segmented",
            scale=3,
        )
        save_dataset_btn = gr.Button("Save dataset", variant="primary", scale=1)
    save_dataset_status = gr.Markdown()

    with gr.Row():
        from . import paths as _paths
        existing_datasets_dd = gr.Dropdown(
            label="Or open an existing dataset",
            choices=[(p.stem, str(p)) for p in _paths.list_datasets()],
            scale=3,
        )
        open_dataset_btn = gr.Button("Open dataset", scale=1)
        refresh_datasets_btn = gr.Button("🔄", scale=1, size="sm")
    open_dataset_status = gr.Markdown()

    # ---- Wiring ----
    files_btn.click(
        _load_files, [file_in, sources_state],
        [sources_state, load_status,
         sources_remove, segment_only_in, text_cols_in, id_col_in, metadata_cols_in],
    )
    json_apply_btn.click(
        _apply_json_path, [file_in, json_path_in, sources_state],
        [sources_state, load_status,
         sources_remove, segment_only_in, text_cols_in, id_col_in, metadata_cols_in],
    )
    paste_btn.click(
        _load_paste, [paste_in, paste_mode, sources_state],
        [sources_state, load_status,
         sources_remove, segment_only_in, text_cols_in, id_col_in, metadata_cols_in],
    )
    hf_btn.click(
        _load_hf, [hf_name, hf_split, hf_max, sources_state],
        [sources_state, load_status,
         sources_remove, segment_only_in, text_cols_in, id_col_in, metadata_cols_in],
    )
    remove_btn.click(
        _remove_sources, [sources_remove, sources_state],
        [sources_state, load_status,
         sources_remove, segment_only_in, text_cols_in, id_col_in, metadata_cols_in],
    )
    clear_btn.click(
        _clear_all, sources_state,
        [sources_state, load_status,
         sources_remove, segment_only_in, text_cols_in, id_col_in, metadata_cols_in],
    )

    # When sources change, also refresh the per-source preview dropdown
    def _refresh_preview_dd(srcs):
        return gr.update(choices=[s.name for s in srcs])

    sources_state.change(_refresh_preview_dd, sources_state, preview_one_dd)

    apply_btn.click(
        _apply_pipeline,
        [
            sources_state, text_cols_in, id_col_in, metadata_cols_in,
            segmenter_in, seg_params_in, segment_only_in, sample_n_in,
            inference_state,
        ],
        [docs_state, preview_summary, preview_df],
    )

    save_dataset_btn.click(
        _save_dataset, [save_name_in, docs_state], save_dataset_status,
    )
    save_dataset_btn.click(_refresh_datasets_dropdown, None, existing_datasets_dd)

    open_dataset_btn.click(
        _open_dataset, existing_datasets_dd,
        [docs_state, preview_summary, preview_df],
    )
    refresh_datasets_btn.click(_refresh_datasets_dropdown, None, existing_datasets_dd)
