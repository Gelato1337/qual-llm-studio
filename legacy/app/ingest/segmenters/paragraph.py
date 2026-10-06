"""Split on blank lines, optional grouping to a target size."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .base import Document

_PARA_BREAK = re.compile(r"\n\s*\n")


@dataclass
class ParagraphSplit:
    name = "paragraph"
    group_target_chars: int = 0

    def split(self, doc: Document) -> list[Document]:
        paragraphs = [p.strip() for p in _PARA_BREAK.split(doc.text) if p.strip()]
        if not paragraphs:
            return [doc]

        if self.group_target_chars <= 0:
            return [
                doc.child(
                    suffix=f"p{i:03d}",
                    text=p,
                    chunk_index=i,
                    chunk_strategy=self.name,
                )
                for i, p in enumerate(paragraphs)
            ]

        chunks = []
        buffer = []
        buffer_len = 0
        idx = 0
        for p in paragraphs:
            buffer.append(p)
            buffer_len += len(p) + 2
            if buffer_len >= self.group_target_chars:
                chunks.append(
                    doc.child(
                        suffix=f"p{idx:03d}",
                        text="\n\n".join(buffer),
                        chunk_index=idx,
                        chunk_strategy=self.name,
                    )
                )
                buffer = []
                buffer_len = 0
                idx += 1
        if buffer:
            chunks.append(
                doc.child(
                    suffix=f"p{idx:03d}",
                    text="\n\n".join(buffer),
                    chunk_index=idx,
                    chunk_strategy=self.name,
                )
            )
        return chunks
