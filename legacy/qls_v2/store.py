"""SQLite store: immutable sources, runs, an append-only event log, and a graph view.

The event log is the source of truth. Every write by an agent or a human is one
event; nothing is ever deleted. The graph (objects + links) of a run is a
materialised view built by applying the run's *effective log* in order:

    effective_log(run) = effective_log(parent) up to fork_at  +  run's own events

so a fork shares history up to any event and diverges after it (like a git
branch), and `replay` rebuilds the view from scratch to prove it is a pure
function of the log.

Applying an event never validates: validation happens before an event is
written (see tools.py). Replaying a valid log therefore always succeeds.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterator

from .util import QlsError, dumps, now, sha256_text

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
  id TEXT NOT NULL, version INTEGER NOT NULL, sha TEXT NOT NULL, title TEXT, meta TEXT NOT NULL,
  text TEXT NOT NULL, units TEXT NOT NULL, created TEXT NOT NULL,
  PRIMARY KEY (id, version)
);
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, parent TEXT, fork_at INTEGER, recipe TEXT NOT NULL, recipe_hash TEXT NOT NULL,
  config TEXT NOT NULL, created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, run TEXT NOT NULL, kind TEXT NOT NULL, actor TEXT NOT NULL,
  payload TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', model TEXT, ts TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_run ON events(run, seq);
CREATE TABLE IF NOT EXISTS objects (
  run TEXT NOT NULL, id TEXT NOT NULL, type TEXT NOT NULL, fields TEXT NOT NULL,
  status TEXT NOT NULL, created_seq INTEGER NOT NULL, changed_seq INTEGER NOT NULL, superseded_by TEXT,
  PRIMARY KEY (run, id)
);
CREATE INDEX IF NOT EXISTS objects_type ON objects(run, type, status);
CREATE TABLE IF NOT EXISTS links (
  run TEXT NOT NULL, src TEXT NOT NULL, rel TEXT NOT NULL, dst TEXT NOT NULL, reason TEXT NOT NULL,
  seq INTEGER NOT NULL, active INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS links_src ON links(run, src, rel);
CREATE INDEX IF NOT EXISTS links_dst ON links(run, dst, rel);
CREATE TABLE IF NOT EXISTS counters (run TEXT NOT NULL, prefix TEXT NOT NULL, n INTEGER NOT NULL, PRIMARY KEY (run, prefix));
CREATE TABLE IF NOT EXISTS run_state (run TEXT PRIMARY KEY, status TEXT NOT NULL, checkpoint_seq INTEGER);
"""

