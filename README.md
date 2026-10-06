# Qual LLM Studio

Tools for LLM-assisted qualitative research where **every interpretive choice is logged, replayable, and comparable** across runs, models and researchers. The Gioia method is the reference playbook.

It is a toolkit, not an app. The `qls` command line and its MCP server can be driven by a researcher, by Claude (Code or Cowork), or by an agent harness such as [pi](https://pi.dev/). Whoever drives, the analysis only changes through `qls` operations, and each one writes a decision record: who did what to which IDs, and a one-line reason.

```
transcripts ──Docling / text──► corpus: turns + informant segments with stable IDs
                                   │
stage 1  qls code ────────────────► 1st-order concepts, every quote located in the transcript
stage 2  qls consolidate ─────────► same-meaning concepts merged (each merge logged)
                                   │
stages 3-4, any combination of:    ▼
  qls analyst (pi agents, N independent replicates)
  Claude Cowork / Code, skill qls-gioia-grouping (independent grouping)
  researcher + Claude, skill qls-dialogue (critical discussion, decisions logged as human)
                                   │
qls compare / consensus / report ──► agreement per level, only the differences shown
```

Background and plans: [ROADMAP.md](ROADMAP.md). The earlier Gradio tool is in [legacy/](legacy/).

## Install

```bash
pip install -e ".[all]"          # anthropic, openai, docling, mcp extras
# or minimal: pip install -e .   (text transcripts, mock model, no MCP)
npm install -g @mariozechner/pi-coding-agent   # only for `qls analyst`
```

You need **one** of:
- an LLM API key: `ANTHROPIC_API_KEY` (default, Claude Opus 5.5), or any OpenAI-compatible endpoint set in `qls.toml` (`provider = "openai"`, `base_url`, `model`, e.g. OpenAI, OpenRouter, vLLM or Ollama);
- your own compute running an OpenAI-compatible server (vLLM/Ollama on a GPU machine or LUMI), so no interview data leaves your infrastructure.

Audio is transcribed by Docling with Whisper (no speaker separation); for interviews, a diarized transcript from WhisperX or AssemblyAI is better input. See the roadmap.

## Quick start

```bash
qls init my-study && cd my-study
# write the research question and study context:
$EDITOR context/research_question.md context/study_context.md
qls ingest ~/interviews/*.docx          # txt/md/srt/vtt read directly; other formats via Docling
qls docs                                # check interviewer/informant roles per transcript

qls code --run code-v1                  # stage 1 (add --passes 3 to code each transcript 3 times)
qls consolidate code-v1 --into cons-v1  # stage 2
qls report --run cons-v1                # reports/cons-v1.html

qls analyst --from cons-v1 --replicates 5      # 5 independent pi groupings
qls consensus cons-v1-pi-1 cons-v1-pi-2 cons-v1-pi-3 cons-v1-pi-4 cons-v1-pi-5
qls compare cons-v1-pi-1 researcher --html reports/pi1_vs_researcher.html
```

Try it without a key: set `provider = "mock"` under `[llm]` in `qls.toml` and ingest `examples/synthetic/*.txt`.

`qls guide` prints the full command reference (written for agents, readable by people).

## With Claude Cowork or Claude Code

The repo is a plugin marketplace. The plugin adds the `qls` MCP server and three skills:

| Skill | What Claude does |
|---|---|
| `qls-pipeline` | set up a project, ingest, run stages 1-2, start pi replicates, compare |
| `qls-gioia-grouping` | its own blind grouping into 2nd-order themes and aggregate dimensions |
| `qls-dialogue` | critical co-analyst for the researcher: alternatives, counter-evidence from the transcripts, devil's advocate; records the researcher's decisions as `human` and its own arguments as memos |

Claude Code: `/plugin marketplace add Gelato1337/qual-llm-studio`, then `/plugin install qls@qual-llm-studio`.
Claude Cowork (desktop): Customize → Plugins → add the marketplace from the GitHub URL. The MCP server runs locally (`qls mcp`), so `qls` must be installed on the same machine.

## What is recorded

```
my-study/
  qls.toml                 models and transcript conventions
  context/*.md             research question, study context (hashed into every run)
  corpus/docs/<doc>.json   turns and segments; the only place raw text lives
  runs/<run>/
    manifest.json          parent run, model requested and served, prompt hash, corpus hash,
                           quote-grounding statistics, pi process metrics
    state.json             concepts (with quote spans), themes, dimensions, memos
    decisions.jsonl        one record per change: stage, actor, op, inputs → outputs, reason
    pi-events.jsonl        (analyst runs) the agent's full event stream
```

Any stage can be rerun with everything upstream frozen: fork the upstream run (`qls fork`) and run only that stage on the fork.

## How it addresses the AMCIS 2025 findings

Laato, Mäntymäki, Kordyaka & Laato (2025) automated the Gioia method with DeepSeek-R1 and reported nine challenges. What changes here:

| AMCIS challenge | Here |
|---|---|
| 1. made-up quotes (15%) | every quote is located in the transcript (exact, then whitespace and case, then fuzzy ≥ 90) and stored as a character span; failures get one retry with feedback; rejected quotes are listed in the manifest |
| 2. interview guide leaking into the codes | only informant turns are coded; the question is context; concepts echoing it are flagged `echoes_question` |
| 3-4. wrong abstraction level, off the research question | prompt asks for the informant's point, not its topic; the study context says what counts (e.g. trends, not technologies) |
| 5, 7. quote not clearly showing the concept | quotes shown with their question in reports; thin quotes flagged `short_quote`; the researcher judges instead of an unreliable yes/no model check |
| 6. overlapping concepts; merges lost concepts in ~3 of 4 runs | merges and splits are operations that cannot lose quotes; `qls check` verifies |
| 8. interpretation matters | the decision log makes each interpretive step visible and attributable |
| 9. meaning lost when grouping by labels | grouping agents work from concept cards with quotes and can read the transcripts; process metrics show how much they did |

`examples/amcis2025/context/` holds a starting context for rerunning that study.

## Tests

```bash
pip install -e ".[dev,mcp]" && pytest
```

## License

Apache License 2.0. See [LICENSE](LICENSE).
