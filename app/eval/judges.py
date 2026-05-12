"""LLM-as-judge.

Wraps an Ollama client to score one row at a time against a rubric. Used
in parallel with the human judge — the human and the LLM see the same
samples and produce judgments, then the eval tab shows agreement metrics
and disagreements.

Design choices:
  * Judge gets the original input AND the model's structured output for
    that row. Two columns of context.
  * Output is one of the recipe's `eval.llm_judge.label_options`. We
    enforce that with the validation-feedback retry loop already used by
    the custom_extract runner.
  * Judge model is configurable separately from the extraction model
    (see inference_state["judge_model"]).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pandas as pd

from ..inference import OllamaClient
from ..json_parse import parse_json


JUDGE_PROMPT_TEMPLATE = """You are evaluating an LLM's output for a qualitative-research task.

CRITERIA:
{criteria}

LABEL OPTIONS:
{labels}

ORIGINAL INPUT:
{input}

LLM OUTPUT (the row to judge):
{output}

Decide which label fits best, and briefly explain.

Reply with JSON only:
{{"label": "<one of the labels above>", "confidence": <0.0-1.0>, "comment": "<one short sentence>"}}
"""


@dataclass
class JudgeVerdict:
    label: str
    confidence: float | None
    comment: str
    raw_response: str


def _format_input(row: pd.Series, target_col: str | None) -> str:
    """Render the original document for the judge. Pull from `target_col`
    if available, fall back to the first text-like column."""
    if target_col and target_col in row.index:
        val = row[target_col]
        if val is not None and str(val).strip():
            return str(val)
    # Fallback: any text-ish column
    for col in row.index:
        if col in {"doc_id", "id", "_validation_status", "_attempts"}:
            continue
        val = row.get(col)
        if isinstance(val, str) and len(val) > 30:
            return val
    return "(no input column found)"


def _format_output(row: pd.Series) -> str:
    """Render the LLM's structured output for the judge. Drops noise
    columns like _validation_status."""
    drop = {"doc_id", "id", "_validation_status", "_attempts", "_errors",
            "grounding_score", "grounded"}
    payload = {k: v for k, v in row.to_dict().items() if k not in drop}
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def judge_row(
    row: pd.Series,
    *,
    client: OllamaClient,
    model: str,
    criteria: str,
    label_options: list[str],
    target_col: str | None = None,
    original_row: pd.Series | None = None,
    max_retries: int = 3,
) -> JudgeVerdict:
    """Run the LLM judge on one (row of results, optional row of source).

    `row` is from the recipe's results dataframe.
    `original_row` is the corresponding source row (for the input column).
    `target_col` is the recipe's input column name.

    On parse failure or invalid label, retries with a feedback prompt up
    to `max_retries` times. On final failure, returns a verdict with
    label="ERROR" so the UI can surface it.
    """
    src = original_row if original_row is not None else row
    base_prompt = JUDGE_PROMPT_TEMPLATE.format(
        criteria=criteria.strip() or "(no criteria provided)",
        labels="\n".join(f"- {l}" for l in label_options),
        input=_format_input(src, target_col)[:4000],
        output=_format_output(row)[:2000],
    )

    label_set = set(label_options)
    current_prompt = base_prompt
    last_response = ""

    for attempt in range(max_retries + 1):
        try:
            result = client.chat(current_prompt, model=model, json_mode=True)
        except Exception as exc:  # noqa: BLE001
            return JudgeVerdict(
                label="ERROR",
                confidence=None,
                comment=f"LLM call failed: {exc}",
                raw_response="",
            )

        last_response = result.content
        parsed = parse_json(result.content)
        errors: list[str] = []

        if not parsed.ok or not isinstance(parsed.data, dict):
            errors.append("response was not a valid JSON object")
        else:
            data = parsed.data
            if "label" not in data:
                errors.append("missing 'label' field")
            elif str(data["label"]) not in label_set:
                errors.append(
                    f"label {data['label']!r} is not one of {label_options}"
                )

        if not errors and isinstance(parsed.data, dict):
            data = parsed.data
            label = str(data["label"])
            confidence = data.get("confidence")
            if confidence is not None:
                try:
                    confidence = float(confidence)
                    confidence = max(0.0, min(1.0, confidence))
                except (TypeError, ValueError):
                    confidence = None
            comment = str(data.get("comment", "")).strip()
            return JudgeVerdict(
                label=label,
                confidence=confidence,
                comment=comment,
                raw_response=result.content,
            )

        # Build feedback prompt for retry
        if attempt < max_retries:
            current_prompt = (
                "PREVIOUS ATTEMPT FAILED VALIDATION:\n"
                + "\n".join(f"- {e}" for e in errors)
                + "\n\nTry again, keeping strictly to the schema.\n\n"
                + base_prompt
            )

    # All retries exhausted
    return JudgeVerdict(
        label="ERROR",
        confidence=None,
        comment="LLM judge could not produce valid response after retries",
        raw_response=last_response,
    )
