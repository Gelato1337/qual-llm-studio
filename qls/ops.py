"""Structural operations on a run, each one logged as a decision record.

This is the commit boundary: humans, agents (pi, Claude Cowork) and the
fixed pipeline stages all change the analysis only through these functions.
Every call validates its inputs and appends a decision record:

    {"id", "run_id", "stage", "actor", "model", "prompt_hash",
     "op", "target", "inputs", "outputs", "reason", "detail", "ts"}

Inputs/outputs are IDs. "detail" carries labels (never quote text) so the
log is readable on its own.

Merges and splits are lossless by construction: quotes move to the new
concept(s), and a split must place every quote. (The AMCIS pipeline lost
concepts during merging in ~3 of 4 runs; here that cannot happen.)
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterable

from .project import QlsError, Run, now

STAGE = {"concept": 2, "theme": 3, "dimension": 4, "memo": None, "corpus": 0}

# Memo kinds: what the note is for. Kept small so memos stay queryable.
MEMO_KINDS = ("analytic", "boundary", "surprise", "counter", "alternative", "decision", "summary")


class Ops:
    def __init__(self, run: Run, actor: str = "human", model: str | None = None, prompt_hash: str | None = None):
        if not actor or not actor.strip():
            raise QlsError("actor is required (e.g. 'human', 'agent:cowork', 'pipeline')")
        self.run = run
        self.actor = actor.strip()
        self.model = model
        self.prompt_hash = prompt_hash
        self._state: dict | None = None
        self._pending: list[dict] = []
        self._batch = 0
        self._evidence: list[str] = []

    # -- state & commit -------------------------------------------------------

    @property
    def s(self) -> dict:
        if self._state is None:
            self._state = self.run.state()
        return self._state

    @contextmanager
    def batch(self):
        """Group many ops into one state write (used by pipeline stages)."""
        self._batch += 1
        try:
            yield self
        finally:
            self._batch -= 1
            if self._batch == 0:
                self.commit()

    def commit(self) -> None:
        if self._state is not None:
            self.run.save_state(self._state)
        for rec in self._pending:
            self.run.append_decision(rec)
        if hasattr(self, "_logged"):
            self._logged += len(self._pending)
        self._pending = []

    def _done(self) -> None:
        if self._batch == 0:
            self.commit()

    def _next(self, kind: str) -> str:
        self.s["counters"][kind] += 1
        n = self.s["counters"][kind]
        return {"c": f"c{n}", "q": f"q{n}", "t": f"t{n}", "a": f"a{n}", "m": f"m{n}"}[kind]

    def _log(self, op: str, target: str, inputs: list, outputs: list, reason: str, stage: int | None = None, **detail: Any) -> dict:
        if not hasattr(self, "_logged"):
            self._logged = len(self.run.decisions())
        n = self._logged + len(self._pending) + 1
        rec = {
            "id": f"dr{n}",
            "run_id": self.run.id,
            "stage": STAGE.get(target) if stage is None else stage,
            "actor": self.actor,
            "model": self.model,
            "prompt_hash": self.prompt_hash,
            "op": op,
            "target": target,
            "inputs": list(inputs),
            "outputs": list(outputs),
            "reason": (reason or "").strip(),
            "evidence": self._evidence,
            "detail": detail,
            "ts": now(),
        }
        self._evidence = []
        self._pending.append(rec)
        return rec

    # -- evidence ---------------------------------------------------------------

    def known(self, ref: str) -> bool:
        """True if ref names something in this run or the corpus."""
        s = self.s
        if ref in s["concepts"] or ref in s["themes"] or ref in s["dimensions"] or ref in s["quotes"] or ref in s["memos"]:
            return True
        proj = self.run.project
        doc_id = ref.split(":", 1)[0]
        if doc_id not in proj.doc_ids():
            return False
        if ":" not in ref:
            return True
        d = proj.doc(doc_id)
        return any(u["id"] == ref for u in d["segments"]) or any(u["id"] == ref for u in d["turns"])

    def _check_refs(self, refs: list[str] | None, what: str) -> list[str]:
        refs = list(dict.fromkeys(refs or []))
        bad = [r for r in refs if not self.known(r)]
        if bad:
            raise QlsError(f"Unknown {what}: {', '.join(bad)} (use concept/theme/dimension/quote/memo IDs, documents, turns or segments)")
        return refs

    def with_evidence(self, refs: list[str] | None) -> "Ops":
        """Attach evidence (segment, quote, memo... IDs) to the next decision record."""
        self._evidence = self._check_refs(refs, "evidence")
        return self

    @staticmethod
    def _need_reason(reason: str | None) -> str:
        if not reason or not reason.strip():
            raise QlsError("A one-line reason is required for every decision.")
        return reason.strip()

    # -- lookups --------------------------------------------------------------

    def concept(self, cid: str, active: bool = True) -> dict:
        c = self.s["concepts"].get(cid)
        if c is None:
            raise QlsError(f"Unknown concept {cid!r}")
        if active and c["status"] != "active":
            hint = f" (merged into {c['merged_into']})" if c.get("merged_into") else ""
            raise QlsError(f"Concept {cid} is {c['status']}{hint}")
        return c

    def theme(self, tid: str) -> dict:
        t = self.s["themes"].get(tid)
        if t is None or t["status"] != "active":
            raise QlsError(f"Unknown or dropped theme {tid!r}")
        return t

    def dimension(self, aid: str) -> dict:
        d = self.s["dimensions"].get(aid)
        if d is None or d["status"] != "active":
            raise QlsError(f"Unknown or dropped dimension {aid!r}")
        return d

    def theme_of(self, cid: str) -> str | None:
        for t in self.s["themes"].values():
            if t["status"] == "active" and cid in t["concepts"]:
                return t["id"]
        return None

    def dimension_of(self, tid: str) -> str | None:
        for d in self.s["dimensions"].values():
            if d["status"] == "active" and tid in d["themes"]:
                return d["id"]
        return None

    # -- concepts (stage 1-2) -------------------------------------------------

    def add_concept(self, label: str, description: str, quotes: list[dict], reason: str = "",
                    origin: dict | None = None, flags: list[str] | None = None, stage: int = 1,
                    memo: dict | None = None) -> str:
        if not label.strip():
            raise QlsError("Concept label is empty")
        if not quotes:
            raise QlsError("A concept needs at least one grounded quote")
        qids = []
        for q in quotes:
            qid = self._next("q")
            self.s["quotes"][qid] = {"id": qid, **{k: q[k] for k in ("doc", "segment", "start", "end", "text", "score", "method") if k in q}}
            qids.append(qid)
        cid = self._next("c")
        self.s["concepts"][cid] = {
            "id": cid, "label": label.strip(), "description": (description or "").strip(),
            "quotes": qids, "status": "active", "merged_into": None,
            "origin": origin or {}, "flags": flags or [],
        }
        memo = {k: v.strip() for k, v in (memo or {}).items() if isinstance(v, str) and v.strip()}
        if memo:
            self.s["concepts"][cid]["memo"] = memo
        self._log("create", "concept", [q["segment"] for q in quotes], [cid], reason or "coded from transcript",
                  stage=stage, label=label.strip())
        self._done()
        return cid

    def rename_concept(self, cid: str, label: str | None, description: str | None, reason: str) -> None:
        reason = self._need_reason(reason)
        c = self.concept(cid)
        old = c["label"]
        if label:
            c["label"] = label.strip()
        if description is not None:
            c["description"] = description.strip()
        self._log("rename", "concept", [cid], [cid], reason, old=old, new=c["label"])
        self._done()

    def merge_concepts(self, ids: list[str], label: str, description: str, reason: str) -> str:
        reason = self._need_reason(reason)
        ids = list(dict.fromkeys(ids))
        if len(ids) < 2:
            raise QlsError("Merge needs at least two concept IDs")
        cs = [self.concept(i) for i in ids]
        themes = {self.theme_of(i) for i in ids}
        quotes, seen = [], set()
        for c in cs:
            for qid in c["quotes"]:
                q = self.s["quotes"][qid]
                key = (q["segment"], q["start"], q["end"])
                if key in seen:
                    continue  # identical span quoted twice: keep one
                seen.add(key)
                quotes.append(qid)
        cid = self._next("c")
        self.s["concepts"][cid] = {
            "id": cid, "label": label.strip() or cs[0]["label"], "description": (description or "").strip(),
            "quotes": quotes, "status": "active", "merged_into": None,
            "origin": {"merged_from": ids}, "flags": sorted({f for c in cs for f in c.get("flags", [])}),
        }
        for c in cs:
            c["status"], c["merged_into"] = "merged", cid
            for t in self.s["themes"].values():
                if c["id"] in t["concepts"]:
                    t["concepts"].remove(c["id"])
        note = None
        if len(themes) == 1 and None not in themes:
            self.s["themes"][themes.pop()]["concepts"].append(cid)
        elif themes - {None}:
            note = "merged concepts came from different themes; result left unassigned"
        self._log("merge", "concept", ids, [cid], reason, label=self.s["concepts"][cid]["label"],
                  from_labels=[c["label"] for c in cs], note=note)
        self._done()
        return cid

    def split_concept(self, cid: str, parts: list[dict], reason: str) -> list[str]:
        """parts: [{"label", "description", "quotes": [qid, ...]}]; every quote must be placed."""
        reason = self._need_reason(reason)
        c = self.concept(cid)
        if len(parts) < 2:
            raise QlsError("Split needs at least two parts")
        placed = [q for p in parts for q in p.get("quotes", [])]
        unknown = set(placed) - set(c["quotes"])
        if unknown:
            raise QlsError(f"Quotes {sorted(unknown)} do not belong to {cid}")
        missing = set(c["quotes"]) - set(placed)
        if missing:
            raise QlsError(f"Split must place every quote of {cid}; unplaced: {sorted(missing)}")
        twice = sorted({q for q in placed if placed.count(q) > 1})
        if twice:
            raise QlsError(f"Each quote goes to exactly one part; placed more than once: {twice}")
        theme = self.theme_of(cid)
        new = []
        for p in parts:
            if not p.get("quotes"):
                raise QlsError("Every part of a split needs at least one quote")
            nid = self._next("c")
            self.s["concepts"][nid] = {
                "id": nid, "label": p["label"].strip(), "description": (p.get("description") or "").strip(),
                "quotes": list(dict.fromkeys(p["quotes"])), "status": "active", "merged_into": None,
                "origin": {"split_from": cid}, "flags": list(c.get("flags", [])),
            }
            new.append(nid)
            if theme:
                self.s["themes"][theme]["concepts"].append(nid)
        c["status"] = "split"
        if theme:
            self.s["themes"][theme]["concepts"].remove(cid)
        self._log("split", "concept", [cid], new, reason, from_label=c["label"],
                  labels=[self.s["concepts"][n]["label"] for n in new])
        self._done()
        return new

    def drop_concept(self, cid: str, reason: str) -> None:
        reason = self._need_reason(reason)
        c = self.concept(cid)
        c["status"] = "dropped"
        tid = self.theme_of(cid)
        if tid:
            self.s["themes"][tid]["concepts"].remove(cid)
        self._log("drop", "concept", [cid], [], reason, label=c["label"], theme=tid)
        self._done()

    def restore_concept(self, cid: str, reason: str) -> None:
        reason = self._need_reason(reason)
        c = self.concept(cid, active=False)
        if c["status"] != "dropped":
            raise QlsError(f"Only dropped concepts can be restored ({cid} is {c['status']})")
        c["status"] = "active"
        self._log("restore", "concept", [cid], [cid], reason, label=c["label"])
        self._done()

    # -- themes (stage 3) -----------------------------------------------------

    def _place_concepts(self, tid: str, ids: Iterable[str]) -> list[dict]:
        moved = []
        for cid in ids:
            self.concept(cid)
            old = self.theme_of(cid)
            if old == tid:
                continue
            if old:
                self.s["themes"][old]["concepts"].remove(cid)
                moved.append({"concept": cid, "from": old})
            self.s["themes"][tid]["concepts"].append(cid)
        return moved

    def create_theme(self, label: str, definition: str, concept_ids: list[str], reason: str) -> str:
        reason = self._need_reason(reason)
        if not label.strip():
            raise QlsError("Theme label is empty")
        for cid in concept_ids:
            self.concept(cid)
        tid = self._next("t")
        self.s["themes"][tid] = {"id": tid, "label": label.strip(), "definition": (definition or "").strip(),
                                 "concepts": [], "status": "active"}
        moved = self._place_concepts(tid, concept_ids)
        self._log("create", "theme", concept_ids, [tid], reason, label=label.strip(), moved=moved or None)
        self._done()
        return tid

    def assign_concepts(self, tid: str, ids: list[str], reason: str) -> None:
        reason = self._need_reason(reason)
        self.theme(tid)
        moved = self._place_concepts(tid, ids)
        self._log("assign", "theme", ids, [tid], reason, label=self.s["themes"][tid]["label"], moved=moved or None)
        self._done()

    def unassign_concepts(self, ids: list[str], reason: str) -> None:
        reason = self._need_reason(reason)
        froms = []
        for cid in ids:
            tid = self.theme_of(cid)
            if tid:
                self.s["themes"][tid]["concepts"].remove(cid)
                froms.append(tid)
        self._log("unassign", "theme", ids, froms, reason)
        self._done()

    def rename_theme(self, tid: str, label: str | None, definition: str | None, reason: str) -> None:
        reason = self._need_reason(reason)
        t = self.theme(tid)
        old = t["label"]
        if label:
            t["label"] = label.strip()
        if definition is not None:
            t["definition"] = definition.strip()
        self._log("rename", "theme", [tid], [tid], reason, old=old, new=t["label"])
        self._done()

    def drop_theme(self, tid: str, reason: str) -> None:
        reason = self._need_reason(reason)
        t = self.theme(tid)
        freed = list(t["concepts"])
        t["concepts"], t["status"] = [], "dropped"
        aid = self.dimension_of(tid)
        if aid:
            self.s["dimensions"][aid]["themes"].remove(tid)
        self._log("drop", "theme", [tid], freed, reason, label=t["label"])
        self._done()

    # -- aggregate dimensions (stage 4) ---------------------------------------

    def _place_themes(self, aid: str, tids: Iterable[str]) -> list[dict]:
        moved = []
        for tid in tids:
            self.theme(tid)
            old = self.dimension_of(tid)
            if old == aid:
                continue
            if old:
                self.s["dimensions"][old]["themes"].remove(tid)
                moved.append({"theme": tid, "from": old})
            self.s["dimensions"][aid]["themes"].append(tid)
        return moved

    def create_dimension(self, label: str, definition: str, theme_ids: list[str], reason: str) -> str:
        reason = self._need_reason(reason)
        if not label.strip():
            raise QlsError("Dimension label is empty")
        for tid in theme_ids:
            self.theme(tid)
        aid = self._next("a")
        self.s["dimensions"][aid] = {"id": aid, "label": label.strip(), "definition": (definition or "").strip(),
                                     "themes": [], "status": "active"}
        moved = self._place_themes(aid, theme_ids)
        self._log("create", "dimension", theme_ids, [aid], reason, label=label.strip(), moved=moved or None)
        self._done()
        return aid

    def assign_themes(self, aid: str, tids: list[str], reason: str) -> None:
        reason = self._need_reason(reason)
        self.dimension(aid)
        moved = self._place_themes(aid, tids)
        self._log("assign", "dimension", tids, [aid], reason, label=self.s["dimensions"][aid]["label"], moved=moved or None)
        self._done()

    def rename_dimension(self, aid: str, label: str | None, definition: str | None, reason: str) -> None:
        reason = self._need_reason(reason)
        d = self.dimension(aid)
        old = d["label"]
        if label:
            d["label"] = label.strip()
        if definition is not None:
            d["definition"] = definition.strip()
        self._log("rename", "dimension", [aid], [aid], reason, old=old, new=d["label"])
        self._done()

    def drop_dimension(self, aid: str, reason: str) -> None:
        reason = self._need_reason(reason)
        d = self.dimension(aid)
        freed = list(d["themes"])
        d["themes"], d["status"] = [], "dropped"
        self._log("drop", "dimension", [aid], freed, reason, label=d["label"])
        self._done()

    # -- memos ----------------------------------------------------------------

    def add_memo(self, text: str, links: list[str] | None = None, kind: str = "analytic",
                 evidence: list[str] | None = None) -> str:
        """A memo is analytic context written down: what something means here, what it is
        not, what was surprising, a counter-argument. Links say what it is about; evidence
        says which data supports it."""
        if not text.strip():
            raise QlsError("Memo text is empty")
        if kind not in MEMO_KINDS:
            raise QlsError(f"Memo kind must be one of {', '.join(MEMO_KINDS)}")
        links = self._check_refs(links, "link")
        evidence = self._check_refs(evidence, "evidence")
        mid = self._next("m")
        self.s["memos"][mid] = {"id": mid, "author": self.actor, "kind": kind, "text": text.strip(),
                                "links": links, "evidence": evidence, "ts": now()}
        self._evidence = evidence
        self._log("memo", "memo", links, [mid], text.strip().splitlines()[0][:160], kind=kind)
        self._done()
        return mid

    # -- corpus subsets (robustness to data) ------------------------------------

    def exclude_documents(self, docs: list[str], reason: str) -> dict:
        """Remove every quote from these documents. Concepts left without quotes become
        'excluded'. Used for leave-one-informant-out and bootstrap analyses."""
        reason = self._need_reason(reason)
        known = set(self.run.project.doc_ids())
        bad = [d for d in docs if d not in known]
        if bad:
            raise QlsError(f"Unknown documents: {', '.join(bad)}")
        gone = {qid for qid, q in self.s["quotes"].items() if q["doc"] in docs}
        emptied = []
        for c in self.s["concepts"].values():
            if not set(c["quotes"]) & gone:
                continue
            c["quotes"] = [q for q in c["quotes"] if q not in gone]
            if not c["quotes"] and c["status"] in ("active", "dropped"):
                c["status"] = "excluded"
                emptied.append(c["id"])
                for t in self.s["themes"].values():
                    if c["id"] in t["concepts"]:
                        t["concepts"].remove(c["id"])
        for qid in gone:
            del self.s["quotes"][qid]
        self._log("exclude", "corpus", docs, emptied, reason, quotes_removed=len(gone))
        self._done()
        return {"quotes_removed": len(gone), "concepts_excluded": emptied}
