# Qual LLM Studio: Roadmap

Status: planning document, October 2026. Living file; update as decisions land.

---

## 1. Vision

**A general toolkit for LLM-assisted qualitative research, with the Gioia method as its north star.**

Qual LLM Studio (`qls`) is a set of tools that any agent harness can drive: Claude Code, Claude Cowork, pi, Codex, or a plain script. It handles the boring and error-prone parts of qualitative research (transcription, parsing, segmentation, quote verification, tables, logging, comparison) so that the *interpretive* work, by a human, an agent, or both, is visible and replayable.

> Every interpretive choice is logged, replayable, and measured for stability: across runs, across models, and against humans.

Why Gioia is the north star: it is the hardest common case. It needs informant-close 1st-order coding, abstraction into 2nd-order themes and aggregate dimensions, and a transparent data structure. If the toolkit does Gioia well, it does thematic analysis, codebook coding, framework analysis and content analysis too. Those are subsets or variations of the same operations.

What it is **not**: a one-shot "make me a Gioia figure" button. A frontier model can already produce a plausible-looking data structure in one prompt. The hard part, and our contribution, is showing whether that structure is any good and where it came from.

---

## 2. Where we come from

### 2.1 Base paper

Laato, J., Mäntymäki, M., Kordyaka, B., & Laato, S. (2025). *Automating Qualitative Data Analysis with Chain-of-Thought Reasoning Models: A Study with the Gioia Method.* AMCIS 2025 Proceedings (SIG DSA), Paper 2130. <https://aisel.aisnet.org/amcis2025/data_science/sig_dsa/1/>

- DeepSeek-R1 with custom pipelines on LUMI; quote hallucination handled with Levenshtein checks.
- Compared against a peer-reviewed human Gioia analysis of 17 expert interviews.
- Result: conceptually similar, but 1st-order concepts **lost their meaning** (over-generalisation), and 2nd-order themes and aggregate dimensions were **inaccurate and not very informative**. Nine challenges identified.
- The paper's code is lost. This repo is the follow-up tooling.

### 2.2 What this repo already has (to become `legacy/`)

A Gradio app that runs a *recipe* (prompt + output schema) row by row over a dataset, against Ollama, with JSON parsing, validation, retry-with-feedback and parallelism. Built for deductive, per-row work (classify, rate, extract).

| Module | Keep? | Notes |
|---|---|---|
| `app/ingest/segmenters/*` | Salvage | Paragraph, regex, char-length, LLM-natural segmenters. |
| `app/ingest/json_loader.py`, `structure.py`, `sources.py` | Salvage parts | Tabular loading ideas; replaced by Docling + DuckDB. |
| `app/json_parse.py` | Salvage | Multi-strategy JSON extraction; still useful for weak local models. |
| `app/grounding.py` | Salvage + change | Fuzzy quote check. Must return `(segment_id, start, end)` offsets, not just a score. |
| `app/runner_custom.py`, `prompting.py`, `recipe.py` | Salvage as "map" stage | Per-segment prompt + schema + retry loop becomes one operation (`qls map`). |
| `app/inference.py` | Replace | Ollama only. Replaced by a multi-provider layer. Keep the retry-on-empty-thinking logic. |
| `app/runs.py` | Replace | Versioned run folders → content-addressed artifact store. |
| `app/eval/*` (judges, sampling, agreement) | Salvage parts | LLM-as-judge and stratified sampling are useful for deductive work. |
| `app/eval/optimizer.py` (DSPy) | Park | Optimising prompts against our own labels conflicts with a fixed, logged protocol. |
| `app/ui_*.py`, `main.py`, `drive.py` | Park | Gradio UI. Review UI is rebuilt as static HTML reports (§5.8). |
| `scripts/lumi_example.sh` | Salvage | Ollama-on-LUMI GPU discovery loop; template for the vLLM job script. |

Gap: the old runner only knows **map over rows**. Gioia stages 2–4 are **reduce over the corpus** (merge, split, group), which the old design cannot express. There is also no decision log, no hashing, no run comparison and no multi-provider support.

---

## 3. Who uses it, and how

### 3.1 Usage environments

The user brings **one** of these:

| Environment | What the user provides | What runs where |
|---|---|---|
| **A. API keys** (default) | One LLM key + one speech-to-text key | Everything on a laptop; models via API. |
| **B. Own compute** | A GPU machine or an HPC allocation (LUMI, CSC Mahti/Roihu, university cluster) | STT and LLMs run locally (WhisperX/pyannote, vLLM/Ollama). No data leaves the machine. |
| **C. Mixed** | Both | e.g. local STT + pseudonymisation, then API LLMs on the pseudonymised text. |

