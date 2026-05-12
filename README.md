# Qual LLM Studio

Workshop tool for running LLM-powered analyses on qualitative data. No-code UI on top of a generic prompt+schema runner. Works in Colab, locally, and on LUMI.

## Quick start

**Colab:** open `notebook/QualLLMStudio_Colab.ipynb`, run all cells, click the proxied URL.

**Local:**
```bash
pip install -r requirements.txt
ollama serve   # in another terminal
python -m app.main
```

## How it works

Pick or write a recipe, load some data, click run, get a CSV with structured outputs you can statistically analyze.

A recipe is a JSON file with:
- A free-form prompt template (with `{{column_name}}` placeholders)
- An output schema (list of fields with types and constraints)
- Optional retry policy and post-processing steps

Each row of the dataset is processed by:
1. Rendering the prompt with the row's column values
2. Calling the local LLM (Ollama)
3. Parsing the JSON response
4. Validating against the schema
5. Retrying with feedback if invalid (up to N times)

Rows are processed concurrently — the Runs tab auto-detects your hardware (CPU / 1 GPU / N GPUs) and picks an appropriate thread count.

## Tab layout

1. **Data** — load files / paste / HuggingFace, structure tabular sources, segment, save as a dataset CSV
2. **Chat / Recipes** — free chat OR pick a recipe and test it on a sample row. Recipe authoring lives here. Recipes shown as readable text, not JSON.
3. **Runs** — pick a saved dataset + a recipe, click Run. Results land in `results/` as a CSV.
4. **Quick Eval** — pick a results CSV, look at 5 random samples, label each ✓/✗, get an alignment %.
5. **Advanced** — full eval (devset / testset, stratified sampling, LLM-as-judge, agreement metrics, DSPy optimizer)
6. **Settings** — Ollama host + default model + per-task overrides + compute resource detection

## Repository layout

```
qual-llm-studio/
  notebook/QualLLMStudio_Colab.ipynb   # one-click Colab entry
  scripts/lumi_example.sh              # template Slurm script
  datasets/                            # final ingested CSVs (saved from Data tab)
  results/                             # one CSV per recipe run
  recipes/                             # JSON recipes
    custom/*.json                      # all recipes (no more gabriel/ subdir)
  runs/                                # diagnostic run folders + workspace state
    _workspace/                        # disk-backed UI state
  app/
    main.py                # Gradio UI assembly
    paths.py               # filesystem layout helpers
    workspace.py           # disk-backed UI state
    errors.py              # error toasts + log file
    llm_state.py           # connection status helpers
    resources.py           # adaptive parallelism detection
    recipe.py              # Recipe schema + render_as_text/parse_from_text
    runner_custom.py       # generic prompt+schema runner with parallel execution
    dispatcher.py          # picks the runner, applies post-processing
    json_parse.py          # multi-strategy JSON extraction
    grounding.py           # rapidfuzz quote verification
    prompting.py           # placeholder substitution (legacy + {{column}})
    runs.py                # run folder management
    drive.py               # Google Drive save (Colab)
    inference.py           # Ollama client
    ui_data_tab.py
    ui_chat_tab.py         # chat + recipe maker
    ui_runs_tab.py         # dataset + recipe + run + parallelism
    ui_quick_eval_tab.py   # 5-sample alignment check
    ui_eval_tab.py         # full eval (Advanced tab)
    eval/                  # eval package
    ingest/                # ingestion package
  requirements.txt
```

## Recipe format

A recipe is a JSON file. The Chat tab renders it as human-readable text and parses your edits back. You rarely need to edit JSON by hand.

Custom recipe (text view, what the Chat tab shows):

```
# theme_grounded

> Extract themes with summaries and supporting quotes; verify quotes via fuzzy match.

## PROMPT
Extract the main themes from this text.

For each theme return:
- "theme": short name
- "summary": 1-2 sentence summary
- "quote": EXACT word-for-word quote
- "keywords": 3-5 relevant keywords

Return JSON: {"themes": [ ... ]}

TEXT:
{{text}}

## OUTPUT FIELDS
shape: list
list_key: themes
- theme (text)
- summary (text)
- quote (text)
- keywords (list)  range: 3..5

## ON INVALID
max_retries: 5
fallback: keep_with_warning

## EVAL
stratify_by: grounded
judge_label_options: ["good", "spurious_theme", "bad_summary", "bad_quote"]
llm_judge.criteria: Decide if the theme is faithful to the source.
llm_judge.label_options: ["correct", "incorrect"]
```

