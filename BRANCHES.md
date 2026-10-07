# Branches

| Branch | What it is | Runs today? |
|---|---|---|
| `master` | the original Gradio tool (2025) | yes |
| `claude/upbeat-ritchie-gh2k4n` | **v1 pipeline**: fixed stages (code → consolidate → group → compare → report), LLM via API key, pi analysts, Cowork skills. Smoke-tested on two CHM oral histories. | yes, end to end with an LLM API key |
| `claude/executable-protocol` | **v2**: strict store (SQLite event log), Gioia recipe in YAML, MCP server and CLI, fork/replay/compare, reports. | yes; analysis by an agent through the MCP server |
| `claude/typedb-mcp` | **v3 (this direction)**: MCP memory server on a TypeDB ontology; method as an open document; intent as a first-class object; Palantir-style actions from `actions.yaml`. | **yes, first version**: needs a TypeDB 3 server; tested end to end on two CHM oral histories ([docs/smoke-tests/2026-10-07-typedb-server.md](docs/smoke-tests/2026-10-07-typedb-server.md)) |

Each branch is separate; none is merged into `master`. On this branch, v1 lives in `legacy/qls_v1/` and v2 in `legacy/qls_v2/`.

## Processing real interviews with v3

1. Start TypeDB (README, Install).
2. `pip install -e .` in this repository, on this branch.
3. `qls init` a project **outside the repository**, so interview data is never committed.
4. Write `intent.yaml`, `qls ingest`, `qls run new`, attach Claude Code or Cowork with `qls mcp-config`.
