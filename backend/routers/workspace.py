"""Workspace state endpoints.

Routes:
  GET  /api/workspace          — return current state (host, model, docs summary)
  PUT  /api/workspace/settings — update host + model; persists to disk
  POST /api/workspace/reset    — wipe workspace state + docs (destructive)
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

import backend.state as state
from app.inference import DEFAULT_HOST

router = APIRouter()


# ---------------------------------------------------------------------------
# GET state
# ---------------------------------------------------------------------------


@router.get("")
def get_workspace():
    """Return the current workspace state."""
    ws = state.workspace
    docs_df = ws.docs_df
    return {
        "host": ws.state.host or DEFAULT_HOST,
        "model": ws.state.model or "",
        "docs": {
            "n_rows": len(docs_df) if docs_df is not None else 0,
            "columns": list(docs_df.columns) if docs_df is not None else [],
        },
        "sources_meta": [
            {
                "name": m.name,
                "kind": m.kind,
                "n_rows": m.n_rows,
                "columns": m.columns,
            }
            for m in ws.state.sources_meta
        ],
        "loaded_sources_count": len(state.loaded_sources),
    }


# ---------------------------------------------------------------------------
# PUT settings
# ---------------------------------------------------------------------------


class SettingsRequest(BaseModel):
    host: str = DEFAULT_HOST
    model: str = ""
    output_language: str = "English"


@router.put("/settings")
def update_settings(req: SettingsRequest):
    """Update Ollama host + model. Persists to workspace on disk."""
    ws = state.workspace
    ws.state.host = req.host or DEFAULT_HOST
    ws.state.model = req.model or ""
    ws.save()
    return {
        "saved": True,
        "host": ws.state.host,
        "model": ws.state.model,
    }


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------


@router.post("/reset")
def reset_workspace():
    """Wipe all workspace state and in-memory sources. Destructive."""
    state.workspace.reset()
    state.loaded_sources = []
    state.active_jobs.clear()
    return {"reset": True}