Placeholder syntax for prompts:
- `{{column_name}}` — substituted with the row's value of that column (recommended)
- `{target}` / `{compare}` / `{extra.X}` — legacy syntax, still works

Both can mix in the same prompt.

## Sample recipes

The repo ships 8 starter recipes:

**Discovery / coding:**
- `aspect_pass1_discovery` — free-form aspect extraction (Pass 1)
- `aspect_pass2_structured` — apply a fixed taxonomy (Pass 2)
- `theme_grounded` — themes with quote verification

**Rating / classification:**
- `rate_attributes` — score each text 0-100 on a set of attributes
- `classify_topics` — assign one or more topic labels from a list
- `codify_themes` — apply a fixed codebook (Pass 2 of theme analysis)

**Extraction / comparison:**
- `extract_entities` — pull named entities (person, org, place, event)
- `compare_paired` — pairwise comparison between two text columns

Recipes are starting points — edit the prompt, attributes, allowed values, etc. for your study, then "Save as new recipe."

## Parallelism

Recipe runs are concurrent. The Runs tab has a "Parallel threads" field; leave at 0 for auto-detection.

Auto-detection logic (in order of precedence):
1. **SLURM env vars** (`SLURM_GPUS_ON_NODE`) — authoritative on HPC clusters like LUMI
2. **`nvidia-smi --list-gpus`** — counts NVIDIA GPUs
3. **`rocm-smi --showid`** — counts AMD GPUs
4. **Ollama `/api/ps`** — detects whether a model is loaded into VRAM
5. Fall back to CPU-only

Suggested concurrency:
- CPU-only: `min(cpu_count, 8)`
- 1 GPU: 12 threads
- N GPUs: `min(12*N, 64)`

## Ingestion

Three stages, each independently configurable per source:

**Load.** Files (CSV/XLSX/PDF/DOCX/TXT/JSON/JSONL), pasted text, or HuggingFace datasets. JSON files get a small **path picker** for nested data — e.g. `[*].data` to descend into `[{"data": {...}}, ...]`-shaped files.

**Structure.** For tabular sources, pick one or more text columns (joined as `Column: value\n\nColumn: value` if multi), an optional ID column, and any metadata columns to carry along.

**Segment.** Five strategies: `none`, `char_length`, `paragraph`, `regex`, `llm_natural`. The regex strategy includes a regex-from-examples helper.

**Save.** After Apply, click "Save dataset" to commit the ingested CSV into `datasets/` for later runs.

## Eval

**Quick Eval (the workshop default):** pick a results CSV → click Pick samples → label 5 random rows ✓/✗ → see your alignment percentage. Labels are appended to the results CSV.

**Full Eval (Advanced tab):** devset/testset partitioning, stratified or targeted sampling, parallel human + LLM judges, Cohen's κ, confusion matrix. Recipes can declare `eval` blocks that configure stratification axis, label options, and an LLM-judge rubric.

**Optimize (Advanced tab):** with at least 3 "good" labels, click Optimize and DSPy `BootstrapFewShot` produces an optimized prompt, saved as `<original>_optimized_v<N>.json`.

## What changed from earlier versions

**v6 (current):** dropped GABRIEL dependency entirely. The 5 GABRIEL helpers (rate, classify, codify, compare, extract) are now native `custom_extract` recipes living in `recipes/custom/`. Added adaptive parallelism with auto-detection (SLURM / nvidia-smi / rocm-smi / Ollama API). Single recipe family, single runner, single testable path. ~30% less code than v5.

**v5:** filesystem reshape (datasets / results / recipes separated), recipes shown as text in the Chat tab (not JSON), Quick Eval simplified to 5-sample alignment check, advanced eval moved out of default surface.

**v4 and earlier:** see git history.
