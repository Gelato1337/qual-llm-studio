# Project Reference: AI-Powered Research & Text Analysis Teaching Tool

This document describes the tech stack, how the pieces connect, the frontend development strategy, and the day-to-day development workflow. It is written for both the human developer returning to this project and for any AI coding agent assisting with development.

---

## What We Are Building

A desktop-friendly web app used as a hands-on teaching tool for AI-powered research and text corpus analysis. The core UI concept is a **drag-and-drop block builder**: students assemble analysis pipelines by connecting modular building blocks (e.g. "load corpus", "run topic model", "visualize output"). The visual aesthetic is clean, light, and approachable for non-technical users.

---

## Tech Stack Overview

### Frontend: React + Vite + Tailwind CSS + React Flow

| Tool | Role |
|---|---|
| **React** | The UI framework. All visible interface elements are React components. |
| **Vite** | The build tool and local dev server. It compiles and serves the React app during development and bundles it for production. Run with `npm run dev`. |
| **Tailwind CSS** | Utility-first CSS framework. Styling is applied directly in JSX using class names (e.g. `className="bg-white rounded-lg shadow p-4"`). No separate CSS files needed for most things. |
| **React Flow** | The drag-and-drop node/edge canvas library. Handles all block rendering, dragging, connecting, and canvas state. |

### Backend: FastAPI (Python)

| Tool | Role |
|---|---|
| **FastAPI** | A Python web framework that exposes the app's AI and NLP logic as HTTP API endpoints. The frontend sends requests to these endpoints and receives results. |
| **Uvicorn** | The server that runs FastAPI locally. Start with `uvicorn main:app --reload`. |

### How They Talk to Each Other

The frontend and backend are two separate processes that communicate over HTTP.

```
[React app in browser]  <-->  HTTP requests (fetch/axios)  <-->  [FastAPI server]
     localhost:5173                                                localhost:8000
```

A typical interaction looks like this:

1. Student drops a "Run Sentiment Analysis" block onto the canvas and clicks Run
2. React collects the pipeline configuration and sends a `POST` request to `http://localhost:8000/analyze`
3. FastAPI receives the request, runs the Python NLP logic, and returns a JSON result
4. React receives the JSON and renders the output in the appropriate output block

During development, both servers run simultaneously in two terminal windows.

---

## Project Structure

