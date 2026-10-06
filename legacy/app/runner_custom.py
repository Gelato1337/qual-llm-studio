"""
Custom extract runner.

Drives one Recipe of type=custom_extract over a DataFrame:

  for each row:
    1. render the prompt
    2. call Ollama with json_mode
    3. parse the response
    4. validate against the output schema
    5. if invalid, build a feedback prompt and retry (up to max_retries)
    6. if still invalid: drop or keep_with_warning per recipe.on_invalid

Output is a DataFrame:
  doc_id | <field columns from recipe.output> | _validation_status | _attempts

`_validation_status` is "ok", "warning", or "error". Rows with "error"
are present only if recipe.on_invalid.fallback == "keep_with_warning";
otherwise they're dropped (but logged in raw_responses for debugging).

`raw_responses` is a parallel list of dicts, one per row attempt, suitable
for writing to raw_responses.jsonl. We collect every attempt, not just
the final one — useful for debugging and as DSPy training signal later.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import pandas as pd
from tqdm import tqdm

from .inference import OllamaClient, OllamaError
from .json_parse import parse_json
from .prompting import build_feedback_prompt, render_prompt
from .recipe import OutputSpec, Recipe


@dataclass
class _AttemptLog:
    doc_id: str
    attempt: int
    prompt: str
    raw_response: str
    parsed: Any
    errors: list[str]
    elapsed_s: float


# ---------------------------------------------------------------------------
# Item validation
# ---------------------------------------------------------------------------


def _validate_item(item: dict, output: OutputSpec) -> list[str]:
    """Validate one parsed item dict against the output schema. Return error list."""
    if not isinstance(item, dict):
        return [f"expected an object, got {type(item).__name__}"]
    errors: list[str] = []
    for f_spec in output.fields:
        ok, msg = f_spec.validate_value(item.get(f_spec.name))
        if not ok and msg:
            errors.append(msg)
    # Warn about unexpected fields, but don't fail. Models love adding extras.
    return errors


def _validate_response(parsed: Any, output: OutputSpec) -> tuple[list[dict], list[str]]:
    """Validate a parsed LLM response against the output spec.

    Returns (items, errors). `items` is the list of valid item dicts ready
    to become rows; `errors` is non-empty iff at least one validation issue
    was found.
    """
    errors: list[str] = []

    if output.shape == "single":
        item_errs = _validate_item(parsed if isinstance(parsed, dict) else {}, output)
        if item_errs:
            return [], item_errs
        return [parsed], []

    # shape == "list"
    if not isinstance(parsed, dict):
        return [], [f"expected an object with key {output.list_key!r}, got {type(parsed).__name__}"]
    items_raw = parsed.get(output.list_key)
    if items_raw is None:
        return [], [f"missing top-level key {output.list_key!r}"]
    if not isinstance(items_raw, list):
        return [], [f"{output.list_key!r} must be a list, got {type(items_raw).__name__}"]

    # Count constraint, if set
    if output.count:
        n = len(items_raw)
        if "exact" in output.count and n != output.count["exact"]:
            errors.append(f"expected exactly {output.count['exact']} items, got {n}")
        if "min" in output.count and n < output.count["min"]:
            errors.append(f"expected at least {output.count['min']} items, got {n}")
        if "max" in output.count and n > output.count["max"]:
            errors.append(f"expected at most {output.count['max']} items, got {n}")

    valid_items: list[dict] = []
    for i, item in enumerate(items_raw):
        item_errs = _validate_item(item if isinstance(item, dict) else {}, output)
        if item_errs:
            # Tag errors with item index so the feedback prompt is specific
            errors.extend(f"item[{i}]: {e}" for e in item_errs)
        else:
            valid_items.append(item)
    return valid_items, errors


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def _flatten_lists_for_table(item: dict, output: OutputSpec) -> dict:
    """Convert list-typed fields to ', '-joined strings for tabular output.

    The validator enforces lists at parse time. By the time we hit this,
    we know the lists are valid; the only job here is presentation.
    """
    out = dict(item)
    for f_spec in output.fields:
        if f_spec.type == "list" and isinstance(out.get(f_spec.name), list):
            out[f_spec.name] = ", ".join(str(v) for v in out[f_spec.name])
    return out


def run(
    docs: pd.DataFrame,
    recipe: Recipe,
    *,
    client: OllamaClient,
    model: str,
    show_progress: bool = True,
    json_mode: bool = True,
    n_parallel: int = 1,
) -> tuple[pd.DataFrame, list[dict]]:
    """Run a custom_extract recipe over `docs`.

    Returns (results_df, raw_responses).

    n_parallel:
      Number of concurrent worker threads. Each row's retry loop runs
      sequentially within its thread; threads run independently across
      rows. Default 1 = fully sequential (legacy behavior).

      Heuristic guidance:
        * CPU-only:    8
        * 1 GPU:       12-16
        * N GPUs:      12*N (capped 64)

      The runner_custom never decides this; the caller (Runs tab) does
      based on app.resources.detect().
    """
    if recipe.type != "custom_extract":
        raise ValueError(
            f"run() expected a custom_extract recipe, got {recipe.type!r}"
        )
    if recipe.output is None:
        raise ValueError("recipe.output is required for custom_extract")

    target_col = recipe.inputs.target
    compare_col = recipe.inputs.compare
    extra = recipe.inputs.extra
    output = recipe.output
    max_retries = recipe.on_invalid.max_retries
    fallback = recipe.on_invalid.fallback

    # Sanity check: target column must exist (but only if the prompt actually
    # uses {target} legacy syntax — {{column}} prompts may not need it)
    if target_col and target_col not in docs.columns:
        # Check whether the prompt actually uses legacy {target} placeholder
        if "{target}" in recipe.prompt or "{compare}" in recipe.prompt:
            raise ValueError(
                f"target column {target_col!r} not in dataframe. "
                f"Available: {list(docs.columns)}"
            )
    if compare_col and compare_col not in docs.columns:
        if "{compare}" in recipe.prompt:
            raise ValueError(
                f"compare column {compare_col!r} not in dataframe. "
                f"Available: {list(docs.columns)}"
            )

    n_parallel = max(1, int(n_parallel))

    # Build list of work units up front so we can parallelize cleanly
    work_units: list[tuple[int, str, str]] = []  # (input_idx, doc_id, base_prompt)
    for i, (_, doc_row) in enumerate(docs.iterrows()):
        doc_id = str(doc_row.get("id", doc_row.name))
        base_prompt = render_prompt(
            recipe.prompt, doc_row,
            target_col=target_col, compare_col=compare_col, extra=extra,
        )
        work_units.append((i, doc_id, base_prompt))

    if n_parallel == 1:
        # Sequential path — preserves legacy behavior exactly
        results = _run_sequential(
            work_units=work_units, output=output, client=client, model=model,
            max_retries=max_retries, json_mode=json_mode,
            recipe_name=recipe.name, show_progress=show_progress,
        )
    else:
        results = _run_parallel(
            work_units=work_units, output=output, client=client, model=model,
            max_retries=max_retries, json_mode=json_mode, n_parallel=n_parallel,
            recipe_name=recipe.name, show_progress=show_progress,
        )

    # Assemble final dataframe in input row order
    rows: list[dict] = []
    attempt_logs: list[dict] = []
    for input_idx, doc_id, items, status, attempts_used, last_errors, logs in results:
        attempt_logs.extend(logs)
        if status == "ok":
            for item in items:
                rows.append({
                    "doc_id": doc_id,
                    **_flatten_lists_for_table(item, output),
                    "_validation_status": "ok",
                    "_attempts": attempts_used,
                })
        else:
            if fallback == "keep_with_warning":
                for item in items:
                    rows.append({
                        "doc_id": doc_id,
                        **_flatten_lists_for_table(item, output),
                        "_validation_status": "warning",
                        "_attempts": attempts_used,
                        "_errors": "; ".join(last_errors),
                    })
                if not items:
                    rows.append({
                        "doc_id": doc_id,
                        "_validation_status": "error",
                        "_attempts": attempts_used,
                        "_errors": "; ".join(last_errors),
                    })
            # fallback == "drop": just don't append.

    return pd.DataFrame(rows), attempt_logs


def _run_sequential(*, work_units, output, client, model, max_retries,
                     json_mode, recipe_name, show_progress):
    """Sequential execution path. Preserves the original behavior."""
    results = []
    iterator = work_units
    if show_progress:
        iterator = tqdm(work_units, total=len(work_units), desc=recipe_name)
    for input_idx, doc_id, base_prompt in iterator:
        local_logs: list[dict] = []
        items, status, attempts_used, last_errors = _run_one_with_retries(
            doc_id=doc_id, base_prompt=base_prompt, output=output,
            client=client, model=model, max_retries=max_retries,
            json_mode=json_mode, attempt_logs=local_logs,
        )
        results.append((input_idx, doc_id, items, status, attempts_used, last_errors, local_logs))
    return results


def _run_parallel(*, work_units, output, client, model, max_retries,
                   json_mode, n_parallel, recipe_name, show_progress):
    """Concurrent execution via ThreadPoolExecutor.

    Each worker handles one row's full retry loop. Worker exceptions are
    isolated — a single bad row doesn't kill the run."""
    import concurrent.futures as cf

    def worker(unit):
        input_idx, doc_id, base_prompt = unit
        local_logs: list[dict] = []
        try:
            items, status, attempts_used, last_errors = _run_one_with_retries(
                doc_id=doc_id, base_prompt=base_prompt, output=output,
                client=client, model=model, max_retries=max_retries,
                json_mode=json_mode, attempt_logs=local_logs,
            )
            return (input_idx, doc_id, items, status, attempts_used, last_errors, local_logs)
        except Exception as exc:  # noqa: BLE001
            return (input_idx, doc_id, [], "failed", 0,
                    [f"worker exception: {type(exc).__name__}: {exc}"], local_logs)

    results = [None] * len(work_units)
    with cf.ThreadPoolExecutor(max_workers=n_parallel) as exec_:
        futures = {exec_.submit(worker, u): u[0] for u in work_units}
        if show_progress:
            pbar = tqdm(total=len(work_units),
                        desc=f"{recipe_name} [{n_parallel} threads]")
        for fut in cf.as_completed(futures):
            input_idx = futures[fut]
            results[input_idx] = fut.result()
            if show_progress:
                pbar.update(1)
        if show_progress:
            pbar.close()
    # Fill in any gaps (shouldn't happen but defensive)
    return [r for r in results if r is not None]


