"""Split every N characters, optional overlap."""

from __future__ import annotations

from dataclasses import dataclass

from .base import Document


@dataclass
class CharLengthSplit:
    name = "char_length"
    chunk_size: int = 5000
    overlap: int = 0

    def split(self, doc: Document) -> list[Document]:
        if self.chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        if self.overlap < 0 or self.overlap >= self.chunk_size:
            raise ValueError("overlap must be in [0, chunk_size)")

        text = doc.text
        if len(text) <= self.chunk_size:
            return [doc]

        step = self.chunk_size - self.overlap
        chunks = []
        i = 0
        idx = 0
        while i < len(text):
            piece = text[i : i + self.chunk_size]
            chunks.append(
                doc.child(
                    suffix=f"c{idx:03d}",
                    text=piece,
                    chunk_index=idx,
                    chunk_start=i,
                    chunk_end=min(i + self.chunk_size, len(text)),
                    chunk_strategy=self.name,
                )
            )
            i += step
            idx += 1
        return chunks
