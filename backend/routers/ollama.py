"""Ollama connection, model listing, and model pull endpoints.

Routes:
  GET  /api/ollama/status          — ping Ollama, return version + models
  GET  /api/ollama/models          — list pulled models
  POST /api/ollama/pull            — pull a model (streams JSON-lines via SSE)
  GET  /api/ollama/resources       — detect GPU/CPU resources
"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.inference import DEFAULT_HOST, OllamaClient
from app import resources as _resources

router = APIRouter()


def _client(host: str | None = None) -> OllamaClient:
    return OllamaClient(host=host or DEFAULT_HOST)


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


@router.get("/status")
def get_status(host: str = DEFAULT_HOST):
    """Ping Ollama. Returns version string and list of pulled models."""
    client = _client(host)
    if not client.is_alive(timeout=3.0):
        return {"alive": False, "version": None, "models": [], "host": host}
    version = client.version()
    try:
        models = client.list_models()
    except Exception:
        models = []
    return {"alive": True, "version": version, "models": models, "host": host}


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


@router.get("/models")
def list_models(host: str = DEFAULT_HOST):
    client = _client(host)
    if not client.is_alive(timeout=3.0):
        raise HTTPException(status_code=503, detail=f"Ollama unreachable at {host}")
    try:
        return {"models": client.list_models()}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


# ---------------------------------------------------------------------------
# Pull
# ---------------------------------------------------------------------------


class PullRequest(BaseModel):
    model: str
    host: str = DEFAULT_HOST


@router.post("/pull")
def pull_model(req: PullRequest):
    """Pull a model. Streams newline-delimited JSON progress events.

    Each line is a JSON object. The final line has `_qls_status` set to
    "ok" or "failed". Example event sequence:

        {"status": "pulling manifest"}
        {"status": "downloading", "completed": 1024, "total": 4096}
        ...
        {"_qls_status": "ok", "model": "qwen3:4b"}
    """
    client = _client(req.host)
    if not client.is_alive(timeout=3.0):
        raise HTTPException(status_code=503, detail=f"Ollama unreachable at {req.host}")

    def event_stream():
        for event in client.pull_and_verify(req.model):
            yield json.dumps(event) + "\n"

    return StreamingResponse(event_stream(), media_type="application/x-ndjson")


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------


@router.get("/resources")
def detect_resources(host: str = DEFAULT_HOST):
    """Detect available compute resources (GPU/CPU) and suggested parallelism."""
    res = _resources.detect(ollama_host=host)
    return {
        "backend": res.backend,
        "n_gpus": res.n_gpus,
        "cpu_count": res.cpu_count,
        "suggested_parallelism": res.suggested_parallelism,
        "detection_notes": res.detection_notes,
    }
