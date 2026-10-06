"""
Recipe dispatcher.

Single entry point for running any recipe. Calls runner_custom (the only
runner now — there used to be a separate gabriel runner but those helpers
have been ported as native custom_extract recipes).

Also applies post-processing steps (grounding, etc.) that recipes declare.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from . import runner_custom
from .grounding import fuzzy_score
from .inference import DEFAULT_HOST, OllamaClient
from .recipe import PostProcessStep, Recipe


def run(
    docs: pd.DataFrame,
    recipe: Recipe,
    *,
    client: OllamaClient,
    model: str,
    save_dir: str | Path,
    ollama_host: str = DEFAULT_HOST,
    n_parallel: int = 1,
) -> tuple[pd.DataFrame, list[dict]]:
    """Run any recipe. Returns (results_df, raw_responses_log)."""
    save_dir = Path(save_dir)

    if recipe.type != "custom_extract":
        raise ValueError(
            f"Unknown recipe type: {recipe.type!r}. "
            "(Note: gabriel.* types are no longer supported — port to custom_extract.)"
        )

    if client is None:
        raise ValueError("custom_extract recipes need an OllamaClient")

    results, logs = runner_custom.run(
        docs, recipe, client=client, model=model, n_parallel=n_parallel,
    )
    # Post-processing
    for step in recipe.post_process:
        results = _apply_post_process(results, step, source_docs=docs, recipe=recipe)
    return results, logs


def _apply_post_process(
    results: pd.DataFrame,
    step: PostProcessStep,
    *,
    source_docs: pd.DataFrame,
    recipe: Recipe,
) -> pd.DataFrame:
    if step.type == "ground_quotes":
        return _ground_quotes(results, step.params, source_docs, recipe)
    raise ValueError(f"Unknown post_process type: {step.type!r}")


def _ground_quotes(
    results: pd.DataFrame,
    params: dict,
    source_docs: pd.DataFrame,
    recipe: Recipe,
) -> pd.DataFrame:
    """Verify that quotes in `quote_field` actually appear in the source.

    Adds columns: grounding_score, grounded.
    """
    if results.empty:
        return results

    quote_field = params.get("quote_field", "quote")
    source_field = params.get("source_field", "target")
    threshold = params.get("threshold", 90)

    if quote_field not in results.columns:
        results = results.copy()
        results["grounding_score"] = None
        results["grounded"] = None
        return results

    source_lookup: dict[str, str] = {}
    if source_field == "target":
        target_col = recipe.inputs.target
        for _, row in source_docs.iterrows():
            doc_id = row.get("id", str(row.name))
            source_lookup[str(doc_id)] = str(row.get(target_col, "") or "")

    scores: list[float] = []
    grounded: list[bool] = []
    for _, row in results.iterrows():
        quote = str(row.get(quote_field, "") or "")
        if source_field == "target":
            source = source_lookup.get(str(row.get("doc_id", "")), "")
        else:
            source = str(row.get(source_field, "") or "")
        score = fuzzy_score(quote, source)
        scores.append(round(score, 1))
        grounded.append(score >= threshold)

    results = results.copy()
    results["grounding_score"] = scores
    results["grounded"] = grounded
    return results
