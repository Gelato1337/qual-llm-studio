"""Segmenter registry and factory."""

from __future__ import annotations

from typing import Any

from .base import Document, Segmenter
from .char_length import CharLengthSplit
from .llm_natural import LLMNaturalSplit
from .none_seg import NoSplit
from .paragraph import ParagraphSplit
from .regex_seg import RegexSplit

REGISTRY = {
    "none": NoSplit,
    "char_length": CharLengthSplit,
    "paragraph": ParagraphSplit,
    "regex": RegexSplit,
    "llm_natural": LLMNaturalSplit,
}

LIST = list(REGISTRY.keys())


def build(name, **params):
    """Instantiate a segmenter by name. Unknown params silently dropped."""
    if name not in REGISTRY:
        raise ValueError(f"Unknown segmenter {name!r}. Choices: {LIST}")
    cls = REGISTRY[name]
    if hasattr(cls, "__dataclass_fields__"):
        valid = set(cls.__dataclass_fields__.keys())
        params = {k: v for k, v in params.items() if k in valid}
    return cls(**params)


__all__ = ["build", "LIST", "REGISTRY", "Document", "Segmenter"]
