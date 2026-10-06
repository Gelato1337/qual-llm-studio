"""Generate a copy-paste prompt the user can take to ChatGPT/Claude/Gemini
to produce a regex from examples.

Workflow:
  1. User pastes 3-10 examples of split-marker text (e.g. ['Chapter 1',
     'Chapter 2', 'Chapter 17'])
  2. App generates a prompt with those examples baked in
  3. User copies prompt → pastes to external smart LLM → gets a regex
  4. User pastes regex back into the regex segmenter

We don't run the regex generation locally because small Ollama models
do this badly. Sending the user out to a smart LLM is faster, more
reliable, and a teachable moment ("the LLM helps you build the pipeline,
not just run it").
"""

from __future__ import annotations

PROMPT_TEMPLATE = """I need a Python regex that matches a chapter/section/heading marker in a document. Here are examples of strings that should ALL match my regex (one per line):

{examples}

Also here are some examples of strings that should NOT match:

{negatives}

Requirements:
- The regex will be used with re.MULTILINE flag, so ^ matches line starts.
- It should match the START of each marker (so the document can be split there).
- Be tight enough to avoid false positives but flexible enough to catch all the positive examples.
- Use the simplest pattern that satisfies the constraints.

Output: a single line containing ONLY the regex pattern, nothing else.
Do not wrap in slashes, quotes, or code fences. Just the raw pattern.

Example good output:
^Chapter\\s+\\d+

Example bad output:
`^Chapter\\s+\\d+`
/^Chapter\\s+\\d+/
"^Chapter\\s+\\d+"
"""


def build_prompt(positives: list[str], negatives: list[str] | None = None) -> str:
    """Build a copy-paste prompt for the user to send to an external LLM."""
    if not positives:
        raise ValueError("need at least one positive example")
    pos_block = "\n".join(f"- {p}" for p in positives)
    neg_block = (
        "\n".join(f"- {n}" for n in negatives)
        if negatives
        else "(no negative examples provided)"
    )
    return PROMPT_TEMPLATE.format(examples=pos_block, negatives=neg_block)
