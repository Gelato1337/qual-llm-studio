"""Read-only views of a run and the corpus, shared by the CLI, MCP server and reports.

Views return plain dicts/strings sized for an agent's context: concept cards
carry the label, description, informants, quotes and the question each quote
answered, so grouping works from meaning, not bare labels (AMCIS challenge #9).
"""

from __future__ import annotations

import re

from .project import Project, QlsError, Run

_STOP = set(
    "the a an and or but of to in on for with is are was were it that this be have has do not more less new "
    "ja on ei se että kuin myös".split()
)


def active(state: dict, kind: str) -> list[dict]:
    return [x for x in state[kind].values() if x["status"] == "active"]


def membership(state: dict) -> tuple[dict, dict]:
    c2t, t2a = {}, {}
    for t in active(state, "themes"):
        for c in t["concepts"]:
            c2t[c] = t["id"]
    for a in active(state, "dimensions"):
        for t in a["themes"]:
            t2a[t] = a["id"]
    return c2t, t2a


def informants(state: dict, cid: str, project: Project | None = None) -> list[str]:
    docs = sorted({state["quotes"][q]["doc"] for q in state["concepts"][cid]["quotes"]})
    if project is None:
        return docs
    return sorted({project.doc(d).get("participant", d) for d in docs})


def informant_language(state: dict, cid: str) -> float | None:
    """Share of a label's content words that occur in its own quotes.

    A rough measure of how close a label stays to how informants talk;
    low values flag generic or academic labels (AMCIS: over-generalisation).
    """
    c = state["concepts"][cid]
    words = {w for w in re.findall(r"[\wÅÄÖåäö]+", c["label"].lower()) if len(w) > 2 and w not in _STOP}
    if not words:
        return None
    text = " ".join(state["quotes"][q]["text"].lower() for q in c["quotes"])
    return round(sum(1 for w in words if w in text) / len(words), 2)


def quote_view(project: Project, state: dict, qid: str, context: bool = True) -> dict:
    q = state["quotes"][qid]
    out = {"id": qid, "doc": q["doc"], "segment": q["segment"], "text": q["text"], "score": q.get("score")}
    if context:
        try:
            seg = project.segment(q["segment"])
            out["question"] = seg.get("question")
        except QlsError:
            pass
    return out


def concept_card(project: Project, state: dict, cid: str, max_quotes: int = 3, context: bool = True) -> dict:
    c = state["concepts"].get(cid)
    if c is None:
        raise QlsError(f"Unknown concept {cid!r}")
    c2t, _ = membership(state)
    return {
        "id": cid,
        "label": c["label"],
        "description": c["description"],
        "status": c["status"],
        "merged_into": c.get("merged_into"),
        "theme": c2t.get(cid),
        "informants": informants(state, cid, project),
        "n_quotes": len(c["quotes"]),
        "flags": c.get("flags", []),
        "informant_language": informant_language(state, cid),
        "quotes": [quote_view(project, state, q, context) for q in c["quotes"][:max_quotes]],
    }


def status(project: Project, run: Run) -> dict:
    s = run.state()
    c2t, t2a = membership(s)
    concepts = active(s, "concepts")
    themes = active(s, "themes")
    dims = active(s, "dimensions")
    m = run.manifest()
    return {
        "run": run.id,
        "kind": m.get("kind"),
        "parent": m.get("parent"),
        "concepts": len(concepts),
        "themes": len(themes),
        "dimensions": len(dims),
        "memos": len(s["memos"]),
        "decisions": len(run.decisions()),
        "unassigned_concepts": [c["id"] for c in concepts if c["id"] not in c2t],
        "themes_without_dimension": [t["id"] for t in themes if t["id"] not in t2a],
        "empty_themes": [t["id"] for t in themes if not t["concepts"]],
        "flagged_concepts": {c["id"]: c["flags"] for c in concepts if c.get("flags")},
    }