Environment B is also the **ethics fallback**: interview consent often does not cover cloud processing.

**LLM keys, supported from day one** (any one is enough):
- Anthropic (Claude), OpenAI (GPT), Google (Gemini), Mistral (EU-hosted option).
- OpenRouter (one key, many models).
- Any OpenAI-compatible endpoint: vLLM, Ollama, llama.cpp server, university/HPC-hosted endpoints.

**Speech-to-text key, recommended default: AssemblyAI.** It has top-tier diarization in 2026 benchmarks, supports Finnish, and has an EU endpoint (`api.eu.assemblyai.com`) where audio never leaves the EU.
Alternatives behind the same interface:
- **Speechmatics**: strong Finnish, EU endpoint, and an **on-prem container** option. Best choice if a project needs a commercial model but cannot send audio out.
- **ElevenLabs Scribe v2**: 90+ languages, up to 32 speakers in batch.
- **Deepgram Nova-3**, **OpenAI** transcription models: supported, not default.

Before committing to one for a study, run a small bake-off: 2–3 hand-corrected interviews, measure WER and DER per provider. Diarization quality varies a lot by language and recording setup.

### 3.2 Orchestration chain

```
 Researcher
    │  (natural language, review, decisions)
    ▼
 Orchestrator ── Claude Code / Claude Cowork / any agent  ── or just a script
    │  loads the qls skill/plugin; calls qls CLI or MCP
    ▼
 Qual LLM Studio (qls)
    │  project store · ingestion · tables · coding ops · metrics · reports
    │  spawns sandboxed analyst runs when asked
    ▼
 Analyst harness ── pi (default) / Claude Agent SDK / Codex / fixed pipeline
    │  sandbox: corpus read-only, workspace writable, qls tools, no network except model endpoint
    ▼
 Model provider ── API key (Anthropic, OpenAI, …) or local vLLM/Ollama
```

Two ways to use it:
1. **Claude → qls → pi → model.** Claude orchestrates a study: ingest data, launch N sandboxed pi analyst runs, compare them, show the researcher the disagreements. Each pi run is an independent "analyst".
2. **Just Claude (or just pi).** The researcher works with one agent directly; the agent uses `qls` tools as its lab bench. Every structural change still goes through `qls` and is logged.

### 3.3 Why an agent harness (and why pi as the example)

An agent harness gives the model the same freedom a human researcher has: read transcripts, grep, write memos, revisit an earlier decision, adapt to surprises in the data. pi is the reference harness because it is minimal (four tools, small system prompt, full visibility into every model call), multi-provider (15+ providers), extensible with TypeScript extensions and skills, logs sessions as JSONL, and since 1.0 (October 2026) also supports MCP.

Freedom is controlled at two places, not by restricting thinking:
- **The sandbox** fixes what the agent can reach (§5.7).
- **The commit boundary**: the analysis structure (codes, themes, dimensions, tables) can only change through `qls` operations, which validate inputs, check quotes and write a decision record. The agent can think however it likes; the record of what it *decided* is exact.

