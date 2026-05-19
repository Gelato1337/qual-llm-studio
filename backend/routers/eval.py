"""Eval session endpoints.

Each session wraps one completed recipe run folder.  Sessions persist as
sqlite databases at  runs/<pipeline>/<version>/eval/judgments.db  so they
survive backend restarts.

Routes:
  POST /api/eval/sessions                       — create / resume a session
  GET  /api/eval/sessions/{sid}/sample          — pick N rows for this cycle
  POST /api/eval/sessions/{sid}/judge           — record one human judgment
  POST /api/eval/sessions/{sid}/llm             — run LLM judge (sync, ~5 rows)
  GET  /api/eval/sessions/{sid}/stats           — agreement metrics
  GET  /api/eval/sessions/{sid}/view            — rows with their judgments
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import backend.state as state
from app import paths
from app.eval.session import EvalSession
from app.inference import DEFAULT_HOST, OllamaClient

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sid(pipeline: str, version: str) -> str:
    return f"{pipeline}__{version}"


def _require(sid: str) -> EvalSession:
    if sid not in state.eval_sessions:
        raise HTTPException(404, f"No session for {sid!r}. POST /api/eval/sessions first.")
    return state.eval_sessions[sid]


def _load_results(run_folder: Path) -> pd.DataFrame:
    p = run_folder / "results.csv"
    if not p.exists():
        raise HTTPException(422, f"results.csv not found in {run_folder.name}")
    return pd.read_csv(p)


def _load_recipe_dict(run_folder: Path) -> dict:
    cfg_path = run_folder / "config.json"
    if not cfg_path.exists():
        return {}
    try:
        cfg = json.loads(cfg_path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    recipe_name = cfg.get("recipe", "")
    if not recipe_name:
        return cfg
    for p in paths.RECIPES_DIR.rglob("*.json"):
        try:
            data = json.loads(p.read_text())
            if data.get("name") == recipe_name:
                return data
        except (json.JSONDecodeError, OSError):
            continue
    return {"name": recipe_name}


# ---------------------------------------------------------------------------
# Create / resume
# ---------------------------------------------------------------------------


class SessionRequest(BaseModel):
    pipeline: str
    version:  str


@router.post("/sessions")
def create_session(req: SessionRequest):
    """Create or resume an eval session for a completed run."""
    run_folder = paths.RUNS_DIR / req.pipeline / req.version
    if not run_folder.exists():
        raise HTTPException(404, f"Run {req.pipeline}/{req.version} not found")

    sid = _sid(req.pipeline, req.version)

    if sid not in state.eval_sessions:
        results_df  = _load_results(run_folder)
        recipe_dict = _load_recipe_dict(run_folder)
        session = EvalSession(
            run_folder=run_folder,
            results_df=results_df,
            recipe_dict=recipe_dict,
        )
        # Assign all rows to dev partition (idempotent via sqlite INSERT OR REPLACE)
        session.partition()
        state.eval_sessions[sid] = session

    s = state.eval_sessions[sid]
    non_internal_cols = [c for c in s.results_df.columns if not c.startswith("_")]
    return {
        "session_id":    sid,
        "n_rows":        len(s.results_df),
        "label_options": s.human_label_options,
        "has_llm_judge": s.has_llm_judge,
        "columns":       non_internal_cols,
    }


# ---------------------------------------------------------------------------
# Sample
# ---------------------------------------------------------------------------


@router.get("/sessions/{sid}/sample")
def get_sample(sid: str, n: int = 5):
    """Pick N rows for the next eval cycle (stratified when possible)."""
    s = _require(sid)
    row_ids, strategy, cycle = s.sample(n, partition="dev", mode="auto")
    if not row_ids:
        return {"row_ids": [], "rows": [], "cycle": cycle, "strategy": strategy}
    view_df = s.get_eval_view(row_ids)
    return {
        "row_ids":  row_ids,
        "rows":     view_df.to_dict(orient="records"),
        "cycle":    cycle,
        "strategy": strategy,
    }


# ---------------------------------------------------------------------------
# Human judgment
# ---------------------------------------------------------------------------


class JudgeRequest(BaseModel):
    row_id:  str
    label:   str
    comment: str = ""


@router.post("/sessions/{sid}/judge")
def record_judgment(sid: str, req: JudgeRequest):
    """Upsert a human judgment for one row."""
    s = _require(sid)
    s.record_human_judgment(req.row_id, req.label, comment=req.comment)
    return {"ok": True, "row_id": req.row_id, "label": req.label}


# ---------------------------------------------------------------------------
# LLM judge
# ---------------------------------------------------------------------------


class LLMRequest(BaseModel):
    row_ids: list[str]
    host:    str = DEFAULT_HOST
    model:   str


@router.post("/sessions/{sid}/llm")
def run_llm_judge(sid: str, req: LLMRequest):
    """Run the LLM judge on the given row IDs (synchronous, suitable for ≤10 rows)."""
    s = _require(sid)
    client = OllamaClient(host=req.host)
    if not client.is_alive(timeout=3.0):
        raise HTTPException(503, f"Ollama unreachable at {req.host}")
    try:
        verdicts = s.run_llm_judge(req.row_ids, client=client, model=req.model)
    except Exception as exc:
        raise HTTPException(500, str(exc))
    return {"verdicts": verdicts}


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


@router.get("/sessions/{sid}/stats")
def get_stats(sid: str):
    """Return human-vs-LLM agreement metrics for this session."""
    s = _require(sid)
    st = s.agreement_metrics()
    return {
        "n_compared":       st.n_compared,
        "exact_match_rate": st.exact_match_rate,
        "cohens_kappa":     st.cohens_kappa,
        # Serialize tuple keys as "human/llm" strings
        "confusion": {f"{h}/{l}": n for (h, l), n in st.confusion.items()},
    }


# ---------------------------------------------------------------------------
# View rows with judgments
# ---------------------------------------------------------------------------


@router.get("/sessions/{sid}/view")
def get_view(sid: str, row_ids: str = ""):
    """Return result rows with any collected human/LLM label columns.

    `row_ids` — comma-separated. If omitted, returns the most recent cycle.
    """
    s = _require(sid)
    if row_ids:
        ids = [r.strip() for r in row_ids.split(",") if r.strip()]
    else:
        prev_cycle = s.store.next_cycle() - 1
        ids = s.store.samples_for_cycle(prev_cycle) if prev_cycle > 0 else []
    if not ids:
        return {"rows": []}
    df = s.get_eval_view(ids)
    return {"rows": df.to_dict(orient="records")}