def _run_one_with_retries(
    *,
    doc_id: str,
    base_prompt: str,
    output: OutputSpec,
    client: OllamaClient,
    model: str,
    max_retries: int,
    json_mode: bool,
    attempt_logs: list[dict],
) -> tuple[list[dict], str, int, list[str]]:
    """Run one row through the LLM, with feedback-based retries.

    Returns (items, status, attempts_used, last_errors).
    status is "ok" if the final attempt validated, "failed" otherwise.
    """
    current_prompt = base_prompt
    last_errors: list[str] = []
    last_items: list[dict] = []

    for attempt in range(1, max_retries + 1):
        t0 = time.time()
        try:
            chat_result = client.chat(current_prompt, model=model, json_mode=json_mode)
            raw = chat_result.content
        except OllamaError as exc:
            attempt_logs.append({
                "doc_id": doc_id,
                "attempt": attempt,
                "prompt": current_prompt,
                "raw_response": "",
                "parsed": None,
                "errors": [f"ollama_error: {exc}"],
                "elapsed_s": time.time() - t0,
            })
            last_errors = [f"LLM call failed: {exc}"]
            # Don't try to retry on transport errors past the inference layer's
            # own retries — escalate to the next attempt with the same prompt.
            continue

        parse_result = parse_json(raw)
        if not parse_result.ok:
            errors = ["response was not valid JSON"]
            attempt_logs.append({
                "doc_id": doc_id,
                "attempt": attempt,
                "prompt": current_prompt,
                "raw_response": raw,
                "parsed": None,
                "errors": errors,
                "elapsed_s": time.time() - t0,
            })
            current_prompt = build_feedback_prompt(base_prompt, errors)
            last_errors = errors
            last_items = []
            continue

        items, errors = _validate_response(parse_result.data, output)
        attempt_logs.append({
            "doc_id": doc_id,
            "attempt": attempt,
            "prompt": current_prompt,
            "raw_response": raw,
            "parsed": parse_result.data,
            "errors": errors,
            "elapsed_s": time.time() - t0,
        })
        last_items = items
        last_errors = errors
        if not errors:
            return items, "ok", attempt, []
        # Build feedback prompt for the next attempt
        current_prompt = build_feedback_prompt(base_prompt, errors)

    return last_items, "failed", max_retries, last_errors
