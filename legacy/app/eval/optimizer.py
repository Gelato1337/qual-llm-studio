"""DSPy-based prompt optimizer.

Workflow:
  1. The Eval tab collected human/LLM judgments — labeled examples of
     "good" and "bad" recipe outputs.
  2. We build a DSPy training set from those judgments: input row → desired
     structured output (only the "good" examples).
  3. Run DSPy's BootstrapFewShot (cheapest) to find few-shot demonstrations
     that improve the recipe's prompt.
  4. Extract the optimized prompt back out of the DSPy program.
  5. Save it as a new recipe `<original>_optimized_v<N>.json`.

DSPy talks to Ollama through dspy.LM with base_url. We wire that up so
the optimizer can reach the same Ollama instance the rest of the app uses.

This module degrades gracefully if dspy isn't installed — researchers who
don't need optimization don't have to pull a heavy dependency.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from ..recipe import Recipe


@dataclass
class OptimizationResult:
    """What the optimizer produces."""
    new_prompt: str
    n_train_examples: int
    n_demos_added: int
    notes: str
    new_recipe_path: Path | None = None


def is_available() -> bool:
    """True if DSPy is importable. Used by the UI to enable/disable the button."""
    try:
        import dspy  # type: ignore
        return True
    except ImportError:
        return False


def _build_train_examples(
    results_df: pd.DataFrame,
    source_df: pd.DataFrame | None,
    labeled: list[dict],
    target_col: str,
) -> list[dict]:
    """Build training examples for DSPy from collected judgments.

    Only "good" / "correct" / "positive" labels become training examples
    — DSPy needs examples of the desired behavior, not the wrong one.
    Synonyms are normalized.
    """
    GOOD_LABELS = {"good", "correct", "positive", "yes", "ok"}
    by_id_results = (
        results_df.set_index(results_df["doc_id"].astype(str))
        if "doc_id" in results_df.columns else
        results_df.set_index(results_df["id"].astype(str))
    )
    by_id_source = None
    if source_df is not None:
        if "id" in source_df.columns:
            by_id_source = source_df.set_index(source_df["id"].astype(str))
        elif "doc_id" in source_df.columns:
            by_id_source = source_df.set_index(source_df["doc_id"].astype(str))

    examples = []
    for j in labeled:
        if j["label"].lower() not in GOOD_LABELS:
            continue
        rid = str(j["row_id"])
        if rid not in by_id_results.index:
            continue
        row = by_id_results.loc[rid]
        # If there are multiple rows with the same doc_id (chunked output),
        # picking the first one is fine — they share the same parent doc.
        if isinstance(row, pd.DataFrame):
            row = row.iloc[0]

        # Find the input text. Prefer source df by parent_id if chunked.
        input_text = ""
        if by_id_source is not None:
            parent = row.get("parent_id") if "parent_id" in row.index else None
            for cand in (rid, parent):
                if cand and str(cand) in by_id_source.index:
                    src = by_id_source.loc[str(cand)]
                    if isinstance(src, pd.DataFrame):
                        src = src.iloc[0]
                    if target_col in src.index:
                        input_text = str(src.get(target_col, "") or "")
                        break

        # Build expected output: drop noise columns, jsonify the rest
        drop = {"doc_id", "id", "parent_id", "chunk_index", "chunk_start",
                "chunk_end", "chunk_strategy", "chunk_boundary",
                "_validation_status", "_attempts", "_errors",
                "grounding_score", "grounded"}
        output = {k: v for k, v in row.to_dict().items() if k not in drop}
        examples.append({
            "input": input_text or json.dumps(row.to_dict(), default=str),
            "output": json.dumps(output, default=str, ensure_ascii=False),
        })
    return examples


def optimize(
    recipe: Recipe,
    results_df: pd.DataFrame,
    labeled: list[dict],
    *,
    source_df: pd.DataFrame | None = None,
    optimizer_model: str,
    ollama_host: str,
    save_to: Path | None = None,
    min_examples: int = 3,
) -> OptimizationResult:
    """Run DSPy optimization. Returns OptimizationResult with the new prompt.

    `labeled` is the output of JudgmentStore.labeled_examples() — list of
    dicts with row_id, label, etc.
    """
    if not is_available():
        raise RuntimeError(
            "DSPy is not installed. Add `dspy-ai` to requirements and pip install."
        )

    if recipe.type != "custom_extract":
        raise ValueError(
            f"Optimizer only handles custom_extract recipes, got {recipe.type}"
        )

    # Build training examples
    target_col = recipe.inputs.target
    examples = _build_train_examples(results_df, source_df, labeled, target_col)

    if len(examples) < min_examples:
        raise ValueError(
            f"Not enough 'good' labeled examples to optimize "
            f"(have {len(examples)}, need at least {min_examples}). "
            "Mark more samples as 'good'/'correct' on the Eval tab."
        )

    # Set up DSPy
    import dspy  # type: ignore

    base_url = ollama_host.rstrip("/") + "/v1"
    lm = dspy.LM(
        model=f"openai/{optimizer_model}",
        api_base=base_url,
        api_key="ollama",
        max_tokens=4000,
    )
    dspy.settings.configure(lm=lm)

    # Define a generic Signature — we don't specialize per recipe because
    # DSPy works on input/output strings and the recipe runner is
    # responsible for parsing JSON. The LM produces JSON, we capture the
    # prompt structure including the few-shot demos DSPy adds.
    class ExtractSignature(dspy.Signature):
        """Extract structured information from text following a schema."""
        instructions: str = dspy.InputField(desc="The recipe's instructions and schema")
        text: str = dspy.InputField(desc="The text to analyze")
        output: str = dspy.OutputField(desc="JSON matching the schema")

    program = dspy.Predict(ExtractSignature)

    # Build the trainset
    trainset = [
        dspy.Example(
            instructions=recipe.prompt,
            text=ex["input"],
            output=ex["output"],
        ).with_inputs("instructions", "text")
        for ex in examples
    ]

    # Bootstrap few-shot
    metric = _exact_match_metric
    optimizer = dspy.BootstrapFewShot(metric=metric, max_bootstrapped_demos=4)
    try:
        optimized = optimizer.compile(program, trainset=trainset)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"DSPy optimization failed: {exc}") from exc

    # Extract demos and synthesize a new prompt
    demos = getattr(optimized, "demos", []) or []
    new_prompt = _build_optimized_prompt(recipe.prompt, demos)

    # Save as new recipe?
    new_recipe_path = None
    if save_to is not None:
        new_recipe = Recipe.from_dict(recipe.to_dict())
        new_recipe.name = _next_optimized_name(recipe.name, save_to)
        new_recipe.description = (
            f"{recipe.description}\n\n"
            f"[Optimized via DSPy from {len(examples)} good examples; "
            f"{len(demos)} demos added.]"
        ).strip()
        new_recipe.prompt = new_prompt
        target_path = save_to / "custom" / f"{new_recipe.name}.json"
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(json.dumps(new_recipe.to_dict(), indent=2, ensure_ascii=False))
        new_recipe_path = target_path

    return OptimizationResult(
        new_prompt=new_prompt,
        n_train_examples=len(examples),
        n_demos_added=len(demos),
        notes=f"BootstrapFewShot · {len(demos)} demos selected from {len(examples)} examples",
        new_recipe_path=new_recipe_path,
    )


def _build_optimized_prompt(original_prompt: str, demos: list) -> str:
    """Take the demos DSPy chose and prepend them as worked examples in
    the recipe prompt. We keep the original instructions, then add an
    EXAMPLES section, then a TEXT marker. The recipe runner's existing
    {target} placeholder still works — we don't touch the back of the
    prompt that contains it."""
    if not demos:
        return original_prompt

    examples_section = ["", "EXAMPLES:"]
    for i, demo in enumerate(demos, 1):
        text = getattr(demo, "text", None) or demo.get("text", "")
        output = getattr(demo, "output", None) or demo.get("output", "")
        if not text or not output:
            continue
        examples_section.append("")
        examples_section.append(f"[Example {i}]")
        examples_section.append(f"INPUT: {str(text)[:500]}")
        examples_section.append(f"OUTPUT: {str(output)[:1000]}")
    examples_section.append("")

    # Insert just before the "TEXT:\n{target}" marker, otherwise append at top
    if "TEXT:" in original_prompt:
        before, _, after = original_prompt.partition("TEXT:")
        return before.rstrip() + "\n".join(examples_section) + "\nTEXT:" + after
    return "\n".join(examples_section) + "\n" + original_prompt


def _next_optimized_name(base: str, recipes_root: Path) -> str:
    """Find a free name like `<base>_optimized_v<N>`."""
    custom = recipes_root / "custom"
    n = 1
    while (custom / f"{base}_optimized_v{n}.json").exists():
        n += 1
    return f"{base}_optimized_v{n}"


def _exact_match_metric(gold, pred, trace=None):
    """A loose metric: compare prediction's JSON output to gold's JSON output.
    Both are stringified objects. Match keys + roughly-equal values."""
    try:
        gold_obj = json.loads(gold.output)
        pred_obj = json.loads(pred.output)
    except (ValueError, AttributeError, TypeError):
        return 0.0
    if not isinstance(gold_obj, dict) or not isinstance(pred_obj, dict):
        return 0.0
    if set(gold_obj.keys()) != set(pred_obj.keys()):
        return 0.0
    score = 0.0
    n = len(gold_obj)
    for k, v in gold_obj.items():
        if str(pred_obj.get(k, "")).strip() == str(v).strip():
            score += 1.0
    return score / max(n, 1)
