"""Document data class and Segmenter protocol."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class Document:
    """One unit of text that flows through ingestion."""

    id: str
    text: str
    source: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def child(self, suffix: str, text: str, **extra_metadata) -> "Document":
        """Construct a chunk derived from this Document."""
        new_meta = {**self.metadata, "parent_id": self.id, **extra_metadata}
        return Document(
            id=f"{self.id}__{suffix}",
            text=text,
            source=self.source,
            metadata=new_meta,
        )


class Segmenter(Protocol):
    name: str
    def split(self, doc: Document) -> list[Document]: ...
