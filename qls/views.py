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


def ancestors(state: dict, cid: str) -> list[str]:
    """Concepts this one was merged or split from, recursively (oldest last)."""
    out, todo = [], [cid]
    while todo:
        c = state["concepts"].get(todo.pop())
        if not c:
            continue
        parents = c.get("origin", {}).get("merged_from") or ([c["origin"]["split_from"]] if c.get("origin", {}).get("split_from") else [])
        for p in parents:
            if p not in out:
                out.append(p)
                todo.append(p)
    return out


def concept_memos(state: dict, cid: str) -> list[dict]:
    """Coding memos of a concept and of every concept it was merged/split from.

    This is how meaning is carried through consolidation: a merged concept
    keeps the notes its parts were coded with.
    """
    out = []
    for x in [cid, *ancestors(state, cid)]:
        m = state["concepts"].get(x, {}).get("memo")
        if m:
            out.append({"concept": x, "label": state["concepts"][x]["label"], **m})
    return out


def memos_about(state: dict, ref: str, include_lineage: bool = True) -> list[dict]:
    """Memos linked to ref (and, for concepts, to the concepts it came from)."""
    refs = {ref}
    if include_lineage and ref in state["concepts"]:
        refs |= set(ancestors(state, ref))
    return [m for m in state["memos"].values() if refs & set(m.get("links", []))]


def concept_card(project: Project, state: dict, cid: str, max_quotes: int = 3, context: bool = True) -> dict:
    c = state["concepts"].get(cid)
    if c is None:
        raise QlsError(f"Unknown concept {cid!r}")
    c2t, _ = membership(state)
    return {
        "id": cid,
        "label": c["label"],
        "description": c["description"],
        "coding_memos": concept_memos(state, cid),
        "memos": [{"id": m["id"], "kind": m.get("kind", "analytic"), "author": m["author"], "text": m["text"]}
                  for m in memos_about(state, cid)],
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


# ---------------------------------------------------------------------------
# Context packs: rebuild context at decision time
# ---------------------------------------------------------------------------


def _neighbours(project: Project, seg_id: str, n: int = 1) -> dict:
    """The segment's turn plus n turns before and after it (the conversation around a quote)."""
    seg = project.segment(seg_id)
    d = project.doc(seg_id.split(":", 1)[0])
    idx = next(i for i, t in enumerate(d["turns"]) if t["id"] == seg["turn"])
    lo, hi = max(0, idx - n), min(len(d["turns"]), idx + n + 1)
    return {"doc": d["id"], "participant": d.get("participant"),
            "turns": [{"id": t["id"], "speaker": t["speaker"], "role": t["role"], "text": t["text"]} for t in d["turns"][lo:hi]]}


def _spread(state: dict, qids: list[str], k: int) -> list[str]:
    """Pick up to k quotes, one informant at a time, so a pack is not one voice."""
    by_doc: dict[str, list[str]] = {}
    for q in qids:
        by_doc.setdefault(state["quotes"][q]["doc"], []).append(q)
    out, i = [], 0
    while len(out) < k and any(len(v) > i for v in by_doc.values()):
        out += [v[i] for v in by_doc.values() if len(v) > i][: k - len(out)]
        i += 1
    return out


def context_pack(project: Project, state: dict, refs: list[str], max_chars: int = 12000,
                 quotes_per_concept: int = 3, neighbours: int = 1) -> str:
    """Everything an analyst needs to decide about refs, rebuilt from the data.

    Order matters: definitions and memos first, then the data itself (quotes
    in their conversation), cut at max_chars. Concepts are summarised for
    themes and dimensions, with quotes spread across informants.
    """
    c2t, t2a = membership(state)
    parts: list[str] = []

    def concept_block(cid: str, k: int, with_turns: bool) -> list[str]:
        c = state["concepts"][cid]
        docs = sorted({state["quotes"][q]["doc"] for q in c["quotes"]})
        lines = [f"### {cid}: {c['label']}  [{len(docs)} informants: {', '.join(docs)}; {len(c['quotes'])} quotes]",
                 c["description"]]
        for m in concept_memos(state, cid):
            note = "; ".join(f"{k2}: {v}" for k2, v in m.items() if k2 not in ("concept", "label"))
            lines.append(f"- coding memo ({m['concept']}): {note}")
        for m in memos_about(state, cid):
            lines.append(f"- memo {m['id']} [{m.get('kind', 'analytic')}, {m['author']}]: {m['text']}")
        for qid in _spread(state, c["quotes"], k):
            q = state["quotes"][qid]
            if with_turns:
                nb = _neighbours(project, q["segment"], neighbours)
                lines.append(f"- quote {qid} ({q['segment']}), in context:")
                for t in nb["turns"]:
                    mark = ">>" if t["id"] == project.segment(q["segment"])["turn"] else "  "
                    lines.append(f"  {mark} {t['speaker']} ({t['role']}): {t['text']}")
            else:
                seg = project.segment(q["segment"])
                qq = f"  [Q: {seg['question'][:200]}]" if seg.get("question") else ""
                lines.append(f"- {qid} {q['segment']}: \"{q['text']}\"{qq}")
        return lines

    for ref in refs:
        if ref in state["concepts"]:
            parts += concept_block(ref, quotes_per_concept, with_turns=True)
        elif ref in state["themes"]:
            t = state["themes"][ref]
            parts += [f"## theme {ref}: {t['label']}  (dimension {t2a.get(ref, '-')})", t["definition"]]
            parts += [f"- memo {m['id']} [{m.get('kind', 'analytic')}, {m['author']}]: {m['text']}" for m in memos_about(state, ref)]
            for cid in t["concepts"]:
                parts += concept_block(cid, max(1, quotes_per_concept - 1), with_turns=False)
        elif ref in state["dimensions"]:
            a = state["dimensions"][ref]
            parts += [f"## dimension {ref}: {a['label']}", a["definition"]]
            parts += [f"- memo {m['id']} [{m.get('kind', 'analytic')}, {m['author']}]: {m['text']}" for m in memos_about(state, ref)]
            for tid in a["themes"]:
                t = state["themes"][tid]
                parts.append(f"### theme {tid}: {t['label']}: {t['definition']}")
                for cid in t["concepts"]:
                    c = state["concepts"][cid]
                    m = c.get("memo", {}).get("meaning_here", "")
                    parts.append(f"- {cid}: {c['label']}" + (f" (meaning here: {m})" if m else ""))
        elif ":" in ref:  # segment or turn
            seg_id = ref
            try:
                project.segment(seg_id)
            except QlsError:
                d = project.doc(ref.split(":", 1)[0])
                seg_id = next((x["id"] for x in d["segments"] if x["turn"] == ref), None)
                if seg_id is None:
                    raise QlsError(f"Unknown segment or turn {ref!r}")
            nb = _neighbours(project, seg_id, neighbours)
            parts.append(f"## {ref} ({nb['participant']})")
            parts += [f"  {t['speaker']} ({t['role']}): {t['text']}" for t in nb["turns"]]
            users = [c["id"] for c in active(state, "concepts") if any(state["quotes"][q]["segment"] == seg_id for q in c["quotes"])]
            coded = ", ".join(c + " " + state["concepts"][c]["label"] for c in users) or "(nothing)"
            parts.append(f"coded as: {coded}")
        else:
            raise QlsError(f"Unknown reference {ref!r}")
        parts.append("")

    text, out = 0, []
    for line in parts:
        if text + len(line) > max_chars:
            out.append(f"[... cut at {max_chars} characters; ask for fewer references or raise max_chars]")
            break
        out.append(line)
        text += len(line) + 1
    return "\n".join(out)
