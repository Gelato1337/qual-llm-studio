# Smoke test: two real oral histories, end to end (7 October 2026)

## Data

Two interviews from the Computer History Museum's Software History Center, downloaded as PDFs:

- Dan Fylstra, interviewed by Thomas Haigh, 2004 (about 16k words): <https://archive.computerhistory.org/resources/access/text/2024/01/102805341-05-01-acc.pdf>
- Mitch Kapor, interviewed by William Aspray, 2004 (about 12k words): <https://archive.computerhistory.org/resources/text/Oral_History/Kapor_Mitch/Kapor_Mitch.oral_history.2006.102657943.pdf>

The transcripts are © Computer History Museum and are not stored in this repository. Research question
used for the test: how early PC software founders experienced the shift from programs as hobby or
by-product to software as a commercial product.

## Who the model was

No API key was available in the environment. The run used `provider = "external"`: every model request
was written to a file and answered by the Claude Code session running the test (Claude Opus 5.5),
following the stage prompts. This tests the pipeline with a real model's answers, but:

- it is **not** an API run; manifests record `external:claude-code-session`;
- the same session wrote the code, coded the transcripts and grouped the concepts, so the grouping is
  not an independent replicate. Use this run to check the machinery, not the method.

## What ran

| Stage | Command | Result |
|---|---|---|
| Ingest | `qls ingest *.pdf` (Docling 2.134) | Fylstra 85 segments, Kapor 56 (58 after fixes); interviewer found in both; about 2.5 min per PDF on CPU |
| 1 | `qls code --run code-v1` | 66 concepts, 107 quotes, all located exactly (2 requests) |
| 2 | `qls consolidate code-v1 --into cons-v1` | 2 merges (1 cross-informant, 1 within Kapor); coding memos carried through |
| 3-4 | `qls group cons-v1 --view memos` | 10 themes, 3 dimensions; return-to-data check: 2 relabels, 1 concept taken out with a counter-memo (1 + 10 + 3 requests) |
| Checks | `qls check`, `qls metrics`, `qls report`, `qls export` | no lost quotes or double assignments; theme labels share 0.27 of their content words with the quotes, generic-word share 0.04, 2 informants per theme |

## Problems found and fixed

1. **Speaker labels rendered as Markdown headings by Docling** (`## Aspray: What did your father do?`),
   labels alone on a line, and section titles inside turns. Interviewer questions leaked into informant
   segments (Kapor: 15 interviewer turns detected instead of 34): the AMCIS "interview guide
   contamination" problem, created by the parser. Fixed: heading marks stripped, label-only lines
   handled, section titles kept as context, full names resolved to surnames.
2. **Segments cut mid-sentence** where a PDF page break fell inside a sentence. Fixed: segments are exact
   slices of a turn, cut only at sentence ends.
3. **Long monologues attributed to an early question** ("Did your mother have a professional career?"
   for a life story eight segments later). Fixed: segments record `question_gap`; reports, cards and the
   coding prompt say "earlier Q"; the `echoes_question` flag only looks at direct answers.
4. **Interviewer detection by question ratio was fragile.** Added explicit "I am interviewing <name>"
   detection and a first-speaker prior.
5. **Merged concepts lost their memo in dimension context packs.** Fixed: inherited memos shown.
6. **Docling log noise.** Silenced.
7. Re-parsing no longer needs a new conversion: Docling output is cached in `corpus/text/`.

## Open

- Docling occasionally splices a fragment into the wrong place (reading order across a page break); not
  fixable in qls, visible in `corpus/text/<doc>.md`.
- The quote check was not stressed: quotes were copied, so no fabricated quote occurred. A real API run
  is needed to measure the rejection rate.
- The context experiment (labels vs cards vs memos, with and without verification) is meaningless with
  one answerer who already knows the data; it needs independent API runs.
