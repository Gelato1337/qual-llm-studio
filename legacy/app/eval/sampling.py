"""Sampling strategies for picking which rows the judge looks at.

Three flavors, each with a clear contract:

  random_sample(df, n, seed)
    Uniform random N rows. Used when the recipe has nothing to stratify on
    (free-text outputs like themes/summaries).

  stratified_sample(df, n, field, seed)
    Spreads picks across distinct values of `field`. If 80% of model
    outputs scored 5 and 20% are spread across 0-4, stratified sampling
    guarantees you see the rare scores too. Falls back to uniform random
    if `field` doesn't exist or has only one value.

  targeted_sample(df, value, field, n, seed)
    Returns up to N rows where `df[field] == value`. Use when the
    researcher wants to focus eval on rows with specific output (e.g.,
    "show me rows where category was 'Other' so I can check if that's
    the right call").

All three:
  * Are deterministic given a seed (reproducible eval cycles).
  * Return Lists of row IDs (the 'id' column in the results df), not row
    indices, so partitions and judgments key cleanly.
  * Return at most `n` ids — fewer if not enough rows match.
"""

from __future__ import annotations

import random
from collections import defaultdict
from typing import Hashable

import pandas as pd


def _row_ids(df: pd.DataFrame) -> list[str]:
    """Get row IDs, falling back to index if no 'id' column."""
    if "id" in df.columns:
        return [str(v) for v in df["id"].tolist()]
    if "doc_id" in df.columns:
        # Recipe runner emits doc_id, not id, in result frames
        return [str(v) for v in df["doc_id"].tolist()]
    return [str(i) for i in df.index.tolist()]


# ---------------------------------------------------------------------------
# Random
# ---------------------------------------------------------------------------


def random_sample(
    df: pd.DataFrame, n: int, seed: int | None = None
) -> list[str]:
    """Uniform random N rows."""
    if df is None or len(df) == 0 or n <= 0:
        return []
    ids = _row_ids(df)
    if n >= len(ids):
        return ids
    rng = random.Random(seed)
    return rng.sample(ids, n)


# ---------------------------------------------------------------------------
# Stratified
# ---------------------------------------------------------------------------


