# The pipeline: a memory server for qualitative analysis

Status: first version implemented (October 2026), see README and docs/smoke-tests/2026-10-07-typedb-server.md. Branch `claude/typedb-mcp`. Supersedes the recipe-in-the-server design on
`claude/executable-protocol` (kept for reference).

## One idea

Split the work by what each part is good at:

| Part | Holds | Is |
|---|---|---|
| **MCP server** | ontology, evidence, memory, history | strict: refuses anything the ontology does not allow |
| **Method document** | how to do Gioia (or another method) | open: a prompt the agent reads and interprets |
| **Harness** | the model and its loop | replaceable: Claude Code, Cowork, pi, any MCP client |

The server does not know what a good theme is. It knows that a 1st-order concept has at least one
verified quote, that a theme groups at least two concepts, that a memo is about something, and that
nothing is ever lost. Interpretation stays with the model and the researcher; rigour lives in the store.

Changing method means adding a schema extension (`schema/methods/<method>.tql`, a few lines) and a
method document (`methods/<method>.md`). The server, the memory model and the outputs stay the same.

## Why TypeDB

The ontology *is* the schema, and the database enforces it at commit time. Tested with TypeDB 3.12
(`tests/test_schema_typedb.py`), these are refused by the database itself, whoever writes:

- a concept without a quote, or a quote link without a reason;
- deleting the last quote of a concept;
- a memo about nothing, or of a kind the method does not define;
- a theme with one concept; a concept in two themes; a theme in two dimensions.

Abstract core types (`code`, `category`) make the core method-agnostic: Gioia's `concept` is a
`code`, so a generic query ("all codes and their quotes") works for any method. TypeDB functions give
context packs (`theme_quotes`, `dimension_quotes`) as named, versioned queries.

Costs, stated plainly:

- It is a server, not a file: one ~100 MB binary (or Docker) next to the MCP server. Starts in seconds.
- TypeDB keeps no history. The run's append-only **event log** stays the source of truth; the graph
  is its current state. A run is one database; fork = new database + replay the log to event N;
  replay must give the same state hash. 100 write transactions take 0.3 s, so replay is cheap.
- Its errors are terse (`[CNT5] Constraint '@card(1..)' has been violated`). The MCP server
  translates them into what the agent needs to hear.
- License MPL-2.0 (compatible with our Apache 2.0).

Quote verification cannot live in the database: the server checks the words against the source
before it writes anything.

## Memory: how a scholar keeps 1000 interviews in mind

A human scholar does not hold 1000 transcripts in working memory either. They read one interview
closely, write notes, and carry a growing understanding of the field that they refine as they go.
The server models four layers of memory and makes each one explicit and inspectable:

| Layer | Human scholar | In the server | In the agent's context |
|---|---|---|---|
| Working | the interview on the desk | the source text, by segment | one interview (or a few), in full |
| Episodic | notes on each interview | interview memos (about a source) | memos of related interviews, on request |
| Semantic | what "legacy" means in this study | **codebook**: definitions, inclusion, exclusion, examples | always, compact |
| Archive | the filing cabinet | the graph: every concept, quote, memo, decision | queried, never dumped |

The **codebook is long-term memory**: it calibrates the agent once its context window can no longer
hold what came before. It starts from calibration (2–3 interviews coded by agents and the researcher,
reviewed together) and grows in versions. Agents propose changes; the researcher (or a reconciling
agent whose proposal the researcher approves) accepts them. Every code records the codebook version it
was made under, so drift is measurable: re-code an early interview under codebook v5 and compare.

Memos climb levels with citation rules: interview memos → batch memos (must cite interview memos) →
corpus memos (must cite batch memos). A higher memo cannot float free of what it summarises.

## Many scholars

Gioia analysis does not need one person throughout. Gioia, Corley and Hamilton (2013) describe team
work: informants' terms collected by several researchers, and a "devil's advocate" or outsider who
challenges the emerging structure. What a team needs is shared memory and explicit reconciliation,
which is what the codebook and the event log are.

So scholars (agents or people) are first-class:

- each scholar has its own context and its own memos;
- all scholars share the codebook (shared long-term memory) and the graph;
- assignments say who read what. 10–20% overlap between scholars gives agreement data for free;
- reconciliation happens at batch boundaries: codebook proposals from different scholars are compared
  and merged, with reasons, by the researcher.

For robustness, teams can also be isolated (separate runs, no shared codebook) and compared: does the
same structure emerge? That is the stability measure the paper needs.

## The pipeline (Gioia instance)

```
0 ingest      Docling → source text, turns, informant segments with offsets (immutable, versioned)
1 calibrate   2–3 interviews: agents code, researcher codes or reviews → codebook v1        [checkpoint]
2 code        interviews assigned to scholars in batches, with overlap
                per interview: context = study + codebook + this interview
                → 1st-order concepts, each with verified quotes and reasons
                → interview memo; codebook proposals
3 consolidate at each batch boundary: batch memo; proposals reviewed → codebook v(n+1)      [checkpoint]
4 structure   2nd-order themes, aggregate dimensions — open-ended, guided by methods/gioia.md;
                context = codebook + concept list + context packs (graph queries), not transcripts
5 dialogue    researcher and agent discuss the structure; every change logged with its reason [checkpoint]
6 robustness  forks: model, interview order, codebook version, memos on/off; overlap agreement;
                segment-level comparison (Rand, ARI, NMI, pair-F1)
7 outputs     Gioia figure, claim → quote table, decision log, robustness appendix,
                read-only reviewer access to the graph
```

Steps 1–3 are where the server does most work. Step 4 is where the method document does most work.

## Quotes: verified at the moment of writing

A 1st-order concept is written in one call together with its quotes:

