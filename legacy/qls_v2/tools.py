"""Tools: the only way anything is read from or written to a run.

The model is free; the data store is strict. Every write is validated against the
core ontology and the run's recipe *before* an event is written. Refusals are
returned (not raised) as {"ok": false, "error": ..., "hint": ...} so the model can
fix and retry. Successful writes return {"ok": true, "id": ..., "event": seq}.

One implementation, three transports: MCP server (server.py), CLI (`qls tool`),
and agents in a shell calling the CLI.

Human-only actions (approve, reject, edit, answer, resume) are methods here too but
are never exposed to agents: the MCP server has no tool for them, and an agent's
actor is fixed when its server starts, so it cannot write as a human.
"""

from __future__ import annotations

import re
from typing import Any

from .grounding import locate
from .recipe import ANY, MEMO_LEVELS, Recipe, load_recipe
from .store import Store
from .util import QlsError


def ok(**kw) -> dict:
    return {"ok": True, **kw}


def refuse(error: str, hint: str = "", **kw) -> dict:
    return {"ok": False, "error": error, **({"hint": hint} if hint else {}), **kw}


def is_human(actor: str) -> bool:
    return actor == "human" or actor.startswith("human:")


class Session:
    """A run, an actor, and the recipe that governs what they may write."""

    def __init__(self, store: Store, run: str, actor: str, model: str | None = None):
        if not actor:
            raise QlsError("actor is required")
        self.store, self.run, self.actor, self.model = store, run, actor, model
        r = store.run(run)
        self.recipe: Recipe = load_recipe(r["config"].get("recipe_path") or r["recipe"])
        if self.recipe.hash != r["recipe_hash"]:
            raise QlsError(f"Recipe {r['recipe']!r} changed since run {run!r} started "
                           f"({r['recipe_hash'][:19]} -> {self.recipe.hash[:19]}). Fork a new run to use the new recipe.")
        self.config = r["config"]

    # ------------------------------------------------------------------ helpers

    def _status(self) -> str:
        return self.store.run(self.run)["status"]

    def _gate(self) -> dict | None:
        if self._status() == "waiting" and not is_human(self.actor):
            cp = self.store.run(self.run)["checkpoint_seq"]
            return refuse(f"Run {self.run} is waiting for the researcher to review checkpoint #{cp}.",
                          "Stop here. The researcher will resume the run; you will then see their feedback with get_feedback().")
        return None

    def _write(self, kind: str, payload: dict, reason: str = "") -> int:
        return self.store.append(self.run, kind, self.actor, payload, reason, self.model)

    def _type_of(self, ref: str) -> str | None:
        """Type name of a reference: an object type, 'Source' (source or one of its units), or None."""
        o = self.store.obj(self.run, ref)
        if o:
            return o["type"] if o["status"] == "active" else None
        sid = ref.split(":", 1)[0]
        if self.store.has_source(sid):
            if ":" not in ref:
                return "Source"
            units = self.store.source(sid)["units"]
            return "Source" if any(u["id"] == ref for u in units) else None
        return None

    def _describe(self, ref: str) -> str:
        o = self.store.obj(self.run, ref)
        if not o:
            return ref
        f = o["fields"]
        return f"{ref} ({o['type']}: {f.get('in_vivo') or f.get('label') or f.get('code') or f.get('term') or (f.get('text') or '')[:60]})"

    # ------------------------------------------------------------------ read

    def recipe_info(self) -> dict:
        """The method: types, link rules, memo kinds, steps. Read this first."""
        return self.recipe.summary()

    def list_sources(self) -> list[dict]:
        out = []
        for sid in self.store.source_ids():
            s = self.store.source(sid)
            segs = [u for u in s["units"] if u["kind"] == "segment"]
            out.append({"id": sid, "version": s["version"], "title": s["title"], "participant": s["meta"].get("participant"),
                        "segments": len(segs), "chars": len(s["text"])})
        return out

    def read_source(self, source: str, start: int = 1, limit: int = 40) -> dict:
        """Turns of a source (1-based, `limit` turns) with segment IDs and character offsets for quoting."""
        s = self.store.source(source)
        turns = [u for u in s["units"] if u["kind"] == "turn"]
        segs = [u for u in s["units"] if u["kind"] == "segment"]
        out = []
        for t in turns[start - 1:start - 1 + limit]:
            out.append({"turn": t["id"], "speaker": t["speaker"], "role": t["role"],
                        "segments": [{"id": g["id"], "start": g["start"], "end": g["end"]} for g in segs if g["turn"] == t["id"]],
                        "text": s["text"][t["start"]:t["end"]]})
        return {"source": source, "version": s["version"], "participant": s["meta"].get("participant"),
                "turns_total": len(turns), "from": start, "turns": out}

    def search(self, query: str, sources: list[str] | None = None, objects: bool = True, regex: bool = False,
               limit: int = 30) -> list[dict]:
        """Lexical search in informant segments and in this run's objects (labels, definitions, memos)."""
        try:
            rx = re.compile(query if regex else re.escape(query), re.I)
        except re.error as exc:
            raise QlsError(f"bad regex: {exc}") from exc
        hits = []
        for sid in sources or self.store.source_ids():
            s = self.store.source(sid)
            for u in s["units"]:
                if u["kind"] != "segment":
                    continue
                text = s["text"][u["start"]:u["end"]]
                for m in rx.finditer(text):
                    a, b = max(0, m.start() - 150), min(len(text), m.end() + 150)
                    hits.append({"where": u["id"], "start": u["start"] + m.start(), "end": u["start"] + m.end(),
                                 "snippet": ("…" if a else "") + text[a:b] + ("…" if b < len(text) else "")})
                    if len(hits) >= limit:
                        return hits
        if objects:
            for o in self.store.objects(self.run):
                blob = " | ".join(str(v) for v in o["fields"].values())
                if rx.search(blob):
                    hits.append({"where": o["id"], "type": o["type"], "snippet": blob[:300]})
                    if len(hits) >= limit:
                        break
        return hits

    def quote_text(self, q: dict) -> str:
        f = q["fields"]
        return self.store.source(f["source"], f["version"])["text"][f["start"]:f["end"]]

    def get(self, ref: str) -> dict:
        """Any object (with links in and out and memos about it), quote (with its text and question), or source unit."""
        o = self.store.obj(self.run, ref)
        if o:
            out = {"id": ref, "type": o["type"], "status": o["status"], "fields": o["fields"],
                   "superseded_by": o["superseded_by"],
                   "links_out": [{"rel": l["rel"], "to": l["dst"], "reason": l["reason"]} for l in self.store.links(self.run, src=ref)],
                   "links_in": [{"rel": l["rel"], "from": l["src"], "reason": l["reason"]} for l in self.store.links(self.run, dst=ref)]}
            if o["type"] == "Quote":
                out["text"] = self.quote_text(o)
                s = self.store.source(o["fields"]["source"], o["fields"]["version"])
                unit = next((u for u in s["units"] if u["id"] == o["fields"].get("unit")), {})
                if unit.get("question"):
                    out["question"] = unit["question"]
                    out["question_gap"] = unit.get("question_gap", 0)
            return out
        sid = ref.split(":", 1)[0]
        if self.store.has_source(sid):
            s = self.store.source(sid)
            if ":" not in ref:
                return {"id": sid, "type": "Source", "title": s["title"], "meta": s["meta"], "version": s["version"],
                        "memos": [self._describe(l["src"]) for l in self.store.links(self.run, dst=sid, rel="about")]}
            u = next((u for u in s["units"] if u["id"] == ref), None)
            if u:
                return {"id": ref, "type": "SourceUnit", **{k: v for k, v in u.items() if k != "id"},
                        "text": s["text"][u["start"]:u["end"]]}
        raise QlsError(f"Nothing called {ref!r} in run {self.run}")

    def neighbors(self, ref: str) -> dict:
        """Everything linked to ref, one step out and in, with link reasons."""
        out = [{"rel": l["rel"], "to": self._describe(l["dst"]), "reason": l["reason"]} for l in self.store.links(self.run, src=ref)]
        inn = [{"rel": l["rel"], "from": self._describe(l["src"]), "reason": l["reason"]} for l in self.store.links(self.run, dst=ref)]
        return {"id": ref, "out": out, "in": inn}

    def get_codebook(self) -> list[dict]:
        return [{"id": o["id"], **o["fields"], "examples": [l["dst"] for l in self.store.links(self.run, src=o["id"], rel="example")]}
                for o in self.store.objects(self.run, "CodebookEntry")]

    def get_terms(self) -> dict:
        terms = [{"id": o["id"], **o["fields"]} for o in self.store.objects(self.run, "Term")]
        props = [{"id": o["id"], "by": self._creator(o), **o["fields"]} for o in self.store.objects(self.run, "Proposal")
                 if o["fields"].get("kind") == "term"]
        return {"accepted": terms, "proposed": props}

    def _creator(self, o: dict) -> str:
        ev = self.store.db.execute("SELECT actor FROM events WHERE seq=?", (o["created_seq"],)).fetchone()
        return ev["actor"] if ev else ""

    def status(self) -> dict:
        counts: dict[str, int] = {}
        for o in self.store.objects(self.run):
            counts[o["type"]] = counts.get(o["type"], 0) + 1
        r = self.store.run(self.run)
        return {"run": self.run, "status": r["status"], "checkpoint": r["checkpoint_seq"], "recipe": r["recipe"],
                "parent": r["parent"], "fork_at": r["fork_at"], "objects": counts,
                "events": len(self.store.effective_events(self.run)), "open_expectations": len(self.check())}

    def check(self) -> list[dict]:
        """Recipe expectations that are not met yet (soft rules)."""
        problems = []
        for exp in self.recipe.raw.get("expectations") or []:
            each = exp["each"]
            items = ([{"id": sid} for sid in self.store.source_ids()] if each == "Source"
                     else self.store.objects(self.run, each))
            for it in items:
                if not self._meets(it["id"], exp):
                    problems.append({"expectation": exp["id"], "item": it["id"], "text": exp["text"]})
        return problems

    def _meets(self, ref: str, exp: dict) -> bool:
        if "needs_memo_level" in exp:
            return any(self._memo(l["src"], level=exp["needs_memo_level"]) for l in self.store.links(self.run, dst=ref, rel="about"))
        if "needs_incoming" in exp:
            return self._has_incoming(ref, exp["needs_incoming"])
        for alt in exp.get("needs_any", []):
            if "link" in alt and self.store.links(self.run, src=ref, rel=alt["link"]):
                return True
            if "memo_kind" in alt and any(self._memo(l["src"], kind=alt["memo_kind"]) for l in self.store.links(self.run, dst=ref, rel="about")):
                return True
            if "incoming" in alt and self._has_incoming(ref, alt["incoming"]):
                return True
        return not exp.get("needs_any")

    def _has_incoming(self, ref: str, spec: dict) -> bool:
        return any((self.store.obj(self.run, l["src"]) or {}).get("type") == spec["from"]
                   for l in self.store.links(self.run, dst=ref, rel=spec["rel"]))

    def _memo(self, mid: str, kind: str | None = None, level: str | None = None) -> bool:
        o = self.store.obj(self.run, mid)
        return bool(o and o["type"] == "Memo" and o["status"] == "active"
                    and (kind is None or o["fields"].get("kind") == kind)
                    and (level is None or o["fields"].get("level") == level))

    # ------------------------------------------------------------------ write: quotes

    def add_quote(self, source: str, text: str | None = None, start: int | None = None, end: int | None = None) -> dict:
        """Verify a quote against the source and store it as a pointer (source, version, offsets).
        Give either the quoted text or character offsets. Unverifiable quotes are refused with the closest match."""
        if (g := self._gate()):
            return g
        try:
            s = self.store.source(source.split(":", 1)[0])
        except QlsError as exc:
            return refuse(str(exc))
        roles = set(self.recipe.quote.get("roles") or ["informant"])
        units = [u for u in s["units"] if (u["kind"] == "segment" and "informant" in roles)
                 or (u["kind"] == "turn" and u["role"] in roles and u["role"] != "informant")]
        if not units:
            return refuse(f"{s['id']} has no quotable units for roles {sorted(roles)}")
        threshold = float(self.recipe.quote.get("threshold", 90))
        if start is not None and end is not None:
            unit = next((u for u in units if u["start"] <= start < end <= u["end"]), None)
            if unit is None:
                return refuse(f"Offsets {start}-{end} are not inside one {'/'.join(sorted(roles))} segment of {s['id']}",
                              "Use read_source() to see segment offsets; quotes may not cross segments or come from the interviewer.")
            span, score, method = (start, end), 100.0, "offsets"
        elif text:
            best = None
            for u in units:
                sp = locate(text, s["text"][u["start"]:u["end"]], threshold)
                if sp and (best is None or sp.score > best[1].score):
                    best = (u, sp)
                    if sp.score >= 100:
                        break
            if best is None:
                return refuse(f"Quote not found in {s['id']} (threshold {threshold:g}).",
                              "Copy the informant's words exactly, or pass start/end offsets of the closest match.",
                              closest=self._closest(s, units, text))
            unit, sp = best
            span, score, method = (unit["start"] + sp.start, unit["start"] + sp.end), sp.score, sp.method
        else:
            return refuse("Give the quote text, or start and end offsets.")
        for q in self.store.objects(self.run, "Quote"):
            f = q["fields"]
            if f["source"] == s["id"] and f["version"] == s["version"] and (f["start"], f["end"]) == span:
                return ok(id=q["id"], text=s["text"][span[0]:span[1]], existing=True)
        qid = self.store.next_id(self.run, "Q")
        fields = {"source": s["id"], "version": s["version"], "start": span[0], "end": span[1], "unit": unit["id"],
                  "score": round(score, 1), "method": method}
        seq = self._write("create", {"id": qid, "type": "Quote", "fields": fields, "links": []})
        return ok(id=qid, event=seq, text=s["text"][span[0]:span[1]], unit=unit["id"], method=method, score=round(score, 1))

    @staticmethod
    def _closest(s: dict, units: list[dict], text: str) -> dict | None:
        from rapidfuzz import fuzz

        best = None
        for u in units:
            body = s["text"][u["start"]:u["end"]]
            al = fuzz.partial_ratio_alignment(text.lower(), body.lower())
            if al and (best is None or al.score > best[0]):
                # widen the raw alignment to whole sentences, so the suggestion is quotable as is
                a = max((m.end() for m in re.finditer(r"[.!?]\s+", body[:al.dest_start])), default=0)
                m = re.search(r"[.!?](?=\s|$)", body[al.dest_end:])
                b = al.dest_end + m.end() if m else len(body)
                a, b = u["start"] + a, u["start"] + b
                best = (al.score, {"unit": u["id"], "start": a, "end": b, "score": round(al.score, 1), "text": s["text"][a:b]})
        return best[1] if best else None

    # ------------------------------------------------------------------ write: objects and links

    def _check_link(self, src_type: str, rel: str, dst: str, reason: str, src_id: str | None = None) -> str | None:
        spec = self.recipe.type(src_type)
        if rel not in spec.links:
            return f"{src_type} has no link {rel!r}; allowed: {', '.join(spec.links) or 'none'}"
        ls = spec.links[rel]
        dst_type = self._type_of(dst)
        if dst_type is None:
            return f"link target {dst!r} does not exist (or is superseded) in run {self.run}"
        if ANY not in ls.to and dst_type not in ls.to:
            return f"{src_type}.{rel} must point to {'/'.join(ls.to)}, not {dst_type} ({dst})"
        if ls.reason == "required" and not (reason or "").strip():
            return f"{src_type}.{rel} -> {dst} needs a one-line reason (why this link holds)"
        if ls.exclusive:
            for l in self.store.links(self.run, dst=dst, rel=rel):
                other = self.store.obj(self.run, l["src"])
                if other and other["type"] == src_type and other["id"] != src_id:
                    return (f"{dst} is already in {self._describe(other['id'])} via {rel!r}; a {dst_type} belongs to "
                            f"one {src_type}. Unlink it there first if you mean to move it.")
        return None

    def add_object(self, type: str, fields: dict, links: list[dict] | None = None, reason: str = "") -> dict:
        """Create an object of a recipe or core type, with its links ({rel, to, reason}) in one validated write."""
        if (g := self._gate()):
            return g
        try:
            spec = self.recipe.type(type)
        except QlsError as exc:
            return refuse(str(exc))
        if spec.writable_by == "server":
            return refuse(f"{type} objects are created by the server.", "Use add_quote() for quotes.")
        if spec.writable_by == "human" and not is_human(self.actor):
            return refuse(f"Only researchers create {type} objects.", "Use propose_term() or propose_code_change().")
        if type == "Memo":
            return refuse("Use add_memo() (or update_memo() for interview/batch/corpus memos).")
        fields = dict(fields or {})
        unknown = [f for f in fields if f not in spec.fields]
        if unknown:
            return refuse(f"Unknown field(s) for {type}: {', '.join(unknown)}", f"Fields: {', '.join(spec.fields)}")
        missing = [f for f, fs in spec.fields.items() if fs.get("required") and not str(fields.get(f, "")).strip()]
        if missing:
            return refuse(f"{type} needs {', '.join(missing)}",
                          "; ".join(f"{f}: {spec.fields[f].get('description', '')}" for f in missing))
        links = [dict(l) for l in (links or [])]
        seen, counts = set(), {}
        for l in links:
            if "rel" not in l or "to" not in l:
                return refuse("Each link needs rel and to (and reason where required).")
            key = (l["rel"], l["to"])
            if key in seen:
                return refuse(f"Duplicate link {l['rel']} -> {l['to']}")
            seen.add(key)
            err = self._check_link(type, l["rel"], l["to"], l.get("reason", ""))
            if err:
                return refuse(err)
            counts[l["rel"]] = counts.get(l["rel"], 0) + 1
        for rel, ls in spec.links.items():
            n = counts.get(rel, 0)
            if n < ls.min:
                return refuse(f"{type} needs at least {ls.min} {rel!r} link(s) to {'/'.join(ls.to)}; got {n}",
                              ls.description)
            if ls.max is not None and n > ls.max:
                return refuse(f"{type} allows at most {ls.max} {rel!r} link(s); got {n}")
        oid = self.store.next_id(self.run, spec.prefix)
        payload = {"id": oid, "type": type, "fields": fields,
                   "links": [{"rel": l["rel"], "to": l["to"], "reason": (l.get("reason") or "").strip()} for l in links]}
        seq = self._write("create", payload, reason)
        return ok(id=oid, event=seq)

    def link(self, src: str, rel: str, dst: str, reason: str = "") -> dict:
        """Add one link from an existing object (e.g. a concept to a theme's `groups`)."""
        if (g := self._gate()):
            return g
        o = self.store.obj(self.run, src)
        if not o or o["status"] != "active":
            return refuse(f"{src} does not exist or is superseded")
        if o["type"] == "Quote":
            return refuse("Quotes have no outgoing links; link to them from concepts or memos.")
        spec = self.recipe.type(o["type"])
        if spec.writable_by == "human" and not is_human(self.actor):
            return refuse(f"Only researchers change {o['type']} objects.")
        if any(l["dst"] == dst for l in self.store.links(self.run, src=src, rel=rel)):
            return refuse(f"{src} already links {rel} -> {dst}")
        err = self._check_link(o["type"], rel, dst, reason, src_id=src)
        if err:
            return refuse(err)
        ls = spec.links[rel]
        if ls.max is not None and len(self.store.links(self.run, src=src, rel=rel)) >= ls.max:
            return refuse(f"{o['type']} allows at most {ls.max} {rel!r} link(s)")
        seq = self._write("link", {"src": src, "rel": rel, "dst": dst}, reason)
        return ok(event=seq)

    def unlink(self, src: str, rel: str, dst: str, reason: str) -> dict:
        """Remove one link (recorded, never deleted). Refused if it would break the type's minimum."""
        if (g := self._gate()):
            return g
        if not (reason or "").strip():
            return refuse("unlink needs a reason")
        o = self.store.obj(self.run, src)
        if not o or not any(l["dst"] == dst for l in self.store.links(self.run, src=src, rel=rel)):
            return refuse(f"No active link {src} {rel} -> {dst}")
        ls = self.recipe.type(o["type"]).links.get(rel)
        if ls and len(self.store.links(self.run, src=src, rel=rel)) - 1 < ls.min:
            return refuse(f"{src} needs at least {ls.min} {rel!r} link(s)",
                          "Add a replacement first, or supersede the object.")
        seq = self._write("unlink", {"src": src, "rel": rel, "dst": dst}, reason)
        return ok(event=seq)

    def update(self, id: str, fields: dict, reason: str) -> dict:
        """Change fields of an object (e.g. relabel a theme). The old values stay in the log."""
        if (g := self._gate()):
            return g
        if not (reason or "").strip():
            return refuse("update needs a reason")
        o = self.store.obj(self.run, id)
        if not o or o["status"] != "active":
            return refuse(f"{id} does not exist or is superseded")
        spec = self.recipe.type(o["type"])
        if spec.writable_by != "agent" and not is_human(self.actor):
            return refuse(f"Only researchers change {o['type']} objects.")
        unknown = [f for f in fields if f not in spec.fields or f in ("kind", "level")]
        if unknown:
            return refuse(f"Cannot update field(s) {', '.join(unknown)} of {o['type']}")
        empty = [f for f, v in fields.items() if spec.fields[f].get("required") and not str(v).strip()]
        if empty:
            return refuse(f"Required field(s) cannot be empty: {', '.join(empty)}")
        seq = self._write("update", {"id": id, "fields": fields, "old": {f: o["fields"].get(f) for f in fields}}, reason)
        return ok(event=seq)

    def supersede(self, old: list[str], new: str | None, reason: str) -> dict:
        """Replace objects by another (merge: create the new one first, then supersede the old ones into it),
        or withdraw them (new=None). Nothing is deleted; links pointing at the old objects move to the new one."""
        if (g := self._gate()):
            return g
        if not (reason or "").strip():
            return refuse("supersede needs a reason")
        objs = [self.store.obj(self.run, o) for o in old]
        if not old or any(o is None or o["status"] != "active" for o in objs):
            return refuse("All objects to supersede must exist and be active")
        types = {o["type"] for o in objs}
        if len(types) != 1 or "Quote" in types:
            return refuse("Supersede objects of one type (not quotes)")
        if self.recipe.type(objs[0]["type"]).writable_by != "agent" and not is_human(self.actor):
            return refuse(f"Only researchers supersede {objs[0]['type']} objects.")
        if new is not None:
            n = self.store.obj(self.run, new)
            if not n or n["status"] != "active" or n["type"] not in types or new in old:
                return refuse(f"Replacement {new} must be an active {objs[0]['type']} not among the old ones")
            # exclusive incoming links must not end up pointing one object into two places
            for rel_src_type, ls_rel in self._exclusive_incoming(objs[0]["type"]):
                holders = {l["src"] for x in [*old, new] for l in self.store.links(self.run, dst=x, rel=ls_rel)
                           if (self.store.obj(self.run, l["src"]) or {}).get("type") == rel_src_type}
                if len(holders) > 1:
                    return refuse(f"{', '.join(old)} and {new} sit in different {rel_src_type} objects via {ls_rel!r}: "
                                  f"{', '.join(sorted(holders))}", "Move them into one place first (unlink/link), then supersede.")
        seq = self._write("supersede", {"old": old, "new": [new] if new else [], "status": "superseded" if new else "withdrawn"}, reason)
        return ok(event=seq)

    def _exclusive_incoming(self, type_: str) -> list[tuple[str, str]]:
        return [(t.name, rel) for t in self.recipe.types.values() for rel, ls in t.links.items()
                if ls.exclusive and type_ in ls.to]

    # ------------------------------------------------------------------ write: memos

    def add_memo(self, about: list[str], kind: str, text: str, cites: list[str] | None = None) -> dict:
        """Write down analytic content, about at least one object or source.
        kinds: see recipe_info().memo_kinds (meaning, informant, surprise, uncertainty, method, ...)."""
        if (g := self._gate()):
            return g
        if kind not in self.recipe.memo_kinds:
            return refuse(f"Memo kind {kind!r} unknown", f"Kinds: {', '.join(self.recipe.memo_kinds)}")
        return self._memo_write(about, {"kind": kind, "text": text}, cites, "")

    def _memo_write(self, about: list[str], fields: dict, cites: list[str] | None, reason: str,
                    supersedes: str | None = None) -> dict:
        if not (fields.get("text") or "").strip():
            return refuse("Memo text is empty")
        about = list(dict.fromkeys(about or []))
        if not about:
            return refuse("A memo must be about at least one object or source")
        links = [{"rel": "about", "to": a, "reason": ""} for a in about] + \
                [{"rel": "cites", "to": c, "reason": ""} for c in dict.fromkeys(cites or [])]
        for l in links:
            if self._type_of(l["to"]) is None:
                return refuse(f"{l['rel']} target {l['to']!r} does not exist")
        mid = self.store.next_id(self.run, "M")
        seq = self._write("create", {"id": mid, "type": "Memo", "fields": fields, "links": links}, reason)
        if supersedes:
            self._write("supersede", {"old": [supersedes], "new": [mid], "status": "superseded", "move_incoming": False},
                        "new version of the memo")
        return ok(id=mid, event=seq)

    def get_memo(self, level: str, about: str) -> dict:
        """The current interview / batch / corpus memo about a source, batch or corpus object."""
        m = self._level_memo(level, about)
        return {"level": level, "about": about, "memo": m and {"id": m["id"], "text": m["fields"]["text"],
                                                              "cites": [l["dst"] for l in self.store.links(self.run, src=m["id"], rel="cites")]},
                "budget_chars": self.recipe.memo_budget.get(level)}

    def _level_memo(self, level: str, about: str) -> dict | None:
        for l in reversed(self.store.links(self.run, dst=about, rel="about")):
            o = self.store.obj(self.run, l["src"])
            if o and o["type"] == "Memo" and o["status"] == "active" and o["fields"].get("level") == level:
                return o
        return None

    def update_memo(self, level: str, about: str, text: str, cites: list[str] | None = None) -> dict:
        """Write a new version of the interview / batch / corpus memo (old versions are kept).
        Batch memos cite interview memos; the corpus memo cites batch (or interview) memos; all within a budget."""
        if (g := self._gate()):
            return g
        if level not in MEMO_LEVELS:
            return refuse(f"level must be one of {', '.join(MEMO_LEVELS)}")
        budget = self.recipe.memo_budget.get(level)
        if budget and len(text) > budget:
            return refuse(f"{level} memo is {len(text)} characters; budget is {budget}", "Compress: keep what later steps need, cite the rest.")
        need = MEMO_LEVELS[level]["cites"]
        if need:
            lower = [c for c in (cites or []) if self._memo(c, level=need) or (need == "batch" and self._memo(c, level="interview"))]
            if not lower:
                return refuse(f"A {level} memo must cite at least one {need} memo",
                              "Each sentence of a higher-level memo should trace down to lower memos and from them to quotes.")
        prev = self._level_memo(level, about)
        return self._memo_write([about], {"kind": "summary", "level": level, "text": text}, cites,
                                f"{level} memo update", supersedes=prev["id"] if prev else None)

    # ------------------------------------------------------------------ team

    def propose_term(self, term: str, definition: str = "", about: list[str] | None = None) -> dict:
        """Propose a canonical term for the term bank (accepted only in reconciliation)."""
        if (g := self._gate()):
            return g
        return self.add_object("Proposal", {"kind": "term", "term": term, "text": definition or term},
                               [{"rel": "about", "to": a} for a in (about or [])])

    def propose_code_change(self, text: str, about: list[str] | None = None) -> dict:
        """Propose a codebook change (new code, split, merge, a 'when not to use' rule)."""
        if (g := self._gate()):
            return g
        return self.add_object("Proposal", {"kind": "code_change", "text": text}, [{"rel": "about", "to": a} for a in (about or [])])

    def report_residual(self, source: str, text: str, quote: str | None = None) -> dict:
        """Report something in a source that does not fit the current codebook."""
        if (g := self._gate()):
            return g
        links = [{"rel": "from", "to": source}] + ([{"rel": "evidenced_by", "to": quote}] if quote else [])
        return self.add_object("Residual", {"text": text}, links)

    def post_board(self, text: str, about: list[str] | None = None) -> dict:
        if (g := self._gate()):
            return g
        if self.config.get("board") is False:
            return refuse("The shared board is off in this run (independent coding).")
        return self.add_object("BoardPost", {"text": text}, [{"rel": "about", "to": a} for a in (about or [])])

    def read_board(self, since: int = 0) -> list[dict]:
        if self.config.get("board") is False:
            return []
        return [{"id": o["id"], "by": self._creator(o), "text": o["fields"]["text"]}
                for o in self.store.objects(self.run, "BoardPost") if o["created_seq"] > since]

    # ------------------------------------------------------------------ control

    def checkpoint(self, summary: str, questions: list[str] | None = None) -> dict:
        """Stop and hand over to the researcher. Writes are refused until they resume the run."""
        if (g := self._gate()):
            return g
        seq = self._write("checkpoint", {"summary": summary, "questions": questions or [], "open": self.check()})
        self.store.set_status(self.run, "waiting", seq)
        return ok(event=seq, message="Checkpoint recorded. Stop now; the researcher will review and resume the run.")

    def get_feedback(self) -> list[dict]:
        """Researcher events since the last checkpoint (approvals, rejections, edits, answers)."""
        cp = self.store.run(self.run)["checkpoint_seq"] or 0
        return [{"event": e["seq"], "by": e["actor"], **e["payload"], "reason": e["reason"]}
                for e in self.store.events(self.run, since=cp) if e["kind"] == "human"]

    # ------------------------------------------------------------------ human only (CLI)

    def _human(self) -> None:
        if not is_human(self.actor):
            raise QlsError("Only a researcher can do this")

    def human_note(self, action: str, target: str | None, text: str) -> int:
        self._human()
        return self._write("human", {"action": action, "target": target, "text": text}, text)

    def approve(self, text: str = "") -> int:
        return self.human_note("approve", None, text or "approved")

    def reject(self, ref: str, reason: str) -> dict:
        self._human()
        o = self.store.obj(self.run, ref)
        if not o or o["status"] != "active":
            return refuse(f"{ref} is not an active object")
        self.human_note("reject", ref, reason)
        seq = self._write("supersede", {"old": [ref], "new": [], "status": "rejected"}, reason)
        return ok(event=seq)

    def edit(self, ref: str, fields: dict, reason: str) -> dict:
        self._human()
        self.human_note("edit", ref, reason)
        return self.update(ref, fields, reason)

    def answer(self, text: str) -> int:
        return self.human_note("answer", None, text)

    def resume(self) -> int:
        self._human()
        seq = self._write("resume", {})
        self.store.set_status(self.run, "open")
        return seq


AGENT_TOOLS = [
    # read
    "recipe_info", "list_sources", "read_source", "search", "get", "neighbors", "get_codebook", "get_terms", "status", "check",
    # write
    "add_quote", "add_object", "link", "unlink", "update", "supersede", "add_memo",
    # team
    "propose_term", "propose_code_change", "report_residual", "post_board", "read_board",
    # context
    "get_memo", "update_memo",
    # control
    "checkpoint", "get_feedback",
]


def call(session: Session, name: str, args: dict[str, Any]) -> Any:
    """Dispatch an agent tool by name (used by the CLI transport)."""
    if name not in AGENT_TOOLS:
        raise QlsError(f"Unknown tool {name!r}. Tools: {', '.join(AGENT_TOOLS)}")
    try:
        return getattr(session, name if name != "recipe_info" else "recipe_info")(**args)
    except TypeError as exc:
        raise QlsError(f"{name}: {exc}") from exc