```
qual-llm-studio/
│
├── frontend/                        # React + Vite web app (port 5173)
│   ├── src/
│   │   ├── App.jsx                  # Root component — React Flow canvas lives here
│   │   ├── main.jsx                 # Entry point
│   │   ├── index.css                # Tailwind base import
│   │   ├── components/
│   │   │   ├── Header.jsx           # Top bar
│   │   │   ├── Sidebar.jsx          # Block palette (drag to canvas)
│   │   │   └── SettingsPanel.jsx    # Ollama host / model settings overlay
│   │   ├── nodes/                   # One file per React Flow block type
│   │   │   ├── IngestNode.jsx       # Data ingestion block
│   │   │   ├── RecipeNode.jsx       # Recipe selection / prompt block
│   │   │   ├── RunNode.jsx          # Recipe execution block
│   │   │   ├── QuickEvalNode.jsx    # 5-sample alignment check block
│   │   │   └── EvalNode.jsx         # Full eval / advanced block
│   │   └── lib/
│   │       ├── api.js               # Axios helpers — all fetch calls go through here
│   │       └── nodeTypes.js         # Registry mapping type strings → node components
│   ├── index.html
│   ├── vite.config.js
│   ├── tailwind.config.js
│   ├── postcss.config.js
│   └── package.json
│
├── backend/                         # FastAPI REST API (port 8000)
│   ├── main.py                      # App factory, CORS, router registration
│   ├── state.py                     # Shared in-process state (active runs, etc.)
│   ├── requirements.txt             # Backend-only deps (install alongside requirements.txt)
│   └── routers/                     # One file per feature area — thin, delegate to app/
│       ├── ingest.py                # POST /api/ingest/*
│       ├── recipes.py               # GET/POST /api/recipes/*
│       ├── execute.py               # POST /api/execute  (runs a recipe on a dataset)
│       ├── eval.py                  # POST /api/eval/*
│       ├── ollama.py                # GET /api/ollama/* (proxy / status)
│       └── workspace.py             # GET/POST /api/workspace
│
├── app/                             # Legacy Gradio UI + shared core logic
│   ├── main.py                      # Gradio app entry point (python -m app.main)
│   ├── inference.py                 # Ollama HTTP client
│   ├── recipe.py                    # Recipe schema, render_as_text / parse_from_text
│   ├── runner_custom.py             # Parallel prompt+schema runner
│   ├── dispatcher.py                # Picks runner, applies post-processing
│   ├── json_parse.py                # Multi-strategy JSON extraction
│   ├── grounding.py                 # rapidfuzz quote verification
│   ├── prompting.py                 # Placeholder substitution ({{col}} and legacy {col})
│   ├── resources.py                 # Adaptive parallelism (SLURM / GPU / CPU detection)
│   ├── workspace.py                 # Disk-backed UI state (runs/_workspace/)
│   ├── paths.py                     # Filesystem layout constants
│   ├── llm_state.py                 # Connection status helpers
│   ├── errors.py                    # Error toasts + log file
│   ├── runs.py                      # Run folder management
│   ├── drive.py                     # Google Drive save (Colab only)
│   ├── ui_data_tab.py               # Gradio Data tab
│   ├── ui_chat_tab.py               # Gradio Chat/Recipes tab
│   ├── ui_runs_tab.py               # Gradio Runs tab
│   ├── ui_quick_eval_tab.py         # Gradio Quick Eval tab
│   ├── ui_eval_tab.py               # Gradio Advanced/Eval tab
│   ├── eval/                        # Eval package
│   │   ├── judges.py                # Human + LLM judge logic
│   │   ├── optimizer.py             # DSPy BootstrapFewShot optimizer
│   │   ├── sampling.py              # Stratified / targeted sampling
│   │   ├── session.py               # Eval session lifecycle
│   │   └── storage.py               # Label persistence
│   └── ingest/                      # Ingestion package
│       ├── pipeline.py              # Load → structure → segment orchestration
│       ├── sources.py               # File / HuggingFace / paste loaders
│       ├── structure.py             # Column picker, multi-column join
│       ├── json_loader.py           # JSON path picker (e.g. [*].data)
│       ├── regex_helper.py          # Regex-from-examples helper
│       └── segmenters/              # Pluggable segmentation strategies
│           ├── base.py
│           ├── none_seg.py
│           ├── char_length.py
│           ├── paragraph.py
│           ├── regex_seg.py
│           └── llm_natural.py
│
├── datasets/                        # Ingested CSVs saved from the Data tab
├── results/                         # One CSV per recipe run
├── recipes/
│   └── custom/                      # All JSON recipes (8 starters + user-created)
├── runs/
│   └── _workspace/                  # Disk-backed UI state (state.json, docs.parquet, …)
│
├── notebook/
│   └── QualLLMStudio_Colab.ipynb    # One-click Colab entry point
├── scripts/
│   └── lumi_example.sh              # Template Slurm script for LUMI HPC
│
├── requirements.txt                 # Core Python deps (app/ + Gradio)
├── README.md
└── project-reference.md             # This file
```

---

## Frontend Development Strategy

### Phase 1: Static Visual Layer First

Before wiring anything to the backend, build the UI as if it were a static mockup. Get the layout, the block designs, and the canvas feel right. This makes it much easier to iterate on the visual design without breaking logic.

Key things to build in this phase:
- The main canvas area (React Flow with placeholder nodes)
- The sidebar listing available block types
- A few representative block designs (input block, processing block, output block)
- The overall page shell (header, layout, color theme)

### Phase 2: Block System

Define your block types as custom React Flow nodes. Each block type should be its own component in `src/nodes/`. A block component is responsible for:
- Its visual appearance (using Tailwind classes)
- Its configuration UI (e.g. a dropdown inside the block for selecting a model)
- Its input/output connection handles (React Flow `Handle` components)

Keep block components small and focused. One file per block type.

### Phase 3: Backend Integration

Once the visual layer is stable, connect blocks to FastAPI endpoints. The general pattern per block type:

```js
// When a block is executed, POST its config to the relevant endpoint
const result = await fetch('http://localhost:8000/analyze', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ text: inputText, model: selectedModel })
});
const data = await result.json();
// Pass data to the next block or render in output block
```

Use a loading state on each block so students can see when processing is happening.

