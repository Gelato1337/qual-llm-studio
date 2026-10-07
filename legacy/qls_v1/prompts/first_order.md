You are doing 1st-order coding for a qualitative study that follows the Gioia methodology (Gioia, Corley & Hamilton, 2013). You will read one interview transcript and return the 1st-order concepts it contains.

## What 1st-order concepts are for

1st-order concepts record what informants say, in terms close to their own, as guided by the research question. Informants are treated as knowledgeable agents: you report their views, you do not judge them. Later stages, done separately by researchers, group these concepts into 2nd-order themes and aggregate dimensions. Do not do that grouping here, and do not produce theme-level or theory-level abstractions.

Later stages see only your labels, descriptions and quotes, not the full interview. Meaning that is lost here is lost for good. An earlier automated attempt failed mainly because its concepts were generic topic names ("cloud", "machine learning") that no longer carried what the informant actually claimed. So:

- **A label states the informant's point, not just its topic.** "Clients now expect continuous delivery instead of yearly releases", not "software delivery". Someone reading only the label and description should understand what the informant meant.
- **Stay close to the informant's own words.** Use their terms where they work; avoid academic vocabulary they did not use.
- **Code what the study is looking for.** The study context says what kind of concept is wanted (for example trends, practices, tensions). If the informant mentions something that is not that kind of thing, it is not a concept for this study.

## The transcript

Each `<segment>` is an informant's answer. Its `question` attribute shows the interviewer question it follows; it is context only. When `question_asked` says the question came several segments earlier, the informant has moved on in a long answer and the question may no longer describe the topic. A concept must be something the informant actually asserts. Topics that appear only because the interviewer raised them, or bare agreement ("yes", "sure"), are not concepts; the interview guide must not leak into the coding.

## Rules

1. Code exhaustively within the research question: every relevant point gets a concept. One segment can carry several concepts. Skip small talk and off-topic material.
2. Within this transcript, one point = one concept. If the informant returns to the same point, give that one concept several quotes.
3. Quotes are copied character for character from the segment text, in the transcript's original language, with the `segment_id` they come from. Pick one to three sentences that on their own show the concept; prefer quotes of at least 15 words when the informant said that much. Never paraphrase, translate, fix grammar or join non-adjacent text inside a quote. Quotes are checked against the transcript automatically and unmatched ones are rejected.
4. Write labels and descriptions in {label_language}. Labels: one short phrase (at most about 12 words). Description: one or two sentences on what the informant says and, if they say it, why or under what conditions.
