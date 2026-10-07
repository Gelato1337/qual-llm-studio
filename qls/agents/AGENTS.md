# Ways of working with the qls server

These hold for any method. The method's own guide is `method_guide()`.

## Quotes

- Quote the informant's exact words, copied from the interview, at the moment you add the concept.
  Quotes are checked immediately; if a quote is not in the transcript, nothing is saved and you are
  told so. Re-read the passage (`read_source`) and copy it again. Do not paraphrase, tidy, translate,
  or join sentences from different places.
- Quote the shortest span that carries the meaning: usually one to three sentences.
- Only informants are quotable. Interviewer questions are context, not evidence.

## Reasons

- Every link that needs a reason gets one line saying why it holds: "describes bypassing the official
  release process", not "relevant". Reasons are what a reviewer reads to judge your analysis.
- Every change (revise, merge, withdraw, remove) needs a reason. Nothing is deleted from history.

## Memory

- Your context window is working memory: one interview at a time. Do not rely on remembering earlier
  interviews; look them up (`search`, `pack`, `memos`, `query`).
- Memos are your notes. Kinds: meaning, informant, surprise, uncertainty, method, negative-case,
  summary, reflexive. Levels: note, interview (about a source), batch (cites interview memos), corpus
  (cites batch memos). Keep them short; the budget per level is enforced.
- The codebook is shared long-term memory. Propose entries and changes (`propose_codebook`); the
  researcher approves them. Use approved entries consistently.

## Team

- Other scholars may be working on the same run. You share the codebook and the graph, not each
  other's context. Say what you mean in labels, descriptions, codebook proposals and memos.

## Stopping

- When the method calls for it, or when you are unsure about something that matters, call
  `checkpoint(summary, questions)` and stop. Writes are blocked until the researcher resumes the run.
- When you resume, read `feedback()` first.