def stratified_sample(
    df: pd.DataFrame, n: int, field: str, seed: int | None = None,
) -> list[str]:
    """Pick N rows balanced across distinct values of `field`.

    Algorithm:
      1. Group rows by `field`. Skip null/missing.
      2. Allocate N/K picks per group (K = number of groups).
      3. If a group has fewer than allocated, take all and redistribute
         the leftover to the larger groups.
      4. Within each group, uniform random sample.

    Returns up to N IDs total. Falls back to random_sample when the
    field is missing, all-null, or has only one distinct value.
    """
    if df is None or len(df) == 0 or n <= 0:
        return []
    if field not in df.columns:
        return random_sample(df, n, seed)

    rng = random.Random(seed)
    ids = _row_ids(df)

    # Group ids by field value, skipping nulls
    by_value: dict[Hashable, list[str]] = defaultdict(list)
    for row_id, val in zip(ids, df[field].tolist()):
        if val is None or (isinstance(val, float) and pd.isna(val)):
            continue
        # Coerce numpy/pandas scalars; tuples for list-typed fields would
        # explode group count, so we serialize them
        if isinstance(val, (list, dict)):
            key = str(val)
        else:
            key = val
        by_value[key].append(row_id)

    if not by_value or len(by_value) == 1:
        return random_sample(df, n, seed)

    # Allocate
    groups = list(by_value.items())
    rng.shuffle(groups)  # unbias allocation order across runs

    picked: list[str] = []
    leftover_capacity: list[tuple[Hashable, list[str]]] = []

    if len(groups) >= n:
        # More groups than picks — sample one row per (random) group, capped at n
        for key, group_ids in groups[:n]:
            picked.append(rng.choice(group_ids))
        # Don't redistribute — we're already at capacity
        return picked

    # Otherwise: take per_group_target from each group, then fill deficit
    per_group_target = max(1, n // len(groups))
    for key, group_ids in groups:
        take = min(per_group_target, len(group_ids))
        if take > 0:
            sampled = rng.sample(group_ids, take)
            picked.extend(sampled)
            remaining = [i for i in group_ids if i not in set(sampled)]
            if remaining:
                leftover_capacity.append((key, remaining))

    # Second pass: fill the deficit from groups that still have capacity
    deficit = n - len(picked)
    while deficit > 0 and leftover_capacity:
        # Pick from the largest remaining group to avoid imbalance
        leftover_capacity.sort(key=lambda kv: -len(kv[1]))
        key, remaining = leftover_capacity[0]
        if not remaining:
            leftover_capacity.pop(0)
            continue
        chosen = rng.choice(remaining)
        picked.append(chosen)
        remaining.remove(chosen)
        deficit -= 1

    return picked


# ---------------------------------------------------------------------------
# Targeted
# ---------------------------------------------------------------------------


def targeted_sample(
    df: pd.DataFrame,
    field: str,
    value: Hashable,
    n: int,
    seed: int | None = None,
) -> list[str]:
    """Up to N rows where df[field] == value."""
    if df is None or len(df) == 0 or n <= 0:
        return []
    if field not in df.columns:
        return []

    # Direct equality match. For numeric fields where the user may pass a
    # string from the UI, fall back to string comparison.
    matches = df[df[field] == value]
    if len(matches) == 0:
        try:
            matches = df[df[field].astype(str) == str(value)]
        except Exception:  # noqa: BLE001
            return []

    matched_ids = _row_ids(matches)
    if n >= len(matched_ids):
        return matched_ids
    rng = random.Random(seed)
    return rng.sample(matched_ids, n)


# ---------------------------------------------------------------------------
# Convenience: pick the right strategy based on recipe.eval block
# ---------------------------------------------------------------------------


def auto_sample(
    df: pd.DataFrame,
    n: int,
    *,
    stratify_by: str | None = None,
    target_field: str | None = None,
    target_value: Hashable | None = None,
    seed: int | None = None,
) -> tuple[list[str], str]:
    """Pick the right strategy and return (ids, strategy_name).

    Precedence:
      target_field + target_value -> targeted
      stratify_by                 -> stratified
      else                        -> random
    """
    if target_field is not None and target_value is not None:
        return (
            targeted_sample(df, target_field, target_value, n, seed),
            f"targeted({target_field}={target_value!r})",
        )
    if stratify_by:
        return stratified_sample(df, n, stratify_by, seed), f"stratified({stratify_by})"
    return random_sample(df, n, seed), "random"


# ---------------------------------------------------------------------------
# Partition assignment
# ---------------------------------------------------------------------------


def assign_partitions(
    df: pd.DataFrame,
    *,
    test_fraction: float = 0.0,
    holdout_fraction: float = 0.0,
    seed: int | None = 42,
) -> dict[str, str]:
    """Assign each row to dev / test / holdout, deterministically.

    By default everything goes to dev — testset is opt-in. Once the
    researcher creates a testset, they pick a fraction here.

    holdout is for rows the recipe author wants to never touch. Currently
    unused but reserved.
    """
    if df is None or len(df) == 0:
        return {}
    rng = random.Random(seed)
    ids = _row_ids(df)
    shuffled = list(ids)
    rng.shuffle(shuffled)

    n = len(shuffled)
    n_test = int(round(n * test_fraction))
    n_holdout = int(round(n * holdout_fraction))
    n_test = min(n_test, n - n_holdout)

    assignments: dict[str, str] = {}
    i = 0
    for _ in range(n_test):
        assignments[shuffled[i]] = "test"
        i += 1
    for _ in range(n_holdout):
        assignments[shuffled[i]] = "holdout"
        i += 1
    while i < n:
        assignments[shuffled[i]] = "dev"
        i += 1
    return assignments
