"""Three-stage ingestion orchestrator.

Stage 1 — LOAD:    raw inputs (file/paste/HF) → list[Document] or DataFrame
Stage 2 — STRUCTURE: tabular DataFrame → list[Document]
Stage 3 — SEGMENT: list[Document] → list[Document] (possibly more)

Public entry point is `ingest()`. It composes the stages based on the
config it's given, applies a per-source segmenter selection, and returns
a final DataFrame ready for the recipe runner.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Union

import pandas as pd

from . import sources, structure
from .segmenters import Document, build as build_segmenter

# A LoadedSource is one input that's been loaded. It carries enough info
# to apply structure + segmentation independently of other sources.
@dataclass
class LoadedSource:
    name: str  # "myfile.pdf", "paste", "hf:imdb", etc.
    documents: list[Document] | None = None
    dataframe: pd.DataFrame | None = None  # for tabular sources

    @property
    def is_tabular(self) -> bool:
        return self.dataframe is not None


@dataclass
class IngestConfig:
    """User's choices, gathered from the UI."""

    # Tabular structure config (used per-source if that source is tabular)
    text_columns: list[str] = field(default_factory=list)
    id_column: str | None = None
    metadata_columns: list[str] = field(default_factory=list)

    # Segmentation config
    segmenter: str = "none"
    segmenter_params: dict[str, Any] = field(default_factory=dict)
    # If non-empty, only sources with names in this set get segmented.
    # Empty = apply to all (default).
    segment_only: list[str] = field(default_factory=list)
    # Skip segmentation for tabular sources by default; researcher can
    # opt in by including the source name in segment_only.
    skip_tabular_by_default: bool = True

    # Optional inference client/model for llm_natural segmenter
    inference_client: Any = None
    inference_model: str = ""


# ---- Loading helpers (Stage 1 wrappers) ------------------------------------


def load_files_as_sources(paths: list[Union[str, Path]]) -> list[LoadedSource]:
    out = []
    for p in paths:
        result = sources.load_file(p)
        name = Path(p).name
        if isinstance(result, pd.DataFrame):
            out.append(LoadedSource(name=name, dataframe=result))
        else:
            out.append(LoadedSource(name=name, documents=result))
    return out


def load_paste_as_source(text: str, mode: str = "single") -> LoadedSource:
    docs = sources.load_paste(text, mode)
    return LoadedSource(name="paste", documents=docs)


def load_huggingface_as_source(
    name: str, *, split: str = "train", config: str | None = None,
    max_rows: int | None = None,
) -> LoadedSource:
    df = sources.load_huggingface(name, split=split, config=config, max_rows=max_rows)
    return LoadedSource(name=f"hf:{name}", dataframe=df)


# ---- Orchestrator (Stage 2 + 3) -------------------------------------------


def ingest(loaded_sources: list[LoadedSource], cfg: IngestConfig) -> pd.DataFrame:
    """Apply structure + segmentation to all loaded sources. Return a
    single DataFrame ready for the recipe runner."""
    all_docs: list[Document] = []

    for src in loaded_sources:
        # Stage 2: structure
        if src.is_tabular:
            docs = structure.tabular_to_documents(
                src.dataframe,
                text_columns=cfg.text_columns,
                id_column=cfg.id_column,
                metadata_columns=cfg.metadata_columns,
                source=src.name,
            )
        else:
            docs = src.documents or []

        # Stage 3: segmentation, conditional
        if _should_segment(src, cfg):
            seg = build_segmenter(cfg.segmenter, **cfg.segmenter_params)
            # llm_natural needs the inference client wired in
            if hasattr(seg, "client") and seg.client is None:
                seg.client = cfg.inference_client
            if hasattr(seg, "model") and not seg.model:
                seg.model = cfg.inference_model
            new_docs: list[Document] = []
            for d in docs:
                new_docs.extend(seg.split(d))
            docs = new_docs

        all_docs.extend(docs)

    return structure.documents_to_dataframe(all_docs)


def _should_segment(src: LoadedSource, cfg: IngestConfig) -> bool:
    if cfg.segmenter == "none":
        return False
    # Per-source allowlist
    if cfg.segment_only:
        return src.name in cfg.segment_only
    # Default behavior for tabular sources
    if src.is_tabular and cfg.skip_tabular_by_default:
        return False
    return True


# ---- Convenience: preview helpers for the UI ------------------------------


def preview(df: pd.DataFrame, n: int = 5) -> dict:
    """Summary statistics for the Data tab's preview pane."""
    if df is None or len(df) == 0:
        return {"n_rows": 0, "avg_chars": 0, "min_chars": 0, "max_chars": 0,
                "columns": [], "head": pd.DataFrame()}
    text_lens = df["text"].astype(str).str.len() if "text" in df.columns else pd.Series([0])
    return {
        "n_rows": len(df),
        "avg_chars": int(text_lens.mean()),
        "min_chars": int(text_lens.min()),
        "max_chars": int(text_lens.max()),
        "columns": list(df.columns),
        "head": df.head(n),
    }
