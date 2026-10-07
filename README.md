> **Branch `claude/typedb-mcp`: v3, first version of the server. Separate from the other branches.**
> See [BRANCHES.md](BRANCHES.md) for what each branch is. Design: [docs/pipeline.md](docs/pipeline.md).

# Qual LLM Studio

A memory server for qualitative analysis: **a strict ontology the agent cannot break, behind an MCP server; the method itself stays open.**

| Part | Holds | Is |
|---|---|---|
| **MCP server** (`qls mcp`) | ontology, verified evidence, memory (memos, codebook), history | strict: refuses what breaks the method's form |
| **Method** (`qls/methods/gioia/`) | schema, actions, and a guide to doing Gioia | open: the agent reads the guide and interprets |
| **Harness** | the model and its loop | any MCP client: Claude Code, Cowork, others |

The analysis lives in **TypeDB**, whose schema *is* the ontology. The database itself refuses a concept without a quote, a theme with one concept, a concept in two themes, a dimension that does not say how it answers the research question, a memo about nothing. Quotes are checked against the transcript **in the same call** that adds a concept, while the interview is still in the agent's context; a quote that is not in the transcript is refused and nothing is saved.

Every change is an event in an append-only log (SQLite) with the TypeQL it ran, so a run can be **replayed** (identical graph, checked by hash), **forked** at any event, and **compared** with other runs: same question (reliability) or different question (does the analysis follow the intent?).

## Install

```bash
pip install -e ".[dev]"                  # add [docling] for PDF/DOCX/audio ingestion
```

TypeDB 3 server (one binary, MPL-2.0):

```bash
curl -LO https://repo.typedb.com/public/public-release/raw/names/typedb-all-linux-x86_64/versions/3.12.1/typedb-all-linux-x86_64-3.12.1.tar.gz
tar xzf typedb-all-linux-x86_64-3.12.1.tar.gz && cd typedb-all-linux-x86_64-3.12.1
./typedb server --storage.data-directory ./data --server.http.enabled false
# macOS / Windows builds and Docker (typedb/typedb:3.12.1): https://typedb.com/docs
```

`qls` connects to `127.0.0.1:1729` as `admin`/`password` by default; set `QLS_TYPEDB`, `QLS_TYPEDB_USER`, `QLS_TYPEDB_PASSWORD` (or `[typedb]` in `qls.toml`) otherwise. `qls doctor` checks the connection.

## Quick start

```bash
qls init my-study && cd my-study
$EDITOR intent.yaml                      # research question, method, stance: frozen into each run
qls ingest ~/interviews/*.docx           # txt/md/srt/vtt directly; other formats via Docling
qls sources                              # check informant segments per transcript
qls run new r1                           # one TypeDB database per run
qls mcp-config r1 --actor agent:scholar-1 > .mcp.json
```

Open Claude Code (or Cowork) in that folder; the `qls` server's tools appear. A starting prompt:

> Read intent(), method_guide() and ways_of_working(). We are calibrating: work through two interviews
> with begin(), then checkpoint() so I can review your concepts and the codebook.

The agent stops at the checkpoint; writes are blocked until you have looked:

```bash
qls review r1                            # its questions and uncertainty memos first, then what changed
qls respond r1 --approve CB-1 --set use_when="..."
qls respond r1 --reject C-7 --reason "describes the tool, not the practice"
qls respond r1 --revise T-2 --set label="Working around the release cycle" --reason "closer to their words"
qls respond r1 --answer "Treat 'legacy' as a theme only when they describe change over time."
qls resume r1
```

Results:

```bash
qls report r1 --format html -o reports/r1.html   # Gioia figure, claim -> quote table, codebook, memos, decisions
qls run replay r1                                # rebuild from the log; the graph must be identical
qls run fork r1 --at 40 --as r1-alt              # branch after event 40 (another model, another scholar...)
qls compare r1 r2                                # which passages each run coded, and structure per level
qls query r1 'match $c isa concept, has id $i; not { theme-membership (concept: $c); }; select $i;'
```

Several scholars can work on one run at once (each with its own `--actor`): they share the graph and the codebook, and keep their own memos. `qls tool r1 add_concept '{...}' --actor agent:x` calls any tool from a shell.

## The tools an agent gets

- **Orient:** `intent`, `method_guide`, `ways_of_working`, `list_sources`, `status`, `check`, `feedback`
- **Read one interview:** `begin(source)` gives the interview, the codebook and your notes on it; `read_source`
- **Look up instead of remembering:** `search`, `get`, `pack` (everything behind a concept, theme or dimension), `memos`, `codebook`, `history`, `query` (read-only TypeQL)
- **Method actions (from `actions.yaml`):** `add_concept` (with quotes), `add_theme`, `add_dimension`
- **Revise:** `add_evidence`, `add_to_group`, `remove_from_group`, `revise`, `merge`, `withdraw` (all with reasons)
- **Memory:** `memo` (note / interview / batch / corpus levels; higher levels cite lower ones), `propose_codebook`
- **Stop:** `checkpoint(summary, questions)`

Researcher-only actions (`approve`, `reject`, `answer`, `resume`) are in the CLI, never in the MCP server.

## A method is three files

```
qls/methods/gioia/
  schema.tql     nouns: concept, theme, dimension and their rules (on top of qls/schema/core.tql)
  actions.yaml   verbs: which tools exist, their fields, how groups form, soft checks as TypeQL
  method.md      how to think: the open-ended guide the agent reads
```

`qls run new r1 --method path/to/my-method/` uses another one; the server needs no new code.

## Tests

```bash
pytest                                   # ingestion tests run anywhere
QLS_TYPEDB=127.0.0.1:1729 pytest         # plus the ontology and server tests against TypeDB
```

## License

Apache 2.0, see [LICENSE](LICENSE). TypeDB is MPL-2.0.
