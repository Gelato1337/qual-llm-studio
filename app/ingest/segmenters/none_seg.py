"""No-op: pass document through unchanged."""

from __future__ import annotations

from .base import Document


class NoSplit:
    name = "none"

    def split(self, doc: Document) -> list[Document]:
        return [doc]
