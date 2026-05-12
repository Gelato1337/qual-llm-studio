"""Split on a user-supplied regex pattern."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .base import Document


@dataclass
class RegexSplit:
    name = "regex"
    pattern: str = ""
    flags: int = re.MULTILINE

    def split(self, doc: Document) -> list[Document]:
        if not self.pattern:
            raise ValueError("regex pattern is required")
        try:
            rx = re.compile(self.pattern, self.flags)
        except re.error as exc:
            raise ValueError(f"invalid regex {self.pattern!r}: {exc}") from exc

        matches = list(rx.finditer(doc.text))
        if not matches:
            return [doc]

        boundaries = [m.start() for m in matches]
        if boundaries[0] > 0 and doc.text[: boundaries[0]].strip():
            positions = [0] + boundaries
        else:
            positions = boundaries
        positions.append(len(doc.text))

        chunks = []
        for idx in range(len(positions) - 1):
            start = positions[idx]
            end = positions[idx + 1]
            piece = doc.text[start:end].strip()
            if not piece:
                continue
            chunks.append(
                doc.child(
                    suffix=f"r{idx:03d}",
                    text=piece,
                    chunk_index=idx,
                    chunk_start=start,
                    chunk_end=end,
                    chunk_strategy=self.name,
                )
            )
        return chunks if chunks else [doc]