EVENT_KINDS = ("create", "link", "unlink", "update", "supersede", "checkpoint", "human", "resume", "note")


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")  # many agents may read and write at once
        self.db.execute("PRAGMA busy_timeout=30000")
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    def tx(self):
        """BEGIN IMMEDIATE: one writer at a time across processes."""
        return _Tx(self.db)

    # -- sources ----------------------------------------------------------------

    def add_source(self, sid: str, text: str, units: list[dict], title: str = "", meta: dict | None = None) -> int:
        """Store a source; identical text returns the existing version, changed text a new one."""
        sha = sha256_text(text)
        row = self.db.execute("SELECT version, sha FROM sources WHERE id=? ORDER BY version DESC LIMIT 1", (sid,)).fetchone()
        if row and row["sha"] == sha:
            return row["version"]
        version = (row["version"] + 1) if row else 1
        self.db.execute("INSERT INTO sources VALUES (?,?,?,?,?,?,?,?)",
                        (sid, version, sha, title, dumps(meta or {}), text, dumps(units), now()))
        return version

    def source(self, sid: str, version: int | None = None) -> dict:
        q = "SELECT * FROM sources WHERE id=? " + ("AND version=?" if version else "ORDER BY version DESC LIMIT 1")
        row = self.db.execute(q, (sid, version) if version else (sid,)).fetchone()
        if not row:
            known = [r["id"] for r in self.db.execute("SELECT DISTINCT id FROM sources ORDER BY id")]
            raise QlsError(f"Unknown source {sid!r}" + (f" version {version}" if version else "")
                           + f". Sources: {', '.join(known[:30]) or 'none'}")
        d = dict(row)
        d["meta"], d["units"] = json.loads(d["meta"]), json.loads(d["units"])
        return d

    def source_ids(self) -> list[str]:
        return [r["id"] for r in self.db.execute("SELECT DISTINCT id FROM sources ORDER BY id")]

    def has_source(self, sid: str) -> bool:
        return self.db.execute("SELECT 1 FROM sources WHERE id=?", (sid,)).fetchone() is not None

    # -- runs -------------------------------------------------------------------

    def create_run(self, rid: str, recipe: str, recipe_hash: str, config: dict, parent: str | None = None,
                   fork_at: int | None = None) -> None:
        if self.db.execute("SELECT 1 FROM runs WHERE id=?", (rid,)).fetchone():
            raise QlsError(f"Run {rid!r} already exists")
        with self.tx():
            self.db.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?)", (rid, parent, fork_at, recipe, recipe_hash, dumps(config), now()))
            self.db.execute("INSERT INTO run_state VALUES (?,?,?)", (rid, "open", None))
        if parent:
            self.rebuild(rid)

    def run(self, rid: str) -> dict:
        row = self.db.execute("SELECT r.*, s.status, s.checkpoint_seq FROM runs r JOIN run_state s ON s.run=r.id WHERE r.id=?", (rid,)).fetchone()
        if not row:
            known = [r["id"] for r in self.db.execute("SELECT id FROM runs ORDER BY created")]
            raise QlsError(f"Unknown run {rid!r}. Runs: {', '.join(known) or 'none'}")
        d = dict(row)
        d["config"] = json.loads(d["config"])
        return d

    def runs(self) -> list[dict]:
        return [self.run(r["id"]) for r in self.db.execute("SELECT id FROM runs ORDER BY created, id")]

    def set_status(self, rid: str, status: str, checkpoint_seq: int | None = None) -> None:
        self.db.execute("UPDATE run_state SET status=?, checkpoint_seq=COALESCE(?, checkpoint_seq) WHERE run=?",
                        (status, checkpoint_seq, rid))

    # -- events -----------------------------------------------------------------

    def append(self, rid: str, kind: str, actor: str, payload: dict, reason: str = "", model: str | None = None) -> int:
        """Write one event and apply it to the run's graph, atomically."""
        if kind not in EVENT_KINDS:
            raise QlsError(f"unknown event kind {kind!r}")
        with self.tx():
            cur = self.db.execute("INSERT INTO events (run, kind, actor, payload, reason, model, ts) VALUES (?,?,?,?,?,?,?)",
                                  (rid, kind, actor, dumps(payload), reason or "", model, now()))
            seq = cur.lastrowid
            self._apply(rid, {"seq": seq, "kind": kind, "actor": actor, "payload": payload, "reason": reason or ""})
        return seq

    def lineage(self, rid: str) -> list[tuple[str, int | None]]:
        """[(run, max_seq)] from the root to rid; max_seq limits what a fork inherits."""
        chain, cur, limit = [], rid, None
        while cur:
            chain.append((cur, limit))
            r = self.db.execute("SELECT parent, fork_at FROM runs WHERE id=?", (cur,)).fetchone()
            cur, new_limit = (r["parent"], r["fork_at"]) if r else (None, None)
            if new_limit is not None:
                limit = new_limit if limit is None else min(limit, new_limit)
        return list(reversed(chain))

    def effective_events(self, rid: str) -> list[dict]:
        out = []
        for run_id, limit in self.lineage(rid):
            q = "SELECT * FROM events WHERE run=?" + (" AND seq<=?" if limit is not None else "")
            for row in self.db.execute(q + " ORDER BY seq", (run_id, limit) if limit is not None else (run_id,)):
                out.append(_event(row))
        return sorted(out, key=lambda e: e["seq"])

    def events(self, rid: str, since: int = 0, own_only: bool = False) -> list[dict]:
        evs = ([_event(r) for r in self.db.execute("SELECT * FROM events WHERE run=? ORDER BY seq", (rid,))]
               if own_only else self.effective_events(rid))
        return [e for e in evs if e["seq"] > since]

    # -- graph view -------------------------------------------------------------

    def rebuild(self, rid: str) -> None:
        """Rebuild the run's graph from its effective log (replay)."""
        with self.tx():
            for t in ("objects", "links", "counters"):
                self.db.execute(f"DELETE FROM {t} WHERE run=?", (rid,))
            for ev in self.effective_events(rid):
                self._apply(rid, ev)

    def _apply(self, rid: str, ev: dict) -> None:
        k, p, seq = ev["kind"], ev["payload"], ev["seq"]
        db = self.db
        if k == "create":
            db.execute("INSERT INTO objects VALUES (?,?,?,?,?,?,?,NULL)", (rid, p["id"], p["type"], dumps(p.get("fields", {})), "active", seq, seq))
            prefix, n = p["id"].split("-", 1)
            if n.isdigit():
                db.execute("INSERT INTO counters VALUES (?,?,?) ON CONFLICT(run, prefix) DO UPDATE SET n=MAX(n, excluded.n)",
                           (rid, prefix, int(n)))
            for ln in p.get("links", []):
                db.execute("INSERT INTO links VALUES (?,?,?,?,?,?,1)", (rid, p["id"], ln["rel"], ln["to"], ln.get("reason", ""), seq))
        elif k == "link":
            db.execute("INSERT INTO links VALUES (?,?,?,?,?,?,1)", (rid, p["src"], p["rel"], p["dst"], ev["reason"], seq))
        elif k == "unlink":
            db.execute("UPDATE links SET active=0 WHERE run=? AND src=? AND rel=? AND dst=? AND active=1", (rid, p["src"], p["rel"], p["dst"]))
        elif k == "update":
            row = db.execute("SELECT fields FROM objects WHERE run=? AND id=?", (rid, p["id"])).fetchone()
            fields = json.loads(row["fields"]) if row else {}
            fields.update(p["fields"])
            db.execute("UPDATE objects SET fields=?, changed_seq=? WHERE run=? AND id=?", (dumps(fields), seq, rid, p["id"]))
        elif k == "supersede":
            for old in p["old"]:
                db.execute("UPDATE objects SET status=?, superseded_by=?, changed_seq=? WHERE run=? AND id=?",
                           (p.get("status", "superseded"), ",".join(p.get("new", [])) or None, seq, rid, old))
                # links *from* a superseded object stop counting; links *to* it move to the replacement
                db.execute("UPDATE links SET active=0 WHERE run=? AND src=? AND active=1", (rid, old))
                if p.get("new") and p.get("move_incoming", True):
                    for row in db.execute("SELECT src, rel, reason FROM links WHERE run=? AND dst=? AND active=1", (rid, old)).fetchall():
                        db.execute("UPDATE links SET active=0 WHERE run=? AND src=? AND rel=? AND dst=? AND active=1", (rid, row["src"], row["rel"], old))
                        new = p["new"][0]
                        dup = db.execute("SELECT 1 FROM links WHERE run=? AND src=? AND rel=? AND dst=? AND active=1",
                                         (rid, row["src"], row["rel"], new)).fetchone()
                        if not dup:
                            db.execute("INSERT INTO links VALUES (?,?,?,?,?,?,1)", (rid, row["src"], row["rel"], new, row["reason"], seq))
        # checkpoint / human / resume / note change no graph objects; their effects are separate events

    # -- queries ----------------------------------------------------------------

    def obj(self, rid: str, oid: str) -> dict | None:
        row = self.db.execute("SELECT * FROM objects WHERE run=? AND id=?", (rid, oid)).fetchone()
        return _obj(row) if row else None

    def objects(self, rid: str, type_: str | None = None, status: str | None = "active") -> list[dict]:
        q, args = "SELECT * FROM objects WHERE run=?", [rid]
        if type_:
            q, args = q + " AND type=?", args + [type_]
        if status:
            q, args = q + " AND status=?", args + [status]
        rows = self.db.execute(q + " ORDER BY created_seq", args).fetchall()
        return [_obj(r) for r in rows]

    def links(self, rid: str, src: str | None = None, dst: str | None = None, rel: str | None = None,
              active: bool = True) -> list[dict]:
        q, args = "SELECT * FROM links WHERE run=?", [rid]
        for col, val in (("src", src), ("dst", dst), ("rel", rel)):
            if val is not None:
                q, args = q + f" AND {col}=?", args + [val]
        if active:
            q += " AND active=1"
        return [dict(r) for r in self.db.execute(q + " ORDER BY seq", args)]

    def next_id(self, rid: str, prefix: str) -> str:
        row = self.db.execute("SELECT n FROM counters WHERE run=? AND prefix=?", (rid, prefix)).fetchone()
        return f"{prefix}-{(row['n'] if row else 0) + 1}"

    def state_hash(self, rid: str) -> str:
        """Fingerprint of a run's graph, independent of how it was built."""
        objs = [(o["id"], o["type"], dumps(o["fields"]), o["status"]) for o in self.objects(rid, status=None)]
        lns = sorted((l["src"], l["rel"], l["dst"], l["reason"], l["active"]) for l in self.links(rid, active=False))
        return sha256_text(dumps([sorted(objs), lns]))


class _Tx:
    def __init__(self, db):
        self.db = db
        self.nested = db.in_transaction

    def __enter__(self):
        if not self.nested:
            self.db.execute("BEGIN IMMEDIATE")
        return self

    def __exit__(self, exc_type, exc, tb):
        if not self.nested:
            self.db.execute("ROLLBACK" if exc_type else "COMMIT")


def _event(row) -> dict:
    d = dict(row)
    d["payload"] = json.loads(d["payload"])
    return d


def _obj(row) -> dict:
    d = dict(row)
    d["fields"] = json.loads(d["fields"])
    return d


def iter_chunks(xs: list, n: int) -> Iterator[list]:
    for i in range(0, len(xs), n):
        yield xs[i:i + n]
