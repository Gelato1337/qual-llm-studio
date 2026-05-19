"""Global in-process state for the FastAPI backend.

Mirrors the role of Gradio's gr.State objects in the old app, but as
plain Python module-level singletons. Single-user desktop tool — no
multi-session concerns.

Components:
  * workspace  — Workspace instance, disk-backed (host, model, docs, etc.)
  * loaded_sources — LoadedSource list from the most recent ingest/upload
                     (held in memory; cleared on new uploads)
  * active_jobs — running/completed execute jobs keyed by job_id
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from app.eval.session import EvalSession
from app.ingest.pipeline import LoadedSource
from app.paths import WORKSPACE_DIR, ensure_dirs
from app.workspace import Workspace

# ---------------------------------------------------------------------------
# Workspace (disk-backed)
# ---------------------------------------------------------------------------

ensure_dirs()
workspace: Workspace = Workspace(WORKSPACE_DIR).load()


# ---------------------------------------------------------------------------
# In-memory ingest state
# ---------------------------------------------------------------------------

loaded_sources: list[LoadedSource] = []


# ---------------------------------------------------------------------------
# Job registry (execute endpoint)
# ---------------------------------------------------------------------------

JOB_PENDING = "pending"
JOB_RUNNING = "running"
JOB_DONE = "done"
JOB_FAILED = "failed"


@dataclass
class Job:
    job_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    status: str = JOB_PENDING
    recipe_name: str = ""
    model: str = ""
    n_rows: int = 0
    n_parallel: int = 1
    result_df: pd.DataFrame | None = None
    run_folder: str = ""
    error: str = ""
    logs: list[dict] = field(default_factory=list)


active_jobs: dict[str, Job] = {}

# Eval sessions keyed by "{pipeline}__{version}"
eval_sessions: dict[str, EvalSession] = {}


def new_job(**kwargs) -> Job:
    job = Job(**kwargs)
    active_jobs[job.job_id] = job
    return job