```
add_concept(source="P07", label="nobody dares touch it", description="...",
            quotes=[{"text": "<the informant's exact words>", "reason": "why this shows the concept"}])
```

The server checks each quote against the source **in the same call**, while the interview is still in
the agent's context. If a quote is not in the source, nothing is written and the agent is told at once:

> Not found in P07 as written. Copy the informant's exact words from the interview.

No guessed "closest match": a wrong suggestion can pull the agent to the wrong passage, and the agent
has the interview in front of it. Only typo-level differences (≥ 95% similar, same passage) are offered
back with their exact text. Concept and quotes are one transaction: a concept never exists without its
evidence, and the database enforces that anyway.

## The MCP server's tools

Bound to one run and one scholar when it starts. Researcher actions stay outside (CLI or a separate
researcher server), so an agent can never approve its own work.

- **Context** — `begin(source)`: the working set for one interview (study context, codebook, the
  interview by segment, memos of related interviews). `pack(target)`: everything that supports a
  concept, theme or dimension. `codebook()`, `memos(about)`.
- **Write** — `add_concept` (with quotes), `create(type, fields, links)` for method types,
  `link`/`unlink` with reasons, `supersede(old, new, reason)`, `memo(about, kind, level, text, cites)`,
  `propose_codebook(...)`.
- **Look up** — `search(text)`, `get(id)`, `neighbors(id)`, and `query(typeql)`: read-only TypeQL, so
  the agent can ask its own questions of the graph ("concepts from P03 not yet in a theme").
- **Control** — `checkpoint(summary, questions)`: stop until the researcher has looked.

`create` is generic: the server reads the method's schema, so a new method needs no new tools.

## What carries over

From `claude/executable-protocol`: ingestion and segmentation (`qls/ingest.py`), quote grounding
(`qls/grounding.py`), the event log and fork/replay design, segment-level comparison
(`qls/analysis.py`), the report layout. What changes: the store becomes TypeDB, the recipe YAML
becomes a TypeQL schema plus a method document, and the per-step task prompts give way to one
method document the agent reads.

## Intent: findings should depend on the question

The only predefined content is the **intent**: the research question, the method, sensitising
concepts and the researcher's stance. Human research varies a lot with intent, and so should this
tool. A method that returns the same findings whatever question and method it is given is not robust;
it is deaf, most likely returning the model's default summary of what is salient in the data.

Robustness is therefore conditional:

- **same intent → similar findings** (reliability: model, seed, order and scholar should matter little);
- **different intent → different findings** (sensitivity, a form of validity);
- **any intent → grounded findings** (verified quotes and reasons separate "another angle" from invention).

Measured as a variance decomposition (G-theory) over the facets intent (question × method), model,
replicate and scholar: a working method shows large intent variance and small variance from the rest.

Controls:

1. **Paraphrased question**: same intent, different wording → findings should *not* change.
2. **Leading question** (presupposes what the data does not show) → few concepts, residuals and memos
   saying the premise is not supported, not confirmation.
3. **Irrelevant question** (the data cannot answer it) → close to empty, with an honest memo.
4. **Other method, same question** (Gioia vs reflexive thematic analysis) → different structure and
   abstraction, not just different labels.

Prediction for Gioia: intent matters more as abstraction rises. Quotes and 1st-order concepts vary
mostly in *which* passages are selected; themes vary more; aggregate dimensions, built to answer the
question, vary most. Segment-level comparison measures selection and structure separately, per level.

Design consequences:

- `intent` is a first-class object; every run is bound to exactly one. Dimensions (probably themes)
  must link to the question they answer, with a reason.
- The codebook belongs to an intent, not to the corpus.
- Sources and verified quotes are shared across intents; interpretation is not.
- Runs under different intents must not see each other's memos or codebook (contamination).

First experiment on the 17 interviews: 2 questions × 2 methods × 3–5 replicates, plus the controls.
A human baseline is needed for same-intent spread too (the original coding plus one independent coder).

## Operational ontology (the Palantir pattern)

Palantir's Foundry Ontology is operational: it models what may be *done* to the data, not only what
the data is. It maps onto this design:

| Foundry | Here |
|---|---|
| object types, properties, link types (semantic layer) | the TypeDB schema |
| interfaces | abstract `code` / `category` |
| **action types** with parameters, submission criteria, validation, logging (kinetic layer) | the MCP tools |
| action log | the event log = decision record |
| functions | context packs, checks |
| scenarios | forks |
| permissions on actions | agents propose; only humans approve |

What to take: make actions first-class and declarative. A method becomes three files: a TypeQL
schema (nouns), an action file (verbs, who may use them, required reasons), and a method document
(how to think). The MCP server generates its tools from the action file, so the decision log is
complete by construction and a new method needs no server code:

```yaml
group_concepts:
  actors: [agent, human]
  params: {label: str, definition: str, concepts: [{id: concept, reason: str}]}
  creates: theme
approve_codebook_entry:
  actors: [human]
```

What not to take: predefined content (Gioia is inductive; the schema fixes only the method's types,
never what "legacy" means or which themes exist); the heavy platform; treating objects as facts
(every interpretive object is someone's claim, with its provenance).

## Open questions

1. Run TypeDB per project (simple) or one shared server with a database per run (needed for forks)?
   Proposal: one local server, database per run.
2. Who approves codebook changes at scale: the researcher always, or a reconciling agent with the
   researcher sampling? Probably the first for the paper, the second as an experiment.
3. Overlap rate and batch size: start with batches of 10 and 2 shared interviews per batch.
4. Researcher interface: CLI first; a second MCP server bound to `human:` actors would let a researcher
   work through Claude while still being logged as the human.
