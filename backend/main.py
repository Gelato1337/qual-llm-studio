"""FastAPI backend for Qual LLM Studio.

Start with:
    cd <repo-root>
    uvicorn backend.main:app --reload --port 8000

All routes live under /api. The Gradio app (port 7860) can run in
parallel — this backend is additive, not a replacement yet.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routers import eval, execute, ingest, ollama, recipes, workspace

app = FastAPI(
    title="Qual LLM Studio API",
    description="REST API for the Qual LLM Studio backend logic.",
    version="0.1.0",
)

# Allow the Vite dev server (port 5173) and any localhost origin during
# development. Tighten this in production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",  # fallback if CRA or other dev server
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(eval.router,      prefix="/api/eval",      tags=["eval"])
app.include_router(ollama.router,    prefix="/api/ollama",    tags=["ollama"])
app.include_router(ingest.router, prefix="/api/ingest", tags=["ingest"])
app.include_router(recipes.router, prefix="/api/recipes", tags=["recipes"])
app.include_router(execute.router, prefix="/api/execute", tags=["execute"])
app.include_router(workspace.router, prefix="/api/workspace", tags=["workspace"])


@app.get("/api/health")
def health():
    return {"status": "ok"}
