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
- Work from meaning, not labels. **Before every merge, theme or dimension decision, call `get_context(run, [ids])`**: it rebuilds definitions, coding memos (carried through merges), memos and the quotes inside their conversation. Go further with `read_document` or `search_corpus` when needed. Pass the segment/quote IDs you relied on as `evidence`. An earlier automated attempt (AMCIS 2025) failed because it grouped bare concept names and lost what informants meant, then filed themes under dimensions by name alone.
- Write memos whenever you know something a later decision will need (`add_memo` with `kind`): `boundary` (what a concept is not), `surprise`, `counter` (data against a grouping), `alternative` (a grouping you considered), with `evidence`.
- Fix a 1st-order concept only when clearly needed (duplicate, mixed points, off the research question), and give the reason.
- **2nd-order themes** (`create_theme`): name what is going on across a set of concepts. Each needs a definition of one or two sentences. Themes may be more abstract than concepts, but must say something specific about this data. "Challenges" or "Technology" would fit any study and are not informative. Each concept goes in at most one theme.
- **Aggregate dimensions** (`create_dimension`): before placing a theme, `get_context(run, [theme_id])`, not just its name.
- **Return to data**: when the structure stands, check each theme and dimension once more with `get_context`. Is the label as specific as what informants said? Revise with evidence where it is not.
- Every change needs a one-line `reason`; write it for a reviewer reading the method section.

## Finish

1. `check_run` returns no problems; `run_status` shows no unassigned concepts, or each remaining one is explained in a memo.
2. Write a final memo: the data structure in brief, the 2-3 groupings you are least sure about and why, and what alternative structure you considered.
3. `write_report(run)` and give the researcher the path, plus a short summary: number of themes and dimensions, and the uncertain spots.
4. Only now, if the researcher asks, compare with other runs (`compare_runs`, `write_compare_report`).
