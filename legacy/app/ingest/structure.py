"""Stage 2: Convert tabular DataFrames into Documents.

Multi-column concatenation:
  Selected text columns are joined as
    "Column name: value\n\nColumn name: value"
  This format is human-readable and recipes can reference specific
  columns in the prompt naturally — the column names appear right
  there in the rendered text.

Metadata columns are kept on each Document but not folded into text;
they're passed through as Document.metadata for downstream use (filtering,
joining, eval).
"""

from __future__ import annotations

import pandas as pd

from .segmenters import Document


def tabular_to_documents(
    df: pd.DataFrame,
    *,
    text_columns: list[str],
    id_column: str | None = None,
    metadata_columns: list[str] | None = None,
    source: str = "tabular",
) -> list[Document]:
    """Turn a DataFrame into a list of Documents.

    Args:
        df: the source DataFrame.
        text_columns: 1+ columns whose values become the document text.
                      Multi-column values are concatenated with column-name
                      prefixes (see module docstring).
        id_column: optional; column whose values become Document.id.
                   Falls back to "row_<i>" if missing or values are blank.
        metadata_columns: optional; columns whose values are kept on the
                          document as metadata.
        source: a string describing where this DataFrame came from
                (filename, "hf:imdb", etc.).
    """
    if not text_columns:
        raise ValueError("at least one text column is required")
    missing = [c for c in text_columns if c not in df.columns]
    if missing:
        raise ValueError(f"text columns not in dataframe: {missing}")
    if id_column and id_column not in df.columns:
        raise ValueError(f"id column {id_column!r} not in dataframe")
    metadata_columns = metadata_columns or []
    missing_meta = [c for c in metadata_columns if c not in df.columns]
    if missing_meta:
        raise ValueError(f"metadata columns not in dataframe: {missing_meta}")

    docs: list[Document] = []
    for i, row in df.iterrows():
        # Build text
        if len(text_columns) == 1:
            text = _stringify(row[text_columns[0]])
        else:
            parts = []
            for col in text_columns:
                value = _stringify(row[col])
                if value:
                    parts.append(f"{col}: {value}")
            text = "\n\n".join(parts)

        # Build id
        if id_column:
            raw_id = _stringify(row[id_column]).strip()
            doc_id = raw_id if raw_id else f"row_{i}"
        else:
            doc_id = f"row_{i}"

        # Build metadata
        metadata = {col: _coerce_metadata(row[col]) for col in metadata_columns}

        docs.append(Document(id=doc_id, text=text, source=source, metadata=metadata))
    return docs


def _stringify(value) -> str:
    """Best-effort conversion of any pandas value to string."""
    if value is None:
        return ""
    if pd.isna(value):
        return ""
    return str(value)


def _coerce_metadata(value):
    """Keep simple types as-is; stringify everything else for safe storage."""
    if value is None or pd.isna(value):
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def documents_to_dataframe(docs: list[Document]) -> pd.DataFrame:
    """Inverse of tabular_to_documents: produce the DataFrame the recipe
    runner consumes (id, text, source, plus metadata as columns)."""
    if not docs:
        return pd.DataFrame(columns=["id", "text", "source"])
    rows = []
    all_meta_keys: set[str] = set()
    for d in docs:
        all_meta_keys.update(d.metadata.keys())
    for d in docs:
        row = {"id": d.id, "text": d.text, "source": d.source}
        for k in all_meta_keys:
            row[k] = d.metadata.get(k)
        rows.append(row)
    return pd.DataFrame(rows)
