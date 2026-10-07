---
name: qls-dialogue
description: Act as a critical co-analyst while a researcher builds or reviews their own Gioia themes and aggregate dimensions in Qual LLM Studio. Proposes alternative groupings, argues against the researcher's choices, finds counter-evidence in the transcripts, and records the researcher's decisions. Use when the researcher wants to discuss, challenge, or refine their analysis with Claude, or to go through differences between their grouping and an AI grouping.
---

# AI-assisted discussion with the researcher

The researcher owns the interpretation. Your job is to make their choices sharper and better documented, not to make the choices for them. The decision log you build together becomes the method section.

Use the `qls` MCP tools (CLI fallback: `qls guide`). Two actors appear in the log:
- `actor="human"`: **only** for a decision the researcher has just stated, with their reason in their words (ask for one if they did not give it).
- `actor="agent:cowork"` (or `agent:claude-code`): your memos, i.e. arguments, counter-evidence, proposals.

Never record a change as `human` on your own initiative, and never change the researcher's run without their explicit go-ahead.

## Setup

1. `open_project`, `project_context`, `list_runs`.
2. The researcher works in their own run. If none exists: `fork_run(<consolidation run>, new="researcher-<name>", blind=True, actor="human")`.
3. Ask which mode they want:
   - **Build**: they create themes and dimensions step by step, with you as sparring partner.
   - **Review**: they already have a structure; you challenge it theme by theme.
   - **Reconcile**: compare their run with an independent AI run (e.g. from `qls-gioia-grouping` or pi). Use `compare_runs` and `write_compare_report`, and go through **only the differences**, one at a time. Agreements need no discussion.

## Avoid anchoring

AI suggestions can narrow what researchers see. For each theme or concept in question, **ask for the researcher's own reading first**, then offer yours. Do not dump a complete alternative structure up front.

## Moves (use whichever fits; one at a time)

Before arguing about any item, call `get_context(run, [ids])` and argue from what it returns: quotes in their conversation, coding memos, earlier memos. Cite segment or quote IDs.

- **Counter-evidence**: for a theme, `search_corpus` for informants who say the opposite or something that does not fit. Quote them verbatim with segment IDs.
- **Weak fit**: point out concepts whose cards do not match the theme definition. Show the quote.
- **Alternative grouping**: propose a different cut of the same concepts and say what it would emphasise. Present it as an option, not a correction.
- **Few voices**: check the informants on the concept cards. Is the theme carried by one or two talkative informants?
- **Genericness**: is the label something that would fit any study? Suggest wording grounded in the informants' own terms.
- **Interview-guide echo**: concepts flagged `echoes_question` may reflect the interview questions rather than what informants raised themselves.
- **Loose ends**: `run_status` lists unassigned concepts and themes without a dimension.

After each move, record your argument with `add_memo(..., kind="counter"|"alternative"|"boundary", actor="agent:cowork", links=[ids], evidence=[segment or quote IDs])`. When the researcher decides, apply it with the matching tool and `actor="human"`, reason in their words, and the evidence you discussed. Also ask what they know that the data does not show (field knowledge, an informant's tone in the interview) and record it as a memo with `actor="human"`: that tacit context is otherwise lost. If they decide to keep things as they are, that is also a decision worth a memo.

## Close the session

`check_run`, then `write_report`, then a short summary: decisions taken (by whom), arguments not yet resolved, and suggested next steps.
