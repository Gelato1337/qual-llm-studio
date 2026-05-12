"""
Prompt templating.

Two coexisting placeholder styles:

  Legacy (existing recipes):
    {target}        the target column for this row
    {compare}       the compare column for this row (if any)
    {extra.<name>}  a value from recipe.inputs.extra

  Column-name (new recipes, Chat tab uses these):
    {{column_name}} substitute the row's value of `column_name`

Both work and can mix in the same prompt. The substitutor scans for
{{...}} first, then for the legacy {target}/{compare}/{extra.X}.

Real qualitative data contains lots of literal {} characters (JSON
samples, code snippets, etc.) so we don't use Python's .format(); we
substitute only the placeholders we recognize and leave everything else
alone.
"""

from __future__ import annotations

import re
from typing import Any

import pandas as pd

# Legacy: {target}, {compare}, {extra.foo}
_LEGACY = re.compile(r"\{(target|compare|extra\.[A-Za-z_][A-Za-z0-9_]*)\}")

# Column-name: {{anything}} — accept any non-} content so users can write
# {{Question 1}} or {{my-col}} etc. We strip whitespace from the lookup.
_COL = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    if isinstance(value, list):
        return "\n".join(f"- {item}" for item in value)
    if isinstance(value, dict):
        return "\n".join(f"- {k}: {v}" for k, v in value.items())
    return str(value)


def render_prompt(
    prompt: str,
    row: pd.Series,
    target_col: str = "",
    compare_col: str | None = None,
    extra: dict[str, Any] | None = None,
) -> str:
    """Render a prompt against a row. Supports both placeholder styles."""
    extra = extra or {}
    available_cols = set(row.index) if hasattr(row, "index") else set()

    # 1. Column-name substitution (new style)
    def replace_col(match: re.Match) -> str:
        col = match.group(1).strip()
        if col in available_cols:
            return _stringify(row.get(col, ""))
        # Leave unknown column placeholders visible so the researcher can spot them
        return match.group(0)

    out = _COL.sub(replace_col, prompt)

    # 2. Legacy substitution
    def replace_legacy(match: re.Match) -> str:
        ph = match.group(1)
        if ph == "target":
            return _stringify(row.get(target_col, "")) if target_col else ""
        if ph == "compare":
            return _stringify(row.get(compare_col, "")) if compare_col else ""
        if ph.startswith("extra."):
            key = ph.split(".", 1)[1]
            return _stringify(extra.get(key, ""))
        return match.group(0)

    return _LEGACY.sub(replace_legacy, out)


def find_column_placeholders(prompt: str) -> list[str]:
    """Return the list of column names referenced via {{column}} in a prompt.

    Used by the Chat tab to highlight which columns a recipe needs and to
    warn if the dataset doesn't have them.
    """
    matches = _COL.findall(prompt)
    return [m.strip() for m in matches]


def find_legacy_placeholders(prompt: str) -> list[str]:
    """Return ['target', 'compare', 'extra.foo', ...] for legacy syntax used."""
    return _LEGACY.findall(prompt)


def build_feedback_prompt(original_prompt: str, errors: list[str]) -> str:
    """Prepend validation-error feedback to the prompt for a retry."""
    if not errors:
        return original_prompt
    bullet_errors = "\n".join(f"- {e}" for e in errors)
    feedback_block = (
        "PREVIOUS ATTEMPT FAILED VALIDATION:\n"
        f"{bullet_errors}\n\n"
        "Try again, fixing those issues. Pay attention to the schema.\n\n"
    )
    return feedback_block + original_prompt
