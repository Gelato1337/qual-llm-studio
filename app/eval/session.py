"""EvalSession — orchestrates eval over a recipe run.

One session per (run, eval-cycle). Holds:
  * the run we're evaluating (path)
  * the results df
  * the optional source df (so the judge can see original input text)
  * the recipe (so we know eval criteria, target column, label options)
  * a JudgmentStore (sqlite for partitions and judgments)

Public methods:
  * partition(test_fraction) — assign rows to dev/test
  * sample(n, mode, ...)     — pick rows for one eval cycle
  * record_human_judgment(row_id, label, comment, judge_id)
  * run_llm_judge(rows, client, model)
  * agreement_metrics()      — Cohen's kappa, exact-match rate
  * lock_testset()           — opt-in nuclear-launch
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from . import judges, sampling, storage
from .storage import Judgment


@dataclass
class AgreementStats:
    n_compared: int
    exact_match_rate: float
    cohens_kappa: float | None
    confusion: dict[tuple[str, str], int]


class EvalSession:
    """Live orchestrator for one eval session."""

    def __init__(
        self,
        run_folder: str | Path,
        results_df: pd.DataFrame,
        recipe_dict: dict,
        *,
        source_df: pd.DataFrame | None = None,
    ):
        self.run_folder = Path(run_folder)
        self.results_df = results_df.reset_index(drop=True)
        self.recipe_dict = recipe_dict
        self.source_df = source_df

        eval_root = self.run_folder / "eval"
        eval_root.mkdir(parents=True, exist_ok=True)
        self.store = storage.JudgmentStore(eval_root / "judgments.db")

    # ---- Partitions ------------------------------------------------------

    def partition(self, *, test_fraction: float = 0.0, seed: int | None = 42) -> dict[str, int]:
        """Assign every row to a partition. Returns {partition: count}.

        Default behavior (test_fraction=0): everything is dev. Researcher
        opts in to a testset later via the dedicated UI button.
        """
        assignments = sampling.assign_partitions(
            self.results_df, test_fraction=test_fraction, seed=seed,
        )
        self.store.assign_partitions(assignments)
        return self.store.partition_counts()

    # ---- Sampling --------------------------------------------------------

    def sample(
        self,
        n: int,
        *,
        partition: str = "dev",
        mode: str = "auto",
        target_field: str | None = None,
        target_value: Any = None,
        seed: int | None = None,
    ) -> tuple[list[str], str, int]:
        """Pick a fresh cycle of rows to judge.

        Returns (row_ids, strategy_used, cycle_number).
        """
        # Restrict df to the named partition
        partition_ids = set(self.store.list_by_partition(partition))
        if not partition_ids:
            # Auto-partition once if the user never explicitly called it
            self.partition()
            partition_ids = set(self.store.list_by_partition(partition))

        df = self._df_for_ids(partition_ids)
        if len(df) == 0:
            return [], "empty_partition", -1

        # Pick strategy
        if mode == "targeted" and target_field and target_value is not None:
            ids, strategy = sampling.auto_sample(
                df, n, target_field=target_field, target_value=target_value, seed=seed,
            )
        elif mode == "stratified":
            stratify = self.eval_config.get("stratify_by") or self._auto_stratify_field()
            ids, strategy = sampling.auto_sample(df, n, stratify_by=stratify, seed=seed)
        elif mode == "random":
            ids, strategy = sampling.auto_sample(df, n, seed=seed)
        else:
            # auto: prefer stratified if recipe declares or auto-detects, else random
            stratify = self.eval_config.get("stratify_by") or self._auto_stratify_field()
            ids, strategy = sampling.auto_sample(df, n, stratify_by=stratify, seed=seed)

        cycle = self.store.next_cycle()
        self.store.record_samples(cycle, ids, partition, strategy)
        return ids, strategy, cycle

    def _df_for_ids(self, ids: set[str]) -> pd.DataFrame:
        if "doc_id" in self.results_df.columns:
            mask = self.results_df["doc_id"].astype(str).isin(ids)
        elif "id" in self.results_df.columns:
            mask = self.results_df["id"].astype(str).isin(ids)
        else:
            mask = pd.Series([str(i) in ids for i in self.results_df.index])
        return self.results_df[mask]

    def _auto_stratify_field(self) -> str | None:
        """Find a sensible default field to stratify on.

        Heuristics:
          1. Recipe says so explicitly via eval.stratify_by — handled by caller.
          2. Output schema has an enum field → use that.
          3. Output schema has a number field → use that.
          4. None → fall back to random.
        """
        # custom_extract recipes carry an output schema
        if self.recipe_dict.get("type") != "custom_extract":
            return None
        fields = self.recipe_dict.get("output", {}).get("fields", [])
        for f in fields:
            if f.get("type") == "enum" and f["name"] in self.results_df.columns:
                return f["name"]
        for f in fields:
            if f.get("type") == "number" and f["name"] in self.results_df.columns:
                return f["name"]
        return None

    # ---- Judgments -------------------------------------------------------

    def record_human_judgment(
        self,
        row_id: str,
        label: str,
        *,
        judge_id: str = "human",
        comment: str = "",
        cycle: int | None = None,
    ) -> int:
        return self.store.upsert_human_judgment(Judgment(
            row_id=row_id,
            judge_id=judge_id,
            judge_kind="human",
            label=label,
            comment=comment or None,
            cycle=cycle,
        ))

    def run_llm_judge(
        self,
        row_ids: list[str],
        *,
        client,
        model: str,
        cycle: int | None = None,
    ) -> list[dict]:
        """Run the LLM judge on a list of row ids. Records results to store.
        Returns list of verdict dicts for UI display."""
        cfg = self.eval_config
        criteria = (cfg.get("llm_judge", {}) or {}).get("criteria", "")
        labels = (cfg.get("llm_judge", {}) or {}).get("label_options", ["good", "bad"])
        if not criteria:
            criteria = "Is this output reasonable for the given input?"

        target_col = self.recipe_dict.get("inputs", {}).get("target")
        out: list[dict] = []
        for rid in row_ids:
            results_row = self._row_by_id(rid, self.results_df)
            source_row = (
                self._source_row_for_result(results_row)
                if self.source_df is not None else None
            )
            verdict = judges.judge_row(
                results_row,
                client=client,
                model=model,
                criteria=criteria,
                label_options=labels,
                target_col=target_col,
                original_row=source_row,
            )
            self.store.add_judgment(Judgment(
                row_id=rid,
                judge_id=f"llm:{model}",
                judge_kind="llm",
                label=verdict.label,
                confidence=verdict.confidence,
                comment=verdict.comment or None,
                cycle=cycle,
                extra={"raw_response": verdict.raw_response[:500]},
            ))
            out.append({
                "row_id": rid,
                "label": verdict.label,
                "confidence": verdict.confidence,
                "comment": verdict.comment,
            })
        return out

    def _row_by_id(self, rid: str, df: pd.DataFrame) -> pd.Series:
        for col in ("doc_id", "id"):
            if col in df.columns:
                match = df[df[col].astype(str) == str(rid)]
                if len(match):
                    return match.iloc[0]
        raise ValueError(f"row id {rid!r} not found in dataframe")

    def _source_row_for_result(self, results_row: pd.Series) -> pd.Series | None:
        if self.source_df is None:
            return None
        # Result rows have doc_id; source rows have id. They may match,
        # but for chunked data the result's doc_id is something like
        # "d__c003" with parent_id "d" in metadata.
        rid = results_row.get("doc_id") or results_row.get("id")
        parent = results_row.get("parent_id") if "parent_id" in results_row.index else None
        for cand in (rid, parent):
            if cand is None:
                continue
            for col in ("id", "doc_id"):
                if col not in self.source_df.columns:
                    continue
                match = self.source_df[self.source_df[col].astype(str) == str(cand)]
                if len(match):
                    return match.iloc[0]
        return None

    # ---- Agreement -------------------------------------------------------

    def agreement_metrics(self) -> AgreementStats:
        """For rows with both a human and an LLM judgment, compute exact-match
        rate and Cohen's kappa."""
        all_j = self.store.all_judgments()
        # Group by row_id, find rows with at least one human + one llm
        by_row: dict[str, dict[str, str]] = {}
        for j in all_j:
            row = by_row.setdefault(j["row_id"], {"human": None, "llm": None})
            if j["judge_kind"] == "human":
                row["human"] = j["label"]
            elif j["judge_kind"] == "llm":
                row["llm"] = j["label"]

        compared = [(r["human"], r["llm"]) for r in by_row.values()
                    if r["human"] is not None and r["llm"] is not None]

        if not compared:
            return AgreementStats(0, 0.0, None, {})

        n = len(compared)
        exact = sum(1 for h, l in compared if h == l)
        exact_rate = exact / n

        # Confusion matrix
        confusion: dict[tuple[str, str], int] = {}
        for h, l in compared:
            confusion[(h, l)] = confusion.get((h, l), 0) + 1

        kappa = self._cohens_kappa(compared)
        return AgreementStats(n, exact_rate, kappa, confusion)

    @staticmethod
    def _cohens_kappa(pairs: list[tuple[str, str]]) -> float | None:
        """Compute unweighted Cohen's kappa for two raters' categorical labels."""
        if not pairs:
            return None
        labels = sorted({l for pair in pairs for l in pair})
        n = len(pairs)
        # Observed agreement
        po = sum(1 for h, l in pairs if h == l) / n
        # Expected agreement
        from collections import Counter
        h_count = Counter(p[0] for p in pairs)
        l_count = Counter(p[1] for p in pairs)
        pe = sum((h_count.get(c, 0) / n) * (l_count.get(c, 0) / n) for c in labels)
        if pe == 1.0:
            return 1.0 if po == 1.0 else 0.0
        return (po - pe) / (1 - pe)

    # ---- Testset lock ----------------------------------------------------

    def is_testset_locked(self) -> bool:
        return self.store.is_testset_locked()

    def lock_testset(self, note: str = "") -> float:
        return self.store.lock_testset(note=note)

    # ---- Recipe eval config helpers --------------------------------------

    @property
    def eval_config(self) -> dict:
        return self.recipe_dict.get("eval", {}) or {}

    @property
    def has_llm_judge(self) -> bool:
        cfg = self.eval_config
        return bool(cfg.get("llm_judge", {}).get("criteria"))

    @property
    def human_label_options(self) -> list[str]:
        cfg = self.eval_config
        opts = cfg.get("judge_label_options")
        if opts:
            return list(opts)
        return ["good", "bad", "borderline"]

    # ---- Snapshot for the UI ---------------------------------------------

    def get_eval_view(self, row_ids: list[str]) -> pd.DataFrame:
        """Build a DataFrame the UI can display: row content + any judgments
        already collected.

        Rows come back in the same order as `row_ids`."""
        if not row_ids:
            return pd.DataFrame()

        # Build a lookup so we can preserve the caller's order
        df = self._df_for_ids(set(row_ids))
        if "doc_id" in df.columns:
            id_col = "doc_id"
        elif "id" in df.columns:
            id_col = "id"
        else:
            id_col = None
        if id_col:
            df = df.set_index(df[id_col].astype(str))

        rows = []
        for rid in row_ids:
            try:
                r = df.loc[str(rid)]
                if isinstance(r, pd.DataFrame):
                    r = r.iloc[0]
            except KeyError:
                continue
            j = self.store.judgments_for_row(str(rid))
            human = next((x for x in j if x["judge_kind"] == "human"), None)
            llm = next((x for x in j if x["judge_kind"] == "llm"), None)
            row_dict = r.to_dict()
            if id_col and id_col not in row_dict:
                row_dict[id_col] = str(rid)
            rows.append({
                **row_dict,
                "human_label": human["label"] if human else "",
                "human_comment": human.get("comment", "") if human else "",
                "llm_label": llm["label"] if llm else "",
                "llm_confidence": llm.get("confidence") if llm else None,
                "llm_comment": llm.get("comment", "") if llm else "",
            })
        return pd.DataFrame(rows)