### Phase 4: Pipeline Execution Logic

Once individual blocks work in isolation, build the logic that walks the React Flow graph and executes blocks in order, passing outputs from one block as inputs to the next. React Flow exposes the full graph as `nodes` and `edges` arrays, which you can traverse to determine execution order.

---

## Design Guidelines

These should be followed by any agent generating frontend code for this project.

- **Aesthetic**: Clean, light, minimal. White or very light gray backgrounds. Generous whitespace. Subtle shadows on cards and blocks. No dark mode required initially.
- **Typography**: A clean, readable sans-serif. Prioritize legibility over personality.
- **Color**: One primary accent color for interactive elements (buttons, handles, active states). Neutral grays for everything else. Avoid decorative color.
- **Blocks**: Should look like distinct, self-contained cards. Rounded corners. A visible header with the block name. Connection handles on left/right edges.
- **Tailwind approach**: Use Tailwind utility classes directly in JSX. Avoid writing custom CSS unless Tailwind cannot achieve the goal. Use `@apply` in a CSS file only for highly repeated patterns.

---

## Development Workflow

### Starting the project each session

```bash
# Terminal 1: start the frontend
cd frontend
npm run dev
# App available at http://localhost:5173

# Terminal 2: start the backend (run from repo root, not backend/)
uvicorn backend.main:app --reload --port 8001
# API available at http://localhost:8001
# API docs (auto-generated) at http://localhost:8001/docs
```

### Iterating with AI assistance (Claude Code or similar)

A productive loop for each new feature:

1. **Sketch in Figma** (optional but helpful for blocks and layout): establish colors, proportions, and component shape before coding
2. **Write a clear prompt** describing what you want: the component name, its purpose, its props, and any specific behavior. Reference this document for context on the stack.
3. **Generate the component**, review it, and drop it into the right directory
4. **Test visually** in the browser before wiring to the backend
5. **Write the FastAPI endpoint** once the frontend shape is clear, so you know exactly what JSON the frontend will send and expect back

### Adding a new block type: checklist

- [ ] Create `src/nodes/BlockName.jsx` with the visual component and handles
- [ ] Register it in the `nodeTypes` object passed to `<ReactFlow />`
- [ ] Add it to the sidebar list so users can drag it onto the canvas
- [ ] Create the corresponding FastAPI endpoint in `backend/routers/`
- [ ] Connect the block's execute action to that endpoint

### FastAPI endpoint conventions

- Use `POST` for all analysis/processing endpoints
- Accept JSON body, return JSON response
- Always include an `error` field in responses so the frontend can handle failures gracefully
- Keep endpoint logic thin: call a function from `services/` rather than putting logic directly in the route

```python
# Example endpoint pattern
@router.post("/analyze/sentiment")
async def sentiment_analysis(request: SentimentRequest):
    try:
        result = services.sentiment.run(request.text, request.model)
        return {"success": True, "result": result}
    except Exception as e:
        return {"success": False, "error": str(e)}
```

---

## Key Dependencies

### Frontend (`frontend/package.json`)
```json
{
  "dependencies": {
    "react": "^18",
    "react-dom": "^18",
    "reactflow": "^11",
    "axios": "^1"
  },
  "devDependencies": {
    "vite": "^5",
    "@vitejs/plugin-react": "^4",
    "tailwindcss": "^3",
    "autoprefixer": "^10",
    "postcss": "^8"
  }
}
```

### Backend (`backend/requirements.txt`)
```
fastapi
uvicorn[standard]
pydantic
```
Add NLP/AI libraries here as needed (e.g. `transformers`, `scikit-learn`, `spacy`).

---

## Notes for AI Coding Agents

- This project uses **React with JSX**, not TypeScript. Do not introduce TypeScript unless explicitly asked.
- All styling uses **Tailwind CSS utility classes**. Do not write vanilla CSS or use CSS modules unless Tailwind cannot achieve the result.
- React Flow nodes must be defined as custom components and registered in the `nodeTypes` object. Do not render nodes as plain divs outside of the React Flow node system.
- FastAPI routes should be thin. Business logic goes in `backend/services/`.
- The frontend and backend are **separate applications**. Do not attempt to serve the React app from FastAPI.
- When in doubt about the aesthetic, err on the side of **more whitespace and less decoration**.
