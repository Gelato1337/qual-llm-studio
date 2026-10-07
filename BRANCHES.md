# Branches

| Branch | What it is | Runs today? |
|---|---|---|
| `master` | the original Gradio tool (2025) | yes |
| `claude/upbeat-ritchie-gh2k4n` | **v1 pipeline**: fixed stages (code → consolidate → group → compare → report), LLM via API key, pi analysts, Cowork skills. Smoke-tested on two CHM oral histories. | yes, end to end with an LLM API key |
| `claude/executable-protocol` | **v2**: strict store (SQLite event log), Gioia recipe in YAML, MCP server and CLI, fork/replay/compare, reports. Agents attach via MCP (Claude Code, Cowork) or pi. | yes; analysis is done by an agent through the MCP server |
| `claude/typedb-mcp` | **v3 design (this direction)**: MCP memory server on a TypeDB ontology; method as an open document; intent-sensitivity evaluation; Palantir-style action types. | design + tested schema only; server not written |

Each branch is separate and none is merged into `master`.

## Processing real interviews

For processing interviews now, use `claude/executable-protocol` (agent-driven, every decision logged)
or `claude/upbeat-ritchie-gh2k4n` (batch pipeline with an API key). Keep interview data out of the
repository: projects created with `qls init` live in their own folder.

## TypeDB (for `claude/typedb-mcp`)

```bash
# server: one binary, MPL-2.0
curl -LO https://repo.typedb.com/public/public-release/raw/names/typedb-all-linux-x86_64/versions/3.12.1/typedb-all-linux-x86_64-3.12.1.tar.gz
tar xzf typedb-all-linux-x86_64-3.12.1.tar.gz && cd typedb-all-linux-x86_64-3.12.1
./typedb server --storage.data-directory ./data --server.http.enabled false
# tests
pip install -e ".[typedb,dev]"
QLS_TYPEDB=127.0.0.1:1729 pytest tests/test_schema_typedb.py
```