def check(run: Run) -> list[str]:
    """Integrity checks: lossless merges/splits, valid references."""
    s = run.state()
    problems = []
    owner: dict[str, list[str]] = {}
    for c in s["concepts"].values():
        if c["status"] in ("active", "dropped"):
            for q in c["quotes"]:
                owner.setdefault(q, []).append(c["id"])
    for qid in s["quotes"]:
        if qid not in owner:
            problems.append(f"quote {qid} belongs to no active or dropped concept (lost)")
    for t in active(s, "themes"):
        for c in t["concepts"]:
            if s["concepts"].get(c, {}).get("status") != "active":
                problems.append(f"theme {t['id']} lists non-active concept {c}")
    seen: dict[str, str] = {}
    for t in active(s, "themes"):
        for c in t["concepts"]:
            if c in seen:
                problems.append(f"concept {c} is in two themes ({seen[c]}, {t['id']})")
            seen[c] = t["id"]
    for a in active(s, "dimensions"):
        for t in a["themes"]:
            if s["themes"].get(t, {}).get("status") != "active":
                problems.append(f"dimension {a['id']} lists non-active theme {t}")
    return problems


def structure_text(project: Project, run: Run, with_concepts: bool = True) -> str:
    s = run.state()
    c2t, t2a = membership(s)
    lines = []

    def concept_line(cid: str) -> str:
        c = s["concepts"][cid]
        return f"      - {cid}: {c['label']}  [{len(informants(s, cid))} inf, {len(c['quotes'])} q]"

    for a in active(s, "dimensions"):
        lines.append(f"{a['id']}: {a['label']}")
        for tid in a["themes"]:
            t = s["themes"][tid]
            lines.append(f"   {tid}: {t['label']}  ({len(t['concepts'])} concepts)")
            if with_concepts:
                lines += [concept_line(c) for c in t["concepts"]]
    loose_t = [t for t in active(s, "themes") if t["id"] not in t2a]
    if loose_t:
        lines.append("(themes without a dimension)")
        for t in loose_t:
            lines.append(f"   {t['id']}: {t['label']}  ({len(t['concepts'])} concepts)")
            if with_concepts:
                lines += [concept_line(c) for c in t["concepts"]]
    loose_c = [c["id"] for c in active(s, "concepts") if c["id"] not in c2t]
    if loose_c:
        lines.append(f"(unassigned concepts: {len(loose_c)})")
        if with_concepts:
            lines += [concept_line(c) for c in loose_c]
    return "\n".join(lines) or "(empty run)"


def search_corpus(project: Project, pattern: str, docs: list[str] | None = None, limit: int = 30,
                  regex: bool = False, informant_only: bool = True) -> list[dict]:
    flags = re.IGNORECASE
    rx = re.compile(pattern if regex else re.escape(pattern), flags)
    hits = []
    for doc_id in docs or project.doc_ids():
        d = project.doc(doc_id)
        units = d["segments"] if informant_only else d["turns"]
        for u in units:
            for m in rx.finditer(u["text"]):
                a, b = max(0, m.start() - 160), min(len(u["text"]), m.end() + 160)
                hits.append({"doc": doc_id, "id": u["id"], "match": m.group(0),
                             "snippet": ("…" if a else "") + u["text"][a:b] + ("…" if b < len(u["text"]) else "")})
                if len(hits) >= limit:
                    return hits
    return hits


def read_document(project: Project, doc_id: str, start: int = 1, limit: int = 40) -> dict:
    d = project.doc(doc_id)
    turns = d["turns"][start - 1:start - 1 + limit]
    seg_by_turn: dict[str, list[str]] = {}
    for s in d["segments"]:
        seg_by_turn.setdefault(s["turn"], []).append(s["id"])
    return {
        "doc": doc_id,
        "participant": d.get("participant"),
        "n_turns": len(d["turns"]),
        "turns": [{"id": t["id"], "speaker": t["speaker"], "role": t["role"], "segments": seg_by_turn.get(t["id"], []),
                   "text": t["text"]} for t in turns],
    }


def concept_informant_matrix(project: Project, state: dict) -> tuple[list[str], list[dict]]:
    docs = project.doc_ids()
    rows = []
    for c in active(state, "concepts"):
        counts = {d: 0 for d in docs}
        for q in c["quotes"]:
            counts[state["quotes"][q]["doc"]] = counts.get(state["quotes"][q]["doc"], 0) + 1
        rows.append({"id": c["id"], "label": c["label"], "counts": counts})
    return docs, rows
