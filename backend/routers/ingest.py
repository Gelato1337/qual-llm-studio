"""Data ingestion endpoints.

Three-stage flow mirroring the existing Gradio Data tab:

  Stage 1 — LOAD: POST /api/ingest/files | /paste | /huggingface
    Each returns a list of loaded-source summaries. Sources are stored in
    backend.state.loaded_sources until replaced.

  Stage 2+3 — STRUCTURE + SEGMENT: POST /api/ingest/structure
    Applies the user's column choices + segmenter to all loaded sources.
    Saves the resulting docs DataFrame to the workspace.

  Preview: GET /api/ingest/preview
    Returns stats + first N rows of the current docs DataFrame.

  Clear: DELETE /api/ingest/sources
    Wipes loaded sources from memory (docs in workspace untouched).
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

import backend.state as state
from app.ingest import pipeline as ingest_pipeline
from app.ingest.pipeline import IngestConfig, LoadedSource

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _source_summary(src: LoadedSource) -> dict:
    if src.is_tabular and src.dataframe is not None:
        return {
            "name": src.name,
            "kind": "tabular",
            "n_rows": len(src.dataframe),
            "columns": list(src.dataframe.columns),
        }
    docs = src.documents or []
    return {
        "name": src.name,
        "kind": "documents",
        "n_docs": len(docs),
        "columns": [],
    }


# ---------------------------------------------------------------------------
# Stage 1: Loading
# ---------------------------------------------------------------------------


@router.post("/files")
async def upload_files(files: list[UploadFile] = File(...)):
    """Upload one or more files. Replaces the current loaded sources.

    Supported: .csv, .tsv, .xlsx, .xls, .pdf, .docx, .txt, .json, .jsonl
    """
    new_sources: list[LoadedSource] = []
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_paths: list[Path] = []
        for upload in files:
            dest = Path(tmpdir) / (upload.filename or "upload")
            dest.write_bytes(await upload.read())
            tmp_paths.append(dest)

        try:
            new_sources = ingest_pipeline.load_files_as_sources(tmp_paths)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    state.loaded_sources = new_sources
    return {"sources": [_source_summary(s) for s in new_sources]}


class PasteRequest(BaseModel):
    text: str
    mode: str = "single"  # "single" | "lines"


@router.post("/paste")
def ingest_paste(req: PasteRequest):
    """Ingest pasted text as a single document source."""
    try:
        src = ingest_pipeline.load_paste_as_source(req.text, mode=req.mode)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    state.loaded_sources = [src]
    return {"sources": [_source_summary(src)]}


class HuggingFaceRequest(BaseModel):
    name: str
    split: str = "train"
    config: str | None = None
    max_rows: int | None = None


@router.post("/huggingface")
def ingest_huggingface(req: HuggingFaceRequest):
    """Load a HuggingFace dataset by ID."""
    try:
        src = ingest_pipeline.load_huggingface_as_source(
            req.name, split=req.split, config=req.config, max_rows=req.max_rows,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    state.loaded_sources = [src]
    return {"sources": [_source_summary(src)]}


@router.get("/sources")
def list_sources():
    """Return summaries of the currently loaded sources."""
    return {"sources": [_source_summary(s) for s in state.loaded_sources]}


@router.delete("/sources")
def clear_sources():
    """Clear loaded sources from memory."""
    state.loaded_sources = []
    return {"cleared": True}


# ---------------------------------------------------------------------------
# Stage 2+3: Structure + Segment
# ---------------------------------------------------------------------------


class StructureRequest(BaseModel):
    text_columns: list[str] = []
    id_column: str | None = None
    metadata_columns: list[str] = []
    segmenter: str = "none"
    segmenter_params: dict[str, Any] = {}
    segment_only: list[str] = []
    # LLM segmenter needs host + model
    inference_host: str = ""
    inference_model: str = ""


@router.post("/structure")
def structure_sources(req: StructureRequest):
    """Apply structure + segmentation to loaded sources.

    Saves the resulting docs DataFrame into the workspace and returns a
    preview of the first 10 rows plus summary stats.
    """
    if not state.loaded_sources:
        raise HTTPException(status_code=400, detail="No sources loaded. Upload files first.")

    # Wire up the LLM client for llm_natural segmenter if requested
    inference_client = None
    if req.segmenter == "llm_natural" and req.inference_host:
        from app.inference import OllamaClient
        inference_client = OllamaClient(host=req.inference_host)

    cfg = IngestConfig(
        text_columns=req.text_columns,
        id_column=req.id_column,
        metadata_columns=req.metadata_columns,
        segmenter=req.segmenter,
        segmenter_params=req.segmenter_params,
        segment_only=req.segment_only,
        inference_client=inference_client,
        inference_model=req.inference_model,
    )

    try:
        docs_df = ingest_pipeline.ingest(state.loaded_sources, cfg)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # Persist to workspace
    state.workspace.docs_df = docs_df
    state.workspace.save()

    return _docs_preview(docs_df, n=10)


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------


@router.get("/preview")
def preview_docs(n: int = 10):
    """Preview the current workspace docs DataFrame."""
    df = state.workspace.docs_df
    if df is None or len(df) == 0:
        return {"n_rows": 0, "avg_chars": 0, "min_chars": 0, "max_chars": 0,
                "columns": [], "rows": []}
    return _docs_preview(df, n=n)


def _docs_preview(df, n: int = 10) -> dict:
    stats = ingest_pipeline.preview(df, n=n)
    head = stats["head"]
    return {
        "n_rows": stats["n_rows"],
        "avg_chars": stats["avg_chars"],
        "min_chars": stats["min_chars"],
        "max_chars": stats["max_chars"],
        "columns": stats["columns"],
        "rows": head.to_dict(orient="records"),
    }
