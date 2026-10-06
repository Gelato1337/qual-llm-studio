---
name: qls-pipeline
description: Set up a Qual LLM Studio project and run the fixed pipeline stages (ingest transcripts, 1st-order coding with verified quotes, consolidation, independent pi analyst runs, comparison). Use when the researcher wants to start a qualitative analysis, add interviews, code transcripts, or run replicate analyses.
---

# Qual LLM Studio: pipeline

Qual LLM Studio (`qls`) keeps a qualitative analysis visible and replayable: transcripts are split into segments with stable IDs, every quote is checked against the transcript, and every change to the analysis is a logged decision with an actor and a one-line reason. Run `qls guide` for the full command reference.

## Before you start

- `qls --version` must work. If not: `pip install "qual-llm-studio[all] @ git+https://github.com/Gelato1337/qual-llm-studio"` (or `pip install -e ".[all]"` in a clone).
- Model access for stages 1-2: `ANTHROPIC_API_KEY` in the environment (default provider), or set `[llm]` in `qls.toml` to an OpenAI-compatible endpoint (`provider = "openai"`, `base_url`, `model`). Check the study's consent terms before sending interviews to a cloud API; a local model (vLLM/Ollama on own hardware) is the fallback.
- Ask before running stages that call a model: they cost money and send data out.

## Steps

1. **Project**: `qls init <folder>`, then help the researcher fill `context/research_question.md` and `context/study_context.md`. The context decides what counts as a concept (e.g. "technology trends", not "technologies"). Optional: `context/interview_guide.md`.
2. **Ingest**: `qls ingest transcripts/*` (txt/md/srt/vtt directly; PDF, DOCX, audio, etc. via Docling). Check `qls docs`: every transcript should show an interviewer and an informant. If roles are wrong, re-ingest with `--interviewer LABEL`. Unlabelled audio transcripts come in as one informant voice.
3. **Stage 1, 1st-order coding**: `qls code --run code-v1 [--passes 3]`. Afterwards read the manifest numbers (`runs/code-v1/manifest.json` → `grounding`, `ungrounded`) and tell the researcher how many quotes were verified and how many were rejected.
4. **Stage 2, consolidation**: `qls consolidate code-v1 --into cons-v1`. Merges identical labels, then same-meaning concepts proposed by the model; every merge is a decision record.
5. **Review**: `qls report --run cons-v1` writes `reports/cons-v1.html`. Point the researcher to flagged concepts (`echoes_question`, `short_quote`).
6. **Grouping** (stages 3-4), any of:
   - independent pi agents: `qls analyst --from cons-v1 --replicates 5` (needs pi: `npm install -g @mariozechner/pi-coding-agent`);
   - Claude itself: use the `qls-gioia-grouping` skill;
   - the researcher with Claude as a critical partner: use the `qls-dialogue` skill.
7. **Compare**: `qls compare runA runB --html reports/a_vs_b.html`, `qls consensus run1 run2 run3 ...`.

To rerun one stage with everything upstream frozen, fork the upstream run (`qls fork`) and run only that stage on the fork. Never edit files under `runs/` or `corpus/` by hand.
