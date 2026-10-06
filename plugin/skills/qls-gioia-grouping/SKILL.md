---
name: qls-gioia-grouping
description: Do an independent Gioia grouping in a Qual LLM Studio project, turning 1st-order concepts into 2nd-order themes and aggregate dimensions, with every decision logged. Use when the researcher asks Claude to make its own themes/dimensions, a blind or independent analysis, or an AI grouping to compare against theirs.
---

# Independent Gioia grouping

You are one independent analyst. Your grouping will be compared with the researchers' and with other runs, so it must be your own: **do not look at any other run's themes or dimensions** until you have finished. That is why you work on a blind fork.

Use the `qls` MCP tools. If they are unavailable, use the `qls` CLI with the same names (`qls guide`). Your actor is `agent:cowork` (or `agent:claude-code` in Claude Code). Never use `human` for your own decisions.

## Setup

1. `open_project(path)` with the folder that contains `qls.toml` (ask the researcher if unsure), then `project_context()` and `list_runs()`.
2. Pick the source run: normally the latest `consolidation` run (else a `coding` run). Confirm with the researcher if there are several.
3. `fork_run(source, new="claude-<yyyymmdd>", blind=True, actor="agent:cowork")`.

## Work

- Read every concept card first: `list_concepts(run, cards=True, quotes=3)`. Cards show what informants said and which interviewer question each quote answered.
- Work from meaning, not labels. When a card is ambiguous, read the data: `read_document(doc, start)` around the quote's turn, or `search_corpus(...)`. An earlier automated attempt (AMCIS 2025) failed because it grouped bare concept names and lost what informants meant, then filed themes under dimensions by name alone.
- Write memos while you work (`add_memo`): alternatives you considered, tensions, concepts that fit nowhere.
- Fix a 1st-order concept only when clearly needed (duplicate, mixed points, off the research question), and give the reason.
- **2nd-order themes** (`create_theme`): name what is going on across a set of concepts. Each needs a definition of one or two sentences. Themes may be more abstract than concepts, but must say something specific about this data. "Challenges" or "Technology" would fit any study and are not informative. Each concept goes in at most one theme.
- **Aggregate dimensions** (`create_dimension`): before placing a theme, look at the concepts inside it, not just its name.
- Every change needs a one-line `reason`; write it for a reviewer reading the method section.

## Finish

1. `check_run` returns no problems; `run_status` shows no unassigned concepts, or each remaining one is explained in a memo.
2. Write a final memo: the data structure in brief, the 2-3 groupings you are least sure about and why, and what alternative structure you considered.
3. `write_report(run)` and give the researcher the path, plus a short summary: number of themes and dimensions, and the uncertain spots.
4. Only now, if the researcher asks, compare with other runs (`compare_runs`, `write_compare_report`).
