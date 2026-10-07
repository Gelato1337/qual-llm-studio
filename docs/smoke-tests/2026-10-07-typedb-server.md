# Smoke test: v3 server on two CHM oral histories (2026-10-07)

Branch `claude/typedb-mcp`, TypeDB 3.12.1, typedb-driver 3.13.6, MCP SDK 2.3.

**Who did what.** No LLM API key was available in the build environment, so Claude (the assistant
writing this code) acted as the analyst agent, calling the same session tools the MCP server exposes
through the shell transport (`qls tool`). The MCP transport itself was tested separately with the
MCP SDK's stdio client (tools listed, `begin`, `add_concept`, `memo`, `query` called; `approve`
not exposed). The researcher steps were done as `human:test` to exercise the flow; they are not
research decisions. Not yet tested: Claude Code or Cowork attached through `.mcp.json`.

## Data

Computer History Museum oral histories (public PDFs, text extracted earlier): Dan Fylstra
(interviewed by Thomas Haigh, 2004) and Mitch Kapor (interviewed by William Aspray, 2004).
Ingestion: 85 and 56 informant segments. Running page footers ("CHM Ref ... Page 11 of 36") were
inside answers and in one file were read as a speaker; ingestion now drops repeated header/footer
lines, so quotes can span page breaks.

## Run `early-pc`

Research question: *How did early personal-computer software entrepreneurs decide what to build,
and for whom?* Stance: information systems researchers.

- 14 concepts, 23 verified quotes (all exact or whitespace-normalised), 5 themes, 2 dimensions;
  2 interview memos, 1 batch memo citing both, 1 uncertainty memo, 2 negative-case memos.
- A paraphrased quote was refused at once ("Quote not found in fylstra ... Nothing was saved").
- Codebook: 1 proposal by the agent, approved with an edit by the researcher (version 1).
- Checkpoint after both interviews blocked a write until the researcher resumed; `qls review`
  showed the agent's two questions and the uncertainty memo first.
- `qls check`: nothing open at the end. `qls run replay`: 35 events, identical graph.

## Data structure

| Concept | Theme | Dimension |
|---|---|---|
| C-9 making a market rather than researching it | T-1 Knowing a market before it exists | D-1 Deciding under unknowable demand |
| C-10 the designer judges what is good |  |  |
| C-12 demand nobody foresaw |  |  |
| C-14 a market research report on the new industry |  |  |
| C-3 pricing as a business product | T-2 Business users as the target |  |
| C-6 microcomputers taken for toys |  |  |
| C-1 seeing a distribution channel form | T-3 Who controls the product | D-2 Building through the industry structure |
| C-2 publishing model borrowed from books and records |  |  |
| C-11 publishing it himself to keep control |  |  |
| C-7 a platform announcement redirects the product | T-4 Reading platform shifts |  |
| C-8 the incumbent looks undislodgeable |  |  |
| C-4 betting the company on one product | T-5 Concentration under scarcity |  |
| C-5 bootstrapping on software economics |  |  |
| C-13 manic focus on one great product |  |  |

D-1 answers the question: They decided what to build by projecting business users and trusting their own judgment, because market data did not exist and demand surprised them.

D-2 answers the question: The structure of the young industry (channels, publishing deals, platform launches) decided for whom and in what form products were built.

## Run `finance`: same interviews, different question

*How did early personal-computer software founders relate to investors and money?* Five concepts.

```
qls compare early-pc finance
early-pc vs finance: DIFFERENT research question
  selection  coded segments 10 / 7, both 3, Jaccard 0.2143
```

The second question selected mostly different passages, which is what an intent-sensitive analysis
should do. This is two hand-made runs, not evidence; the experiment design is in docs/pipeline.md.

## Found and fixed during the test

- TypeDB type inference needs explicit types in `match` when an untyped variable meets an explicit
  role (all server queries now type their variables).
- Every relation role needs `@card(1..1)`; otherwise deleting a concept leaves a membership with an
  empty slot and a theme still "has" two concepts.
- The MCP SDK runs tools in worker threads; the ledger connection is shared across threads and tool
  calls are serialised.
- Placeholder text from the `intent.yaml` template no longer leaks into a run's intent.
