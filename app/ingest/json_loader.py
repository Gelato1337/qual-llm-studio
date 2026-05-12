"""JSON ingestion with a small path-picker.

Real-world JSON datasets are nested in ways pandas can't auto-flatten.
Example user data:

    [
        {"data": {"entity": "X", "type": "org", "hierarchies": ["UNK"]}},
        {"data": {"entity": "Y", ...}}
    ]

The user describes where the records live with a tiny path syntax:

    .                  -> the root (must be a list of objects)
    [*].data           -> for each item in root array, descend to `data`
    .records           -> root["records"] (must be a list)
    .results.items     -> root["results"]["items"]
    .results.items[*]  -> same, with explicit per-item iteration

Whatever the path resolves to MUST be a list of objects. We then flatten
each object one level (so {"a": {"b": 1}} stays as `a` column with a
JSON-encoded value — researchers can still pick fields they care about).

Output is a pandas DataFrame, which the rest of the pipeline (structure,
segmentation) handles normally.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Union

import pandas as pd

# Path syntax tokens
_TOKEN = re.compile(r"\[\*\]|\.\w+|\.")


class JsonPathError(ValueError):
    pass


def parse_path(path: str) -> list[str]:
    """Split a tiny dot/bracket path string into navigation tokens.

    Tokens:
        '[*]'      -> map: for each item in current array, continue
        '.<name>'  -> get the named key
        '.'        -> noop (used to write '.' for "the root")

    Examples:
        '.'              -> []
        '.data'          -> ['.data']
        '[*].data'       -> ['[*]', '.data']
        '.results.items[*]' -> ['.results', '.items', '[*]']
    """
    if not path or path == ".":
        return []
    tokens = _TOKEN.findall(path)
    consumed = "".join(tokens)
    if consumed != path:
        raise JsonPathError(
            f"could not parse JSON path {path!r}; "
            "use '.', '.field', '[*]', or combinations"
        )
    # Drop any bare '.' tokens that aren't followed by a name (purely cosmetic)
    return [t for t in tokens if t != "."]


def apply_path(data: Any, tokens: list[str]) -> Any:
    """Walk `data` according to the parsed path tokens. Returns the value
    at the path, or raises JsonPathError if a step doesn't fit the data shape.
    """
    current = data
    for tok in tokens:
        if tok == "[*]":
            if not isinstance(current, list):
                raise JsonPathError(
                    f"[*] requires a list, got {type(current).__name__}"
                )
            # Map the rest of the tokens — we don't actually do that here;
            # apply_path is a single-result function. Top-level resolve()
            # handles the [*] semantics.
            return current  # caller knows to iterate
        if tok.startswith("."):
            key = tok[1:]
            if not isinstance(current, dict):
                raise JsonPathError(
                    f"key '{key}' requires a dict, got {type(current).__name__}"
                )
            if key not in current:
                raise JsonPathError(
                    f"key '{key}' not found; available: {list(current.keys())}"
                )
            current = current[key]
        else:
            raise JsonPathError(f"unexpected token {tok!r}")
    return current


def resolve(data: Any, path: str) -> list[dict]:
    """Resolve `path` against `data` and return a list-of-dicts.

    Handles [*] semantics: at each [*], iterate the current list and
    apply the remaining path to each item, collecting the results.
    """
    tokens = parse_path(path)

    # Walk tokens, expanding [*] when we see them
    nodes: list[Any] = [data]
    for tok in tokens:
        if tok == "[*]":
            new_nodes = []
            for node in nodes:
                if not isinstance(node, list):
                    raise JsonPathError(
                        f"[*] requires a list, got {type(node).__name__}"
                    )
                new_nodes.extend(node)
            nodes = new_nodes
        elif tok.startswith("."):
            key = tok[1:]
            new_nodes = []
            for node in nodes:
                if not isinstance(node, dict):
                    raise JsonPathError(
                        f"key '{key}' requires a dict, got {type(node).__name__}"
                    )
                if key not in node:
                    raise JsonPathError(
                        f"key '{key}' not found; available: {list(node.keys())}"
                    )
                new_nodes.append(node[key])
            nodes = new_nodes

    # If the user gave a path with no [*] but the result is a list, treat
    # it as the iterable. This makes "."  natural for a top-level array.
    if len(nodes) == 1 and isinstance(nodes[0], list):
        nodes = nodes[0]

    # Final result must be a list of dicts. Reject lists of strings/numbers
    # politely — they belong as a one-column tabular import.
    if not all(isinstance(n, dict) for n in nodes):
        non_dict = [type(n).__name__ for n in nodes if not isinstance(n, dict)]
        raise JsonPathError(
            f"path must resolve to a list of objects, got items of type {non_dict[:3]}. "
            "If you have a list of strings, wrap each in an object first."
        )
    return nodes


def to_dataframe(records: list[dict]) -> pd.DataFrame:
    """Flatten a list of dicts to a DataFrame. Nested values become
    JSON-encoded strings so they survive parquet roundtrips."""
    if not records:
        return pd.DataFrame()
    rows = []
    for r in records:
        flat = {}
        for k, v in r.items():
            if isinstance(v, (dict, list)):
                flat[k] = json.dumps(v, ensure_ascii=False)
            else:
                flat[k] = v
        rows.append(flat)
    return pd.DataFrame(rows)


def load_json(
    path: str | Path | None = None,
    *,
    text: str | None = None,
    json_path: str = ".",
) -> pd.DataFrame:
    """Load JSON from disk or from a string. Returns a DataFrame.

    `path` is a file path; `text` is raw JSON content. Provide one.
    `json_path` is the path-picker syntax (default: root array).
    """
    if path is None and text is None:
        raise ValueError("provide either `path` or `text`")
    if path is not None:
        path = Path(path)
        ext = path.suffix.lower()
        if ext == ".jsonl":
            # one object per line
            records = []
            with path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    records.append(json.loads(line))
            data = records
        else:
            data = json.loads(path.read_text(encoding="utf-8"))
    else:
        data = json.loads(text)

    records = resolve(data, json_path)
    return to_dataframe(records)


def suggest_path(data: Any) -> str:
    """Best-guess at where the records are in a fresh JSON file.

    Heuristics:
      - If root is a list of dicts, return '.'
      - If root is a dict and exactly one of its values is a non-empty list of dicts,
        return that key
      - Otherwise return '.'
    """
    if isinstance(data, list) and data and all(isinstance(d, dict) for d in data):
        return "."
    if isinstance(data, dict):
        list_keys = [
            k for k, v in data.items()
            if isinstance(v, list) and v and all(isinstance(x, dict) for x in v)
        ]
        if len(list_keys) == 1:
            return f".{list_keys[0]}"
    return "."


def peek(path: str | Path) -> dict:
    """Return diagnostics about a JSON file without doing full ingestion.

    Useful for the UI to show the user the shape of their file and a
    suggested path. Returns:
      {
        'kind': 'list' | 'dict' | 'jsonl' | 'other',
        'sample': str,                    # truncated repr
        'top_level_keys': list[str],      # for dicts
        'suggested_path': str,
      }
    """
    path = Path(path)
    ext = path.suffix.lower()
    if ext == ".jsonl":
        records = []
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    break
                if len(records) >= 3:
                    break
        return {
            "kind": "jsonl",
            "sample": json.dumps(records[:1], indent=2, ensure_ascii=False)[:1000],
            "top_level_keys": list(records[0].keys()) if records and isinstance(records[0], dict) else [],
            "suggested_path": ".",
        }
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    if isinstance(data, list):
        return {
            "kind": "list",
            "sample": json.dumps(data[:2], indent=2, ensure_ascii=False)[:1000],
            "top_level_keys": (
                list(data[0].keys()) if data and isinstance(data[0], dict) else []
            ),
            "suggested_path": suggest_path(data),
        }
    if isinstance(data, dict):
        return {
            "kind": "dict",
            "sample": json.dumps(
                {k: data[k] for k in list(data.keys())[:5]},
                indent=2, ensure_ascii=False,
            )[:1000],
            "top_level_keys": list(data.keys()),
            "suggested_path": suggest_path(data),
        }
    return {
        "kind": "other",
        "sample": repr(data)[:1000],
        "top_level_keys": [],
        "suggested_path": ".",
    }
