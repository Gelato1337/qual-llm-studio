"""
Versioned run folders.

Every pipeline execution writes into runs/{pipeline}/v{N}_{model}/ with:
  * config.json       — exact settings used
  * results.csv       — the structured output
  * results.json      — same data, JSON form
  * raw_responses.jsonl — one row per LLM call (for debugging / DSPy later)

Versioning by directory rather than timestamp keeps things ordered and
gives us a stable URL to refer back to a run.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


def _safe(s: str) -> str:
    """Filesystem-safe form of model names like `qwen3.5:9b`."""
    return s.replace(":", "_").replace("/", "_")


def next_version(runs_root: str | Path) -> int:
    runs_root = Path(runs_root)
    if not runs_root.exists():
        return 1
    versions = []
    for p in runs_root.iterdir():
        m = re.match(r"v(\d+)", p.name)
        if m:
            versions.append(int(m.group(1)))
    return max(versions) + 1 if versions else 1


def create_run_folder(runs_root: str | Path, pipeline: str, model: str) -> Path:
    runs_root = Path(runs_root)
    pipeline_root = runs_root / pipeline
    pipeline_root.mkdir(parents=True, exist_ok=True)
    version = next_version(pipeline_root)
    folder = pipeline_root / f"v{version}_{_safe(model)}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def write_results(
    folder: str | Path,
    df: pd.DataFrame,
    config: dict,
    raw_responses: list[dict] | None = None,
):
    """Write the standard set of artefacts to a run folder."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)

    df.to_csv(folder / "results.csv", index=False)

    # JSON output mirrors CSV but with config metadata for portability
    payload = {
        "config": config,
        "timestamp": datetime.now().isoformat(),
        "n_rows": len(df),
        "rows": df.to_dict(orient="records"),
    }
    (folder / "results.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False))

    (folder / "config.json").write_text(json.dumps(config, indent=2))

    if raw_responses is not None:
        with (folder / "raw_responses.jsonl").open("w", encoding="utf-8") as f:
            for row in raw_responses:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")


def list_runs(runs_root: str | Path, pipeline: str | None = None) -> list[dict]:
    """Enumerate prior runs. If pipeline given, scope to it; else all."""
    runs_root = Path(runs_root)
    if not runs_root.exists():
        return []

    targets: list[Path] = []
    if pipeline:
        target = runs_root / pipeline
        if target.exists():
            targets = [p for p in target.iterdir() if p.is_dir()]
    else:
        for pl in runs_root.iterdir():
            if pl.is_dir():
                targets.extend(p for p in pl.iterdir() if p.is_dir())

    out = []
    for folder in sorted(targets):
        cfg_path = folder / "config.json"
        cfg: dict[str, Any] = {}
        if cfg_path.exists():
            try:
                cfg = json.loads(cfg_path.read_text())
            except json.JSONDecodeError:
                pass
        out.append(
            {
                "folder": str(folder),
                "pipeline": folder.parent.name,
                "name": folder.name,
                "config": cfg,
            }
        )
    return out
