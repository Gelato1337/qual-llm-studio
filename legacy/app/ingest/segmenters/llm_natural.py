"""LLM-snapped natural-boundary segmentation.

Iteration: each chunk's start is the previous chunk's *end* (LLM's pick),
NOT a fixed sliding window. That's what prevents content duplication or loss.
"""

from __future__ import annotations

import json as _json
import re
from dataclasses import dataclass
from typing import Any

from .base import Document

DEFAULT_PROMPT = """You are helping segment a long document into natural chunks.

Below is a window of text. We want to split it near the marker
<<<TARGET_LENGTH>>>, ideally at a natural boundary (end of a sentence,
paragraph break, dialogue turn change, section heading) at or BEFORE
the marker.

Rules:
- Pick a boundary at or before the marker, not after.
- Prefer the closest natural boundary to the marker.
- If text right before the marker is mid-sentence, scan back for the
  nearest sentence-ending punctuation followed by whitespace.
- DO NOT rewrite, summarize, or add any text. Only choose where to split.

Output format: one JSON object on one line:
{"split_at": <integer character offset where chunk should END>}

Offset is measured from start of window (position 0). Must be a positive
integer no greater than the marker's position.

WINDOW:
{window}
"""


@dataclass
class LLMNaturalSplit:
    name = "llm_natural"
    wanted_length: int = 5000
    max_length: int = 6000
    model: str = ""
    client: Any = None
    max_iterations: int = 200
    prompt_template: str = DEFAULT_PROMPT
    fallback_min_fraction: float = 0.5
    marker: str = "<<<TARGET_LENGTH>>>"

    def split(self, doc: Document) -> list[Document]:
        if self.wanted_length <= 0 or self.max_length < self.wanted_length:
            raise ValueError("require 0 < wanted_length <= max_length")
        if self.client is None:
            raise RuntimeError("LLMNaturalSplit needs a client set by orchestrator")
        if not self.model:
            raise RuntimeError("LLMNaturalSplit needs a model name")

        text = doc.text
        if len(text) <= self.max_length:
            return [doc]

        chunks = []
        pos = 0
        idx = 0
        warnings = []

        for _ in range(self.max_iterations):
            if pos >= len(text):
                break
            window = text[pos : pos + self.max_length]
            if len(window) <= self.wanted_length:
                chunks.append(self._make_chunk(doc, idx, pos, len(text), window, "tail"))
                break

            marker_at = self.wanted_length
            marked = window[:marker_at] + self.marker + window[marker_at:]
            split_offset = self._ask_llm_for_offset(marked)

            if split_offset is None:
                warnings.append(f"chunk {idx}: LLM unparseable, fell back")
                split_offset = self.wanted_length

            floor = int(self.wanted_length * self.fallback_min_fraction)
            if split_offset < floor:
                warnings.append(
                    f"chunk {idx}: offset {split_offset} below floor {floor}, falling back"
                )
                split_offset = self.wanted_length
            if split_offset > marker_at:
                warnings.append(f"chunk {idx}: past marker, clamped")
                split_offset = marker_at

            chunk_end_pos = pos + split_offset
            piece = text[pos:chunk_end_pos]
            chunks.append(self._make_chunk(doc, idx, pos, chunk_end_pos, piece, "natural"))
            pos = chunk_end_pos
            idx += 1
        else:
            warnings.append(f"hit max_iterations={self.max_iterations} — output may be truncated")

        if chunks and warnings:
            chunks[0].metadata["chunk_warnings"] = warnings
        return chunks

    def _make_chunk(self, parent, idx, start, end, text, reason):
        return parent.child(
            suffix=f"n{idx:03d}",
            text=text,
            chunk_index=idx,
            chunk_start=start,
            chunk_end=end,
            chunk_strategy=self.name,
            chunk_boundary=reason,
        )

    def _ask_llm_for_offset(self, marked_window):
        prompt = self.prompt_template.replace("{window}", marked_window)
        try:
            result = self.client.chat(prompt, model=self.model, json_mode=True)
            content = result.content
        except Exception:
            return None
        try:
            data = _json.loads(content)
            if isinstance(data, dict) and "split_at" in data:
                return int(data["split_at"])
        except (ValueError, TypeError):
            pass
        m = re.search(r'"?split_at"?\s*:\s*(\d+)', content)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                return None
        return None
