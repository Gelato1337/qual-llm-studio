"""Recipe execution endpoints.

Pattern: fire-and-forget with polling.

  POST /api/execute              — start a recipe run; returns job_id immediately
  GET  /api/execute/{job_id}     — poll status; returns result rows when done

  GET  /api/execute/runs         — list all past run folders
  GET  /api/execute/runs/{run_id} — get results from a completed run folder

The runner is CPU/IO-bound. We kick it off in a background thread so the
HTTP response returns quickly and the React frontend can poll for progress.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import backend.state as state
from app import dispatcher, paths, runs as runs_module
from app.inference import DEFAULT_HOST, OllamaClient
from app.recipe import RecipeError, load_recipe
from app.paths import safe_filename

router = APIRouter()


# ---------------------------------------------------------------------------
# Start a run
# ---------------------------------------------------------------------------


class ExecuteRequest(BaseModel):
    recipe_name: str
    host: str = DEFAULT_HOST
    model: str
    n_parallel: int = 1
    # Optional: sample only the first N rows of the workspace docs.
    sample_n: int = 0


@router.post("")
def start_run(req: ExecuteRequest):
    """Kick off a recipe run in a background thread.

    Returns a job_id for polling. The workspace docs_df is used as the
    dataset — make sure /api/ingest/structure has been called first.
    """
    docs_df = state.workspace.docs_df
    if docs_df is None or len(docs_df) == 0:
        raise HTTPException(
            status_code=400,
            detail="No docs in workspace. Run /api/ingest/structure first.",
        )

    # Find recipe on disk
    recipe_path = _find_recipe_path(req.recipe_name)
    if recipe_path is None:
        raise HTTPException(status_code=404, detail=f"Recipe {req.recipe_name!r} not found")
    try:
        recipe = load_recipe(recipe_path)
    except RecipeError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # Optional row sampling
    work_df = docs_df
    if req.sample_n and req.sample_n < len(docs_df):
        work_df = docs_df.head(req.sample_n).reset_index(drop=True)

    job = state.new_job(
        recipe_name=req.recipe_name,
        model=req.model,
        n_rows=len(work_df),
        n_parallel=req.n_parallel,
    )

    def _run():
        job.status = state.JOB_RUNNING
        try:
            client = OllamaClient(host=req.host)
            run_folder = runs_module.create_run_folder(
                paths.RUNS_DIR, safe_filename(recipe.name), req.model,
            )
            results_df, logs = dispatcher.run(
                work_df, recipe,
                client=client,
                model=req.model,
                save_dir=run_folder,
                ollama_host=req.host,
                n_parallel=req.n_parallel,
            )
            config = {
                "recipe": recipe.name,
                "model": req.model,
                "n_parallel": req.n_parallel,
                "n_input_rows": len(work_df),
                "n_output_rows": len(results_df),
            }
            runs_module.write_results(run_folder, results_df, config, logs)
            job.result_df = results_df
            job.run_folder = str(run_folder)
            job.logs = logs
            job.status = state.JOB_DONE
        except Exception as exc:
            job.error = f"{type(exc).__name__}: {exc}"
            job.status = state.JOB_FAILED

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    return {
        "job_id": job.job_id,
        "status": job.status,
        "recipe_name": job.recipe_name,
        "n_rows": job.n_rows,
    }


# ---------------------------------------------------------------------------
# Poll a job
# ---------------------------------------------------------------------------


@router.get("/{job_id}")
def get_job(job_id: str):
    """Poll job status. When status == 'done', includes result rows."""
    job = state.active_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")

    resp: dict[str, Any] = {
        "job_id": job.job_id,
        "status": job.status,
        "recipe_name": job.recipe_name,
        "model": job.model,
        "n_rows": job.n_rows,
        "run_folder": job.run_folder,
        "error": job.error,
    }
    if job.status == state.JOB_DONE and job.result_df is not None:
        resp["n_output_rows"] = len(job.result_df)
        resp["columns"] = list(job.result_df.columns)
        resp["rows"] = job.result_df.to_dict(orient="records")
    return resp


# ---------------------------------------------------------------------------
# Run history
# ---------------------------------------------------------------------------


@router.get("/runs/list")
def list_all_runs():
    """List all past run folders (excluding workspace)."""
    all_runs = runs_module.list_runs(paths.RUNS_DIR)
    filtered = [r for r in all_runs if not Path(r["folder"]).parent.name.startswith("_")]
    out = []
    for r in filtered:
        cfg = r.get("config") or {}
        out.append({
            "pipeline": r["pipeline"],
            "name": r["name"],
            "folder": r["folder"],
            "recipe": cfg.get("recipe", ""),
            "model": cfg.get("model", ""),
            "n_output_rows": cfg.get("n_output_rows", "?"),
        })
    return {"runs": out}


@router.get("/runs/{pipeline}/{version}")
def get_run_results(pipeline: str, version: str):
    """Get results CSV from a specific run folder as JSON rows."""
    import pandas as pd

    run_folder = paths.RUNS_DIR / pipeline / version
    results_path = run_folder / "results.csv"
    if not results_path.exists():
        raise HTTPException(status_code=404, detail=f"Run {pipeline}/{version} not found")
    try:
        df = pd.read_csv(results_path)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    config_path = run_folder / "config.json"
    config: dict = {}
    if config_path.exists():
        import json
        try:
            config = json.loads(config_path.read_text())
        except Exception:
            pass
    return {
        "pipeline": pipeline,
        "version": version,
        "config": config,
        "n_rows": len(df),
        "columns": list(df.columns),
        "rows": df.to_dict(orient="records"),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_recipe_path(name: str) -> Path | None:
    from app.recipe import load_recipe as _load, RecipeError as _RE
    if not paths.RECIPES_DIR.exists():
        return None
    for p in paths.RECIPES_DIR.rglob("*.json"):
        try:
            r = _load(p)
            if r.name == name:
                return p
        except _RE:
            continue
    return None
