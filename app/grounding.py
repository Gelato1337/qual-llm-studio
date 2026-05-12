"""
Quote grounding — does a quote actually appear in the source?

The notebook used exact substring match. We upgrade to rapidfuzz's
partial_ratio with a 90% threshold, which is more forgiving of
whitespace, punctuation, and small paraphrases while still catching
genuine hallucinations.

partial_ratio is the right metric here: it finds the best-matching
substring of the source for the given quote, scoring 0–100. A quote
that the model lightly cleaned up ("the movie's plot" -> "the plot")
will still score high; a quote the model invented will not.
"""

from __future__ import annotations

DEFAULT_THRESHOLD = 90


def _normalize(s: str) -> str:
    return " ".join(s.lower().split())


def fuzzy_score(quote: str, source: str) -> float:
    """Return rapidfuzz partial_ratio (0-100). Imports rapidfuzz lazily so
    the module doesn't hard-fail if rapidfuzz isn't installed."""
    if not quote or not source:
        return 0.0
    try:
        from rapidfuzz import fuzz  # type: ignore
    except ImportError as exc:
        raise ImportError(
            "rapidfuzz is required for fuzzy grounding. "
            "Install with: pip install rapidfuzz"
        ) from exc
    return fuzz.partial_ratio(_normalize(quote), _normalize(source))


def is_grounded(quote: str, source: str, threshold: float = DEFAULT_THRESHOLD) -> bool:
    """True if the quote appears in the source within `threshold` similarity."""
    return fuzzy_score(quote, source) >= threshold


def ground_quotes(
    quotes_and_sources: list[tuple[str, str]],
    threshold: float = DEFAULT_THRESHOLD,
) -> list[dict]:
    """Vectorized grounding for a batch. Returns score + boolean per pair."""
    return [
        {
            "score": (s := fuzzy_score(q, src)),
            "grounded": s >= threshold,
        }
        for q, src in quotes_and_sources
    ]
