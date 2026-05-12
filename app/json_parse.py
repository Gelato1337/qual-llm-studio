"""
Multi-strategy JSON extraction for LLM responses.

LLMs return JSON in many wrong ways:
  * wrapped in ```json fences
  * with trailing commas
  * with single quotes
  * with unquoted keys
  * fully malformed but with the data we want findable by regex

This module tries each strategy in order and returns the first that works.
Ported from our elephant health pipeline; battle-tested.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


@dataclass
class ParseResult:
    data: Any
    method: str
    ok: bool

    @classmethod
    def fail(cls, method: str = "failed") -> "ParseResult":
        return cls(data=None, method=method, ok=False)


def extract_json_block(text: str) -> str:
    """Pull the most likely JSON object/array out of free-form text."""
    text = text.strip()

    # ```json ... ``` or ``` ... ``` fences
    fence = re.search(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", text, re.DOTALL)
    if fence:
        return fence.group(1)

    # Greedy match for outermost object — works for most well-formed cases
    obj = re.search(r"\{.*\}", text, re.DOTALL)
    if obj:
        return obj.group(0)

    # Fallback to array
    arr = re.search(r"\[.*\]", text, re.DOTALL)
    if arr:
        return arr.group(0)

    return text


def repair_loose_json(s: str) -> str:
    """Best-effort cleanup of common LLM JSON mistakes."""
    s = re.sub(r",\s*}", "}", s)
    s = re.sub(r",\s*]", "]", s)
    s = s.replace("'", '"')
    # Quote bare keys: {foo: ...} -> {"foo": ...}
    s = re.sub(r"(\{|,)\s*(\w+)\s*:", r'\1"\2":', s)
    return s


def parse_json(text: str) -> ParseResult:
    """Try every strategy. Return the first that yields a dict or list."""
    if not text or not text.strip():
        return ParseResult.fail("empty")

    block = extract_json_block(text)

    # 1. Direct
    try:
        return ParseResult(data=json.loads(block), method="direct", ok=True)
    except json.JSONDecodeError:
        pass

    # 2. Loose repair
    try:
        repaired = repair_loose_json(block)
        return ParseResult(data=json.loads(repaired), method="loose_repair", ok=True)
    except json.JSONDecodeError:
        pass

    # 3. json_repair library, if available — much more robust
    try:
        from json_repair import repair_json  # type: ignore

        repaired = repair_json(block)
        return ParseResult(data=json.loads(repaired), method="json_repair", ok=True)
    except Exception:
        pass

    return ParseResult.fail("failed")


def parse_with_key(text: str, key: str) -> list[dict]:
    """Convenience: parse JSON and pull a list under a top-level key.

    Returns [] if anything goes wrong. Used by pipelines that expect
    {"aspects": [...]} or {"themes": [...]}.
    """
    result = parse_json(text)
    if not result.ok or not isinstance(result.data, dict):
        return []
    value = result.data.get(key, [])
    return value if isinstance(value, list) else []
