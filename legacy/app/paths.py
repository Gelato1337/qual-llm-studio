"""Filesystem layout for the app.

Three top-level directories the app manages:

  datasets/   final ingested CSVs the researcher saves from the Data tab
  results/    one CSV per recipe run, plus eval columns appended later
  recipes/    JSON recipes

Plus runs/ which still holds run metadata (config.json, logs) for
debugging — but the canonical results live in results/ now.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATASETS_DIR = REPO_ROOT / "datasets"
RESULTS_DIR = REPO_ROOT / "results"
RECIPES_DIR = REPO_ROOT / "recipes"
RUNS_DIR = REPO_ROOT / "runs"
WORKSPACE_DIR = RUNS_DIR / "_workspace"


def ensure_dirs() -> None:
    """Create the three directories if absent. Idempotent."""
    for d in (DATASETS_DIR, RESULTS_DIR, RECIPES_DIR, RUNS_DIR, WORKSPACE_DIR):
        d.mkdir(parents=True, exist_ok=True)


def safe_filename(name: str) -> str:
    """Turn a free-form name into a filesystem-safe slug."""
    name = name.strip()
    safe = re.sub(r"[^\w\-]+", "_", name)
    return safe.strip("_") or "untitled"


def build_results_filename(dataset_name: str, recipe_name: str) -> str:
    """Build a deterministic-ish results filename like
    `<dataset>__<recipe>__YYYYMMDD_HHMMSS.csv`."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{safe_filename(dataset_name)}__{safe_filename(recipe_name)}__{ts}.csv"


def list_datasets() -> list[Path]:
    """All saved dataset CSVs."""
    if not DATASETS_DIR.exists():
        return []
    return sorted(DATASETS_DIR.glob("*.csv"))


def list_results() -> list[Path]:
    """All results CSVs."""
    if not RESULTS_DIR.exists():
        return []
    return sorted(RESULTS_DIR.glob("*.csv"))
