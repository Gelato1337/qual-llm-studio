"""Ingestion package — load, structure, segment.

Public API the UI uses:
  load_files_as_sources, load_paste_as_source, load_huggingface_as_source
  IngestConfig, ingest
  column_candidates  (for tabular file picker)
  build_regex_prompt (for the regex-helper expander)
  preview            (for the data tab preview pane)

For backwards compatibility with the older flat ingest.py, this module
also re-exports a few simple helpers (from_text, from_files, etc.).
"""

from __future__ import annotations

from . import pipeline, segmenters, sources, structure
from .pipeline import (
    IngestConfig,
    LoadedSource,
    ingest,
    load_files_as_sources,
    load_huggingface_as_source,
    load_paste_as_source,
    preview,
)
from .regex_helper import build_prompt as build_regex_prompt
from .segmenters import Document, build as build_segmenter, LIST as SEGMENTERS, REGISTRY
from .sources import column_candidates, load_json_with_path
from . import json_loader


# ---- Back-compat shims ----------------------------------------------------
# These keep the existing main.py working while the UI is rewritten.

def from_text(text, source="paste"):
    """Single document from a string."""
    docs = sources.load_paste(text, mode="single")
    return structure.documents_to_dataframe(docs)


def from_text_lines(text, source="paste_lines"):
    """One document per non-empty line."""
    docs = sources.load_paste(text, mode="lines")
    return structure.documents_to_dataframe(docs)


def from_dataframe(df, text_column, id_column=None, source="dataframe"):
    """Normalize a user DataFrame to the standard shape."""
    docs = structure.tabular_to_documents(
        df, text_columns=[text_column], id_column=id_column, source=source
    )
    return structure.documents_to_dataframe(docs)


def from_path(path, text_column=None, id_column=None):
    """Auto-dispatch by extension. Tabular formats need text_column."""
    result = sources.load_file(path)
    if isinstance(result, list):
        # Already a list[Document]
        import pandas as _pd
        return structure.documents_to_dataframe(result)
    if text_column is None:
        raise ValueError(f"text_column required for tabular file {path}")
    docs = structure.tabular_to_documents(
        result, text_columns=[text_column], id_column=id_column, source=str(path)
    )
    return structure.documents_to_dataframe(docs)


def from_files(paths, text_column=None, id_column=None):
    """Concatenate multiple files."""
    import pandas as _pd
    frames = [from_path(p, text_column, id_column) for p in paths]
    if not frames:
        return _pd.DataFrame(columns=["id", "text", "source"])
    return _pd.concat(frames, ignore_index=True)


__all__ = [
    "IngestConfig", "LoadedSource", "ingest",
    "load_files_as_sources", "load_paste_as_source", "load_huggingface_as_source",
    "load_json_with_path", "json_loader",
    "preview", "column_candidates", "build_regex_prompt",
    "Document", "build_segmenter", "SEGMENTERS", "REGISTRY",
    # Back-compat
    "from_text", "from_text_lines", "from_dataframe", "from_path", "from_files",
]
