"""SQLite-backed storage for eval judgments.

One database per eval session, at:
    runs/<original_run>/eval/<session_id>/judgments.db

Schema is small but explicit — every table has its own primary key, every
write is timestamped. Multiple judges (a human user, an LLM) can produce
judgments on the same row; we don't deduplicate. The UI aggregates them
for display and DSPy reads them as parallel labels.

Why sqlite, not parquet:
  * Multi-writer scenarios: human types feedback while LLM judge is running
  * Easy point-edits ("change my judgment of row 17 from bad to good")
  * Natural for filtering ("show me rows where human and LLM disagree")
  * Tolerates ad-hoc schema additions later (judge confidence, time spent, etc.)

Tables:
  partitions   — which rows are devset / testset / testset-locked
  samples      — which rows are picked into the current sampling cycle
  judgments    — labels collected so far
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterator


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


SCHEMA = """
CREATE TABLE IF NOT EXISTS partitions (
    row_id      TEXT PRIMARY KEY,
    partition   TEXT NOT NULL CHECK(partition IN ('dev','test','holdout')),
    assigned_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS samples (
    sample_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle        INTEGER NOT NULL,
    row_id       TEXT NOT NULL,
    partition    TEXT NOT NULL,
    strategy     TEXT NOT NULL,
    picked_at    REAL NOT NULL,
    UNIQUE(cycle, row_id)
);

CREATE INDEX IF NOT EXISTS idx_samples_cycle ON samples(cycle);

CREATE TABLE IF NOT EXISTS judgments (
    judgment_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    row_id        TEXT NOT NULL,
    judge_id      TEXT NOT NULL,
    judge_kind    TEXT NOT NULL CHECK(judge_kind IN ('human','llm')),
    label         TEXT NOT NULL,
    confidence    REAL,                -- 0.0 - 1.0, optional
    comment       TEXT,
    cycle         INTEGER,
    extra_json    TEXT,                -- arbitrary blob for forward-compat
    created_at    REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_judgments_row ON judgments(row_id);
CREATE INDEX IF NOT EXISTS idx_judgments_judge ON judgments(judge_id);

CREATE TABLE IF NOT EXISTS testset_locks (
    locked_at  REAL PRIMARY KEY,
    note       TEXT
);
"""


# ---------------------------------------------------------------------------
# DataClasses
# ---------------------------------------------------------------------------


@dataclass
class Judgment:
    row_id: str
    judge_id: str
    judge_kind: str            # "human" or "llm"
    label: str
    confidence: float | None = None
    comment: str | None = None
    cycle: int | None = None
    extra: dict | None = None


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class JudgmentStore:
    """Thin wrapper over sqlite for one eval session."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        # New connection per logical operation — sqlite handles concurrency
        # via its own locking. Good enough for one-process workshop use.
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._conn() as c:
            c.executescript(SCHEMA)

    # ---- Partitions ------------------------------------------------------

    def assign_partitions(self, assignments: dict[str, str]) -> None:
        """Bulk-assign rows to partitions. assignments is {row_id: 'dev'|'test'|'holdout'}."""
        now = time.time()
        with self._conn() as c:
            c.executemany(
                "INSERT OR REPLACE INTO partitions(row_id, partition, assigned_at) VALUES (?, ?, ?)",
                [(rid, p, now) for rid, p in assignments.items()],
            )

    def get_partition(self, row_id: str) -> str | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT partition FROM partitions WHERE row_id = ?", (row_id,)
            ).fetchone()
            return row["partition"] if row else None

    def list_by_partition(self, partition: str) -> list[str]:
        with self._conn() as c:
            return [r["row_id"] for r in c.execute(
                "SELECT row_id FROM partitions WHERE partition = ? ORDER BY row_id",
                (partition,),
            )]

    def partition_counts(self) -> dict[str, int]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT partition, COUNT(*) AS n FROM partitions GROUP BY partition"
            ).fetchall()
        return {r["partition"]: r["n"] for r in rows}

    # ---- Samples (one cycle = one round of "pick N to judge") ------------

    def next_cycle(self) -> int:
        with self._conn() as c:
            row = c.execute("SELECT COALESCE(MAX(cycle), 0) AS c FROM samples").fetchone()
            return (row["c"] or 0) + 1

    def record_samples(
        self, cycle: int, row_ids: list[str], partition: str, strategy: str,
    ) -> None:
        now = time.time()
        with self._conn() as c:
            c.executemany(
                "INSERT OR IGNORE INTO samples(cycle, row_id, partition, strategy, picked_at) "
                "VALUES (?, ?, ?, ?, ?)",
                [(cycle, rid, partition, strategy, now) for rid in row_ids],
            )

    def samples_for_cycle(self, cycle: int) -> list[str]:
        with self._conn() as c:
            return [r["row_id"] for r in c.execute(
                "SELECT row_id FROM samples WHERE cycle = ? ORDER BY sample_id", (cycle,),
            )]

    # ---- Judgments -------------------------------------------------------

    def add_judgment(self, j: Judgment) -> int:
        now = time.time()
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO judgments(row_id, judge_id, judge_kind, label, confidence, "
                "comment, cycle, extra_json, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    j.row_id, j.judge_id, j.judge_kind, j.label,
                    j.confidence, j.comment, j.cycle,
                    json.dumps(j.extra) if j.extra else None,
                    now,
                ),
            )
            return cur.lastrowid

    def upsert_human_judgment(self, j: Judgment) -> int:
        """Replace any prior human judgment for (row_id, judge_id) and insert
        a fresh one. Lets a researcher change their mind without us
        accumulating 17 versions of "good" on the same row."""
        with self._conn() as c:
            c.execute(
                "DELETE FROM judgments WHERE row_id = ? AND judge_id = ? AND judge_kind = 'human'",
                (j.row_id, j.judge_id),
            )
        return self.add_judgment(j)

    def judgments_for_row(self, row_id: str) -> list[dict]:
        with self._conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM judgments WHERE row_id = ? ORDER BY created_at",
                (row_id,),
            )]

    def all_judgments(self) -> list[dict]:
        with self._conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM judgments ORDER BY created_at"
            )]

    def labeled_examples(self, judge_kind: str | None = None) -> list[dict]:
        """Pull (row_id, label) pairs suitable for DSPy training. If
        `judge_kind` is None, prefer human judgments where available, then
        LLM judgments. If a row has multiple judgments of the same kind,
        the most recent wins."""
        with self._conn() as c:
            if judge_kind:
                rows = c.execute(
                    "SELECT row_id, label, comment, judge_id, judge_kind, MAX(created_at) AS ts "
                    "FROM judgments WHERE judge_kind = ? GROUP BY row_id, judge_id "
                    "ORDER BY row_id, ts DESC",
                    (judge_kind,),
                ).fetchall()
            else:
                # Human-preferred
                rows = c.execute(
                    """
                    SELECT row_id, label, comment, judge_id, judge_kind, created_at
                    FROM judgments
                    WHERE (row_id, judge_kind, created_at) IN (
                        SELECT row_id, judge_kind, MAX(created_at)
                        FROM judgments GROUP BY row_id, judge_kind
                    )
                    ORDER BY row_id, judge_kind = 'human' DESC
                    """
                ).fetchall()
        # Dedup to one judgment per row, preferring human
        seen: set[str] = set()
        out: list[dict] = []
        for r in rows:
            if r["row_id"] in seen:
                continue
            seen.add(r["row_id"])
            out.append(dict(r))
        return out

    # ---- Testset lock ----------------------------------------------------

    def lock_testset(self, note: str = "") -> float:
        now = time.time()
        with self._conn() as c:
            c.execute("INSERT INTO testset_locks(locked_at, note) VALUES (?, ?)", (now, note))
        return now

    def is_testset_locked(self) -> bool:
        with self._conn() as c:
            row = c.execute("SELECT COUNT(*) AS n FROM testset_locks").fetchone()
            return (row["n"] or 0) > 0

    def testset_lock_info(self) -> list[dict]:
        with self._conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM testset_locks ORDER BY locked_at"
            )]
