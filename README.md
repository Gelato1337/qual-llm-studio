# Qual LLM Studio

Executable qualitative methods: **strict store, free agents.**

A method (Gioia, for now) is a *recipe*: a short YAML document that names the objects of the analysis, how they may be linked, and the steps. Agents work with it freely in any harness (pi, Claude Code, Cowork), but the only way to change the analysis is through `qls` tools, and those tools refuse bad input. A quote that is not in the transcript, a concept without evidence, a theme with one concept, a memo about nothing: each is refused with a reason and a hint (for a quote, the closest real passage and its offsets).

Every write is an event in an append-only log, so a run can be **replayed** (same graph, same hash), **forked** at any event, and **compared** with other runs, models or researchers. That is the point: every interpretive choice is logged, replayable, and measured for stability.

```
sources (txt/md/srt/vtt, or anything Docling reads)
   │  immutable, versioned; turns and informant segments as offset ranges
   ▼
run = recipe + config + event log ──► graph view (objects, links, statuses)
   ▲          ▲            ▲
   │          │            └── researcher: qls review / respond / resume  (CLI only)
   │          └── agent tools over MCP or a shell wrapper, bound to one run and actor
   └── qls run fork --at N · replay · compare · diff · report
```

The 2025 v1 pipeline (fixed stages, one-shot prompts) is in [legacy/qls_v1](legacy/qls_v1); the Gradio tool is in [legacy/](legacy/). Plans and background: [ROADMAP.md](ROADMAP.md).

## Install

```bash
pip install -e ".[mcp]"            # add [docling] for PDF/DOCX/audio ingestion
npm install -g @mariozechner/pi-coding-agent   # only for `qls agent pi`
```

`qls` itself calls no model. Model access belongs to the harness: an API key in pi, or Claude Code / Cowork's own model.

## Quick start

```bash
qls init my-study && cd my-study
$EDITOR context/*.md                     # research question and study context (frozen into each run)
qls ingest ~/interviews/*.txt
qls sources                              # check segments per transcript
qls run new r1 --recipe gioia            # optional --config run.yaml (order_seed, board, ...)
```

Attach an agent, in one of three ways:

```bash
# Claude Code or Cowork: add the MCP server, then paste the task prompt
qls agent mcp-config r1 --actor agent:claude-1 > .mcp.json
qls agent prompt r1 --actor agent:claude-1 --step calibrate --sources P01 P02

# pi, headless, with the shell transport
qls agent pi r1 --actor agent:pi-1 --provider anthropic --model claude-opus-5-5 --step code

# any script
qls tool r1 add_quote '{"source": "P01", "text": "..."}' --actor agent:script
```

When the recipe reaches a checkpoint the agent calls `checkpoint()`, and writes are blocked until the researcher has looked:

```bash
qls review r1                            # what changed since the last checkpoint, uncertainty memos first
qls respond r1 --reject C-7 --reason "describes the tool, not the practice"
qls respond r1 --edit T-2 --set label="Working around the release cycle" --reason "closer to their words"
qls respond r1 --answer "Treat 'legacy' as a theme only when they describe change over time."
qls resume r1
```

Then compare and report:

```bash
qls run fork r1 --at 120 --as r1-alt     # branch after event 120, e.g. with another model
qls run replay r1                        # rebuild from the log; the state hash must match
qls compare r1 r2                        # segment-level agreement per level: Rand, ARI, NMI, pair-F1
qls diff r1 r1-alt                       # objects and links that differ
qls report r1 --format html -o reports/r1.html   # Gioia figure, claim -> quote table, decisions
```

## What the store enforces (Gioia recipe)

| Object | Rule |
|---|---|
| Quote | verified against the source (exact, normalised, or fuzzy ≥ 90); informant words only; stored as offsets |
| Concept | in-vivo label and description; ≥ 1 quote, each link with a reason |
| Theme | label and definition; ≥ 2 concepts with reasons; a concept is in at most one theme |
| Dimension | ≥ 1 theme; a theme is in at most one dimension |
| Memo | about ≥ 1 object or source; interview → batch → corpus memos must cite the level below; length budgets |
| Codebook entry, Term | written by the researcher; agents can only propose |
| anything | never deleted: superseded or rejected, with a reason; incoming links move to the replacement |

Softer expectations (every interview has a memo, every concept is placed, negative cases are looked for) are reported by `check()` and `qls status`, not enforced.

`qls recipe gioia` shows the full recipe and `qls guide` prints the agents' ways of working ([qls/agents/AGENTS.md](qls/agents/AGENTS.md)). A new method is a new YAML file: `qls run new r1 --recipe my-method.yaml`.

## Tests

```bash
pip install -e ".[dev]" && pytest
```

## License

Apache 2.0, see [LICENSE](LICENSE).