We measure stability on the **output structure**, not on the path. The path (pi's session log) is kept and becomes data: process metrics (§7.3).

---

## 4. Methods supported

Methods are packaged as **playbooks**: a skill file (instructions + checklists) plus the `qls` operations it uses. Gioia is the reference playbook and the one we validate.

| Playbook | Type | Main operations | Priority |
|---|---|---|---|
| **Gioia** | Inductive, theory-building | code → merge/split/rename → theme → dimension → data structure + narrative | North star |
| Reflexive thematic analysis (Braun & Clarke) | Inductive | code → candidate themes → review → define/name | High (shares Gioia ops) |
| Codebook / framework analysis | Deductive or hybrid | apply codebook → matrix (case × code) | High (legacy recipes cover much of it) |
| Attribute rating / classification (GABRIEL-style) | Deductive measurement | map prompt + schema over rows | Done in legacy; port as `qls map` |
| Content analysis | Deductive, counts | apply codebook → counts → tables | Medium |
| Saturation analysis | Meta | random orderings → new-code curve | Medium (cheap once runs exist) |

---

## 5. Architecture

### 5.1 Layers

```
┌────────────────────────────────────────────────────────────┐
│ Interfaces: qls CLI (JSON out) · MCP server · Python API   │
│             skills / pi package / Claude plugin            │
├────────────────────────────────────────────────────────────┤
│ Playbooks: gioia · thematic · codebook · rating · …        │
├────────────────────────────────────────────────────────────┤
│ Operations: ingest · segment · map · code · merge · split  │
│   rename · drop · theme · dimension · memo · table · query │
│   compare · consensus · report · export                    │
├────────────────────────────────────────────────────────────┤
│ Services: STT adapters · document parsing · LLM providers  │
│   grounding · embeddings · pseudonymisation · metrics      │
├────────────────────────────────────────────────────────────┤
│ Core: project store · data model · IDs · artifact store    │
│   (content-addressed) · decision log · run manifests       │
└────────────────────────────────────────────────────────────┘
```

Implementation language: **Python** for the core (the ecosystem we need is Python: Docling, pyannote, DuckDB, CluSim, scikit-learn). Harnesses talk to it through the CLI or MCP, so pi being TypeScript is not an issue.

### 5.2 Project layout on disk

```
my-study/
  qls.toml                 # project config: language, providers, defaults
  sources/                 # raw inputs (audio, pdf, docx, csv…), read-only after import
  corpus/                  # normalised documents, turns, segments (parquet + jsonl)
  tables/                  # user and generated tables (parquet) + datapackage.json
  store/                   # content-addressed artifacts: <sha256>.json
  runs/<run_id>/           # manifest.json, decisions.jsonl, memos/, session log, outputs
  reports/                 # generated HTML reports and figures
  exports/                 # .qdpx, csv, docx
```

### 5.3 Data model (IDs everywhere, text in one place)

| Entity | ID | Key fields |
|---|---|---|
| Source | `src_…` | path, sha256, media type, consent flags |
| Document | `doc_…` | source, participant, metadata, full text |
| Turn | `doc_…:t12` | speaker, role (interviewer/informant), start/end time, char offsets |
| Segment | `seg_…` | doc, turn(s), char span, prompting question |
| Quote | `q_…` | segment, char span (verified), match score |
| Concept (1st-order) | `c_…` | label, definition, quote IDs |
| Theme (2nd-order) | `t_…` | label, definition, concept IDs |
| Dimension | `d_…` | label, definition, theme IDs |
| Memo | `m_…` | author (human/agent), text, linked IDs |
| Table | `tbl_…` | schema, source, generated-by |
| Decision | `dr_…` | see §5.4 |
| Run | `run_…` | manifest: model, params, prompt/skill hashes, corpus hash, harness, seed, timestamps |

Raw text lives only in `corpus/`. Every downstream artifact refers to IDs and spans. Reports re-hydrate text when shown to a human.

### 5.4 Decision record

Emitted by every operation that changes structure, by humans and agents alike:

```json
{
  "id": "dr_01J…",
  "run_id": "run_…",
  "stage": 3,
  "actor": "agent | human | pipeline",
  "model": "provider/model@version",
  "prompt_hash": "sha256:…",
  "op": "create | merge | split | rename | drop | move | assign",
  "inputs": ["c12", "c40"],
  "outputs": ["t3"],
  "reason": "one line",
  "parent": "dr_…",
  "ts": "2026-10-06T12:00:00Z"
}
```

For stages 2–4 the decision record *is* the tool call: the agent calls `qls merge c12 c40 --into t3 --reason "…"`. The record shows exactly what was done. The `reason` field is still the model's own account and is treated as such.

### 5.5 Ingestion

```
audio/video ──► STT adapter ──► diarized transcript ─┐
pdf/docx/odt/html/… ──► Docling ──► structured doc ──┼─► transcript parser ─► turns ─► segmenter ─► segments
txt/srt/vtt/existing transcripts ────────────────────┘        (speaker, role, question)
csv/xlsx/parquet/sav ──► DuckDB ──► table (+ text columns → documents, row id kept)
.qdpx (NVivo/ATLAS.ti/MAXQDA/QualCoder) ──► REFI-QDA importer ──► docs + existing codes
```

- **Transcript parser** is ours: Docling and STT give text and speakers, but not interview structure. The parser finds speaker turns, tags interviewer vs informant, and links each answer to the question that prompted it. This makes "quote + the question that prompted it + one turn before/after" free in every view.
- **Speaker identity**: diarization gives `SPEAKER_00/01`. A cheap `qls speakers` step maps them to roles and pseudonymous participant IDs (agent proposes from content, human confirms).
- **Pseudonymisation** before anything goes to a cloud LLM, when the project requires it (§5.9).

### 5.6 Tables: making the agent aware of tabular data, and creating it

Qualitative studies are rarely text-only: participant metadata, survey open-ends, case attributes, coding matrices.

- **Engine: DuckDB.** Query CSV/Parquet/XLSX in place with SQL; fast and dependency-light.
- **Awareness:** `qls table list`, `qls table describe <tbl>` returns schema, types, row count, null rates, a few sample rows, and column descriptions from a `datapackage.json` (Frictionless Table Schema). This short summary, not the whole file, is what the agent reads.
- **Query:** `qls table query "SELECT …"` returns JSON with a row cap. Read-only by default.
- **Create:** `qls table create <name> --from-sql …` or from structured outputs (`qls map` results, coding matrices). Every created table gets a manifest entry (who, how, from what) and a `datapackage.json` description.
- **Text in tables:** `qls ingest table --text-col answer --id-col respondent` turns open-ended survey answers into documents/segments, keeping the row link. Codes can then be joined back to metadata (`concept × informant`, `theme × role`).
- **Generated tables** the playbooks rely on: concept × informant matrix, segment × code assignments, run × item stability, consensus matrix.

### 5.7 Agent layer and sandbox

Deliverables:
- **`SKILL.md` / `AGENTS.md`** describing the `qls` CLI: works in every harness that can run bash.
- **MCP server** (`qls mcp`) exposing the same operations: for Claude Cowork, Claude Desktop, and pi ≥1.0.
- **pi package**: skills + prompt templates + an extension that registers the project's model config and adds a `qls` status line.
- **Claude Code / Cowork plugin**: skills (playbooks), slash commands (`/qls-ingest`, `/qls-run`, `/qls-compare`), MCP server.

Sandbox for an analyst run (fixed and hashed in the run manifest):
1. Corpus snapshot, read-only, hash-pinned.
2. Model, endpoint and decoding parameters.
3. System prompt + playbook skill + research question (hashed; swapping the research-question file = framing experiment).
4. Tool set: `qls` + bash; no network except the model endpoint.
5. Empty writable workspace per run. **Replicate runs never see each other.** Cross-run access (previous runs, consensus) is an explicit condition, and the default in human collaboration mode.

Container: Docker/Podman locally; Apptainer/Singularity on LUMI and CSC systems.

### 5.8 Review and reporting

Static HTML reports (no server needed; open locally, share as a file). Principle: **show what differs, hide what agrees.**

- **Run diff:** two or more runs side by side; stable groupings collapsed, disagreements expanded with the decision records that caused them.
- **Concept card:** label, definition, number of informants, best quotes (each with its prompting question and context), counter-examples, stability badge ("appeared in 9/10 runs").
- **Informant story card:** 3–4 line arc per participant; quotes sit inside a story.
- **Concept × informant matrix:** is a theme driven by 2 talkative people or by 15?
- **Gioia data structure figure:** generated (Graphviz/Mermaid), with stability shading.
- **Decision log view:** the method section's audit trail, filterable by stage and actor.
- **Saturation curve:** new concepts vs number of interviews, over random orderings.

### 5.9 Privacy and ethics

- Consent check per source (`consent: local_only | eu_cloud | any_cloud`) enforced by provider routing: a `local_only` source cannot be sent to an API model.
- Pseudonymisation: Microsoft Presidio as the framework; Finnish recognisers from City of Helsinki `text-anonymizer` and/or ANOPPI; the LLM proposes additional entities, a human confirms. Mapping table stored separately and never sent to a model.
- EU endpoints preferred (AssemblyAI EU, Speechmatics EU, Mistral).

### 5.10 Interoperability

- **REFI-QDA (`.qdpx`) import/export**: exchange with NVivo, ATLAS.ti, MAXQDA, QualCoder. Researchers can review or continue in the tool they already use. No mature Python library found; we write a small reader/writer against the published XML schema.
- CSV/Parquet export of every table; DOCX/Markdown export of narrative and codebook.

---

## 6. Technology list

Status: **Use** = planned dependency · **Option** = behind an adapter, user's choice · **Evaluate** = test before deciding.

### 6.1 Speech-to-text and diarization

| Tool | Kind | Why | Status |
|---|---|---|---|
| [AssemblyAI](https://www.assemblyai.com/docs/pre-recorded-audio/select-the-region) Universal | API | Strongest API diarization in 2026 benchmarks; Finnish; EU endpoint | **Use (default API)** |
| [Speechmatics](https://www.speechmatics.com/speech-to-text/finnish) | API / on-prem container | Strong Finnish, EU endpoint, on-prem option | Option |
| ElevenLabs Scribe v2 | API | 90+ languages, 32-speaker diarization | Option |
| Deepgram Nova-3, OpenAI transcription | API | Common, cheap | Option |
| [WhisperX](https://github.com/m-bain/whisperX) | Local | faster-whisper + word alignment + pyannote; the standard local pipeline | **Use (default local)** |
| [pyannote.audio 4 / community-1](https://www.pyannote.ai/blog/community-1) | Local | Best open diarization; language-independent; CC BY 4.0 | **Use** (via WhisperX or directly) |
| Whisper large-v3 / large-v3-turbo | Local ASR | Multilingual incl. Finnish | Use (via WhisperX) |
| [NVIDIA Parakeet TDT 0.6B v3](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3) + Sortformer | Local | Fast; 25 European languages incl. Finnish; NeMo diarization | Evaluate |
| DiariZen | Local | Strong open diarization research pipeline | Evaluate |
| Docling ASR | Local | Audio: Whisper only (openai-whisper, mlx-whisper on Apple Silicon, or whisper-s2t), timestamped segments, **no speaker diarization**. Video: ffmpeg extracts the audio track → Whisper; optional basic diarization (Resemblyzer embeddings + agglomerative clustering, 2–8 speakers); keyframes by fixed interval or scene change | Not our interview STT path (diarization too basic, audio-only has none). Use for video material and documents |
| ffmpeg, Silero VAD | Local | Audio normalisation, voice activity | Use |

### 6.2 Document parsing

| Tool | Why | Status |
|---|---|---|
| [Docling](https://github.com/docling-project/docling) | PDF/DOCX/PPTX/HTML/ODF/EPUB/email → structured doc; layout, tables, OCR; MIT | **Use (default)** |
| MarkItDown (Microsoft) | Light fallback → Markdown | Option |
| PyMuPDF | Fast PDF text when layout is irrelevant | Option |
| Our transcript parser | Speaker turns, interviewer/informant, question linkage, SRT/VTT/plain formats | **Build** |

### 6.3 Tables and storage

| Tool | Why | Status |
|---|---|---|
| DuckDB | SQL over CSV/Parquet/XLSX in place; agent-friendly queries | **Use** |
| Polars / pandas | Dataframe work; pandas for legacy compatibility | Use |
| Parquet (pyarrow) | Corpus and table storage | Use |
| Frictionless Data (`datapackage.json`) | Table schemas and column descriptions the agent can read | Use |
| pyreadstat | SPSS/Stata import for survey data | Option |
| SQLite | Project index (IDs, runs, decisions) | Use |

### 6.4 LLM access

| Tool | Why | Status |
|---|---|---|
| [LiteLLM](https://github.com/BerriAI/litellm) | One Python interface to 100+ providers incl. Anthropic, OpenAI, vLLM, Ollama | **Use** for fixed-pipeline and `qls map` calls |
| Native Anthropic / OpenAI SDKs | Provider-specific features (prompt caching, batch API) when needed | Option |
| [vLLM](https://docs.vllm.ai/en/latest/features/batch_invariance/) | Local/HPC serving; OpenAI-compatible; batch-invariant mode for determinism | **Use (HPC)** |
| Ollama | Laptop-friendly local models | Option |
| llama.cpp server | CPU / small machines | Option |

Note on determinism: vLLM's batch-invariant mode (`VLLM_BATCH_INVARIANT=1`) is documented for NVIDIA GPUs (compute capability ≥ 8.0). LUMI is AMD MI250X; support there is unverified. The "deterministic floor" experiment may need to run on an NVIDIA system (e.g. CSC Mahti A100s) or must measure residual nondeterminism on LUMI rather than assume none.

### 6.5 Agent harnesses and integration

| Tool | Why | Status |
|---|---|---|
| [pi](https://pi.dev/) ([repo](https://github.com/badlogic/pi-mono)) | Minimal, transparent, multi-provider analyst harness; skills, extensions, JSONL sessions; MCP since 1.0 | **Use (reference analyst harness)** |
| Claude Code | Orchestrator for developers/researchers comfortable in a terminal | **Use (orchestrator)** |
| Claude Cowork | Orchestrator for researchers who are not programmers; plugins = skills + MCP + commands | **Use (orchestrator)** |
| Claude Agent SDK | Programmatic analyst runs in Python | Option |
| MCP Python SDK (FastMCP) | Expose `qls` operations as an MCP server | **Use** |
| Typer | CLI | Use |
| Pydantic | Schemas for every artifact and decision record | Use |

### 6.6 Analysis and metrics

| Tool | Why | Status |
|---|---|---|
| [CluSim](https://github.com/Hoosier-Clusters/clusim) | Element-centric similarity for **overlapping and hierarchical** clusterings (Gates et al. 2019); plus ARI/NMI and 20+ measures | **Use** |
| scikit-learn | ARI, NMI, baseline clustering | Use |
| SciPy | Hungarian matching for label alignment | Use |
| sentence-transformers + multilingual embedding model (e.g. BGE-M3, multilingual-e5) | Label and segment similarity, multilingual incl. Finnish | Use |
| rapidfuzz | Quote grounding (partial ratio + offsets) | Use (from legacy) |
| krippendorff | Inter-coder agreement for deductive tasks and human baseline | Use |

### 6.7 Privacy

| Tool | Why | Status |
|---|---|---|
| Microsoft Presidio | Detection + anonymisation framework, pluggable recognisers | Use |
| [City of Helsinki text-anonymizer](https://github.com/City-of-Helsinki/text-anonymizer) | Finnish names, IDs, phone numbers | Evaluate |
| [ANOPPI](https://seco.cs.aalto.fi/publications/2022/oksanen-et-al-anoppi-2022.pdf) | Finnish pseudonymisation with morphology-aware NER | Evaluate |

### 6.8 Reporting, packaging, infra

| Tool | Why | Status |
|---|---|---|
| Jinja2 + static HTML/JS | Reports without a server | Use |
| Graphviz / Mermaid | Gioia data structure figure | Use |
| Vega-Lite or Observable Plot | Charts in reports (stability, saturation) | Use |
| uv + pyproject, ruff, pytest, GitHub Actions | Packaging and CI | Use |
| Docker/Podman, Apptainer | Sandboxes locally and on HPC | Use |
| Slurm job arrays | N replicate runs on LUMI/CSC | Use |

### 6.9 Neighbouring tools (position against, interoperate with)

| Tool | Relation |
|---|---|
| [GABRIEL](https://github.com/openai/GABRIEL) (OpenAI; Asirvatham, Mokski & Shleifer, *GPT as a Measurement Tool*, NBER WP 34834, 2026) | Deductive attribute measurement at scale. We cover that as `qls map` but focus on inductive theory-building and its evaluation. |
| [QualCoder](https://qualcoder.org/) (MIT; 4.0 beta Sept 2026) | Open-source QDA desktop app with AI-assisted coding. Complement: exchange via `.qdpx`; humans can review our runs there. |
| NVivo, ATLAS.ti, MAXQDA | Commercial QDA; interop via REFI-QDA. |

---

## 7. Research programme

### 7.1 Main question

**Where in the pipeline does interpretation, and instability, enter?**
Paper 1 combines pain points 1 and 2 below: a stage-by-stage variance breakdown plus a method for comparing open-ended Gioia structures.

### 7.2 Study design

Models: one Anthropic, one OpenAI, one open model (e.g. Qwen) on local compute. Pin exact versions and dates.

| Condition | What |
|---|---|
| A. End to end | Each model runs the full pipeline N times (N≈10). |
| B. Stage swaps | Claude end to end, with one stage replaced by another model's output; one stage at a time. |
| C. Stage-isolated reruns | Freeze upstream, rerun stage k N times → per-stage variance. |
| D. Human baseline | ≥2 independent human coders on at least one dataset. AI-vs-AI vs human-vs-human variance. |
| E. One-shot baseline | Whole corpus in one prompt → full Gioia structure. Does the staged pipeline beat it? |
| F. Agentic vs fixed pipeline | Same model, same tools: free pi analyst vs fixed stage pipeline. Does freedom help or hurt stability and quality? |
| G. Framing (later) | Shuffle interview order, reword the research question, prime with theory. |

Pipeline stages (each a rerunnable unit, from frozen upstream artifacts):

| # | Stage | Output |
|---|---|---|
| 0 | Segment transcripts | segment IDs + spans |
| 1 | 1st-order coding | concept → segment/quote IDs |
| 2 | Merge / clean concepts | merge/split/rename/drop decisions |
| 3 | 2nd-order themes | theme → concept IDs |
| 4 | Aggregate dimensions | dimension → theme IDs |
| 5 | Data structure + narrative | figure + text |

### 7.3 Metrics

Labels differ across runs, so compare **structure, not names**.
- **Structural similarity per level:** element-centric similarity (handles a segment carrying several concepts, and the hierarchy). ARI/NMI reported where assignments are forced to a partition, for comparability with prior work.
- **Granularity:** number of items per level, reported separately; otherwise similarity scores mostly reflect granularity.
- **Within-model** similarity (reproducibility), **between-model** similarity (is the structure in the data or the model), **stage-swap delta**.
- **Consensus matrix** across runs → stable core vs fragile groupings → stability badges.
- **Informant-language score:** lexical/embedding overlap of labels with informant words vs generic/academic vocabulary. Targets the base paper's "lost meaning" finding.
- **Label similarity:** embeddings + Hungarian matching (secondary).
- **Process metrics (agentic runs):** share of corpus read, counter-evidence searches, revisions, memos written. Do runs that read more produce more informant-close and more stable structures?
- **Validity:** blind expert ratings; distance to human analyses.

### 7.4 Research pain points (ranked)

1. Where does variance enter? Expected: coding stable, abstraction (stages 3–4) unstable.
2. Comparing open-ended structures without a shared label space; possible standalone methods contribution.
3. Stability ≠ validity: a model can be consistently generic. Needs the human baseline.
4. Pull towards literature/genericness: informant-language score.
5. Order and framing sensitivity.
6. Automated saturation curves.
7. Anchoring in human–AI work: do AI suggestions narrow what researchers see? (User study, HCI venue.)

### 7.5 Human + AI collaboration model

- **AI:** exhaustive 1st-order coding with verified quotes.
- **Human + AI:** 2nd-order themes. AI proposes alternative groupings, argues against the researcher's choices, finds counter-evidence.
- **Human:** final aggregate dimensions and theory. The decision log becomes the method section.

### 7.6 Related work to check

- MindCoder / "Efficiency with Rigor!": <https://arxiv.org/abs/2501.00775>
- "Thematic Analysis in the Age of LLMs: Human–Machine Differences and the Role of Context": <https://link.springer.com/chapter/10.1007/978-3-032-38014-2_26>
- arXiv 2401.15170: <https://arxiv.org/abs/2401.15170>
- "How AI Coders Discuss, Disagree, and Reach Consensus" (arXiv 2609.11109)
- "Human-LLM Collaborative Inductive Coding…" (arXiv 2607.28889)
- "When LLMs fall short in Deductive Coding…" (arXiv 2512.21041)
- Gates et al. (2019), element-centric clustering comparison, *Scientific Reports*.

---

## 8. Phased plan

### Phase 0: Housekeeping
- [ ] Move current code to `legacy/` unchanged, with a short README on what it was.
- [ ] `pyproject.toml` with uv; ruff, pytest, GitHub Actions CI.
- [ ] Package skeleton `qls/` (core, services, ops, playbooks, interfaces).
- [ ] Synthetic interview fixture set (5–6 short fake interviews, Finnish + English) for tests and demos.

### Phase 1: Core and ingestion
- [ ] Data model (Pydantic) and IDs; project layout; `qls init`.
- [ ] Content-addressed artifact store; run manifests; decision log (`decisions.jsonl` + SQLite index).
- [ ] Ingestion: Docling adapter, plain/SRT/VTT readers, transcript parser (turns, roles, question linkage).
- [ ] STT adapters: AssemblyAI (API default), WhisperX + pyannote community-1 (local default); common diarized-transcript format.
- [ ] Segmenters ported from legacy.
- [ ] Grounding returning `(segment_id, start, end)` offsets.
- [ ] Tables: DuckDB-backed `list / describe / query / create`, `datapackage.json`, text-column ingestion.
- [ ] LLM provider layer (LiteLLM), config in `qls.toml` + env vars; consent-aware routing.
- [ ] `qls map` (port of the legacy recipe runner).

### Phase 2: Agent layer
- [ ] `SKILL.md` / `AGENTS.md` for the CLI.
- [ ] MCP server.
- [ ] Sandbox recipe (container + manifest hashing); `qls run --harness pi --replicates N`.
- [ ] pi package (skills, prompt templates, extension).
- [ ] Claude Code / Cowork plugin.

### Phase 3: Gioia playbook
- [ ] Structural operations: `code`, `merge`, `split`, `rename`, `drop`, `theme`, `dimension`, `memo`, all validated and logged.
- [ ] Gioia skill (agentic mode) and fixed-stage driver (pipeline mode) on the same operations.
- [ ] Stage freezing and isolated reruns (`qls rerun --stage 3 --from <artifact>`).
- [ ] Data structure + narrative generation.

### Phase 4: Metrics and review
- [ ] Run comparison per level (CluSim element-centric, ARI/NMI, granularity).
- [ ] Consensus matrix and stability badges.
- [ ] Informant-language score; process metrics from session logs.
- [ ] HTML reports: run diff, concept cards, informant cards, concept × informant matrix, Gioia figure, decision log.

### Phase 5: Study runs
- [ ] Smoke test: N=5 end to end, one model, on the 17-interview dataset (or synthetic data until access/ethics are settled).
- [ ] vLLM job script for LUMI/CSC; determinism check.
- [ ] Conditions A–F; analysis; paper 1.

### Phase 6: Interop and collaboration
- [ ] REFI-QDA `.qdpx` import/export.
- [ ] Pseudonymisation pipeline (Presidio + Finnish recognisers).
- [ ] Human-in-the-loop review flow (accept/reject decisions, counter-arguments).
- [ ] Other playbooks: thematic analysis, codebook/framework, saturation.
- [ ] Anchoring user study design.

---

## 9. Open questions

1. **Human baseline:** for the 17-interview dataset, are there ≥2 independent human coders, or only the one published analysis?
2. **Data access and consent:** does consent cover cloud APIs? If not, Phase 5 starts on local compute and synthetic data.
3. **Compute:** LUMI (AMD) vs CSC NVIDIA systems for the determinism floor.
4. **Name:** keep "Qual LLM Studio" / `qls`, or rename now that it is a toolkit rather than a studio UI?
5. **UI:** are static HTML reports enough, or do researchers need an interactive review app early (accept/reject decisions)?
6. **Languages:** Finnish + English from the start? Affects STT, embeddings and the informant-language score.

---

## 10. Sources consulted (October 2026)

- pi: <https://pi.dev/>, <https://github.com/badlogic/pi-mono>, <https://mariozechner.at/posts/2025-11-30-pi-coding-agent/>, MCP in pi 1.0: <https://www.theregister.com/ai-and-ml/2026/10/02/pi-coding-agent-pulls-a-180-and-adds-mcp-support/5300678>
- pyannote community-1: <https://www.pyannote.ai/blog/community-1>, <https://github.com/pyannote/pyannote-audio>
- WhisperX: <https://github.com/m-bain/whisperX>
- Parakeet v3: <https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3>
- STT comparisons: <https://www.assemblyai.com/blog/top-speaker-diarization-libraries-and-apis>, <https://futureagi.com/blog/speech-to-text-apis-in-2026-benchmarks-pricing-developer-s-decision-guide/>
- AssemblyAI EU residency: <https://www.assemblyai.com/docs/pre-recorded-audio/select-the-region>
- Speechmatics Finnish / deployments: <https://www.speechmatics.com/speech-to-text/finnish>, <https://docs.speechmatics.com/deployments>
- Docling: <https://github.com/docling-project/docling>, <https://pypi.org/project/docling/>
- REFI-QDA: <https://www.qdasoftware.org/project>, <https://www.maxqda.com/help/report-and-export/export-and-import-refi-qda-projects>
- CluSim / element-centric similarity: <https://github.com/Hoosier-Clusters/clusim>, <https://www.nature.com/articles/s41598-019-44892-y>
- vLLM batch invariance: <https://docs.vllm.ai/en/latest/features/batch_invariance/>, <https://thinkingmachines.ai/blog/defeating-nondeterminism-in-llm-inference/>
- LiteLLM: <https://github.com/BerriAI/litellm>
- GABRIEL: <https://github.com/openai/GABRIEL>, <https://www.nber.org/papers/w34834>
- QualCoder: <https://qualcoder.org/doc/en/>
- Finnish anonymisation: <https://github.com/City-of-Helsinki/text-anonymizer>, <https://seco.cs.aalto.fi/publications/2022/oksanen-et-al-anoppi-2022.pdf>
- Claude Cowork plugins: <https://claudecowork.im/blog/claude-cowork-plugins-complete-guide>
