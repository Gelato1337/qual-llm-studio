"""Compare analyses structurally, not by label.

Two runs rarely share labels, so we compare which elements they group
together. Elements are:

  * concepts, when both runs share the same 1st-order concepts (forks of the
    same coding/consolidation run, e.g. Claude's grouping vs the researcher's);
  * quote spans otherwise (segment + character span), so independent codings
    can still be compared where they quoted the same text.

Metrics per level: Rand index (as used in the AMCIS paper, for continuity),
adjusted Rand index (corrects for chance), and NMI. Elements left unassigned
in either run are excluded and reported as coverage.
"""

from __future__ import annotations

import math
from collections import Counter
from itertools import combinations

from .project import Project, QlsError
from .views import active, membership


def _c2(x: int) -> float:
    return x * (x - 1) / 2


def partition_scores(a: dict[str, str], b: dict[str, str]) -> dict:
    keys = sorted(set(a) & set(b))
    n = len(keys)
    if n < 2:
        return {"n": n, "rand": None, "ari": None, "nmi": None}
    nij = Counter((a[k], b[k]) for k in keys)
    ai = Counter(a[k] for k in keys)
    bj = Counter(b[k] for k in keys)
    tp = sum(_c2(v) for v in nij.values())
    sa = sum(_c2(v) for v in ai.values())
    sb = sum(_c2(v) for v in bj.values())
    total = _c2(n)
    rand = (total + 2 * tp - sa - sb) / total
    expected = sa * sb / total
    mx = (sa + sb) / 2
    ari = 1.0 if mx == expected else (tp - expected) / (mx - expected)

    def h(c: Counter) -> float:
        return -sum(v / n * math.log(v / n) for v in c.values())

    mi = sum(v / n * math.log(n * v / (ai[x] * bj[y])) for (x, y), v in nij.items())
    ha, hb = h(ai), h(bj)
    nmi = 1.0 if ha + hb == 0 else mi / ((ha + hb) / 2)
    return {"n": n, "rand": round(rand, 4), "ari": round(ari, 4), "nmi": round(nmi, 4)}


def _lineage(project: Project, run_id: str) -> set[str]:
    out, cur = set(), run_id
    while cur and cur not in out and cur in project.run_ids():
        out.add(cur)
        cur = project.run(cur).manifest().get("parent")
    return out


def _spans(state: dict, c: dict) -> tuple:
    return tuple(sorted((state["quotes"][q]["segment"], state["quotes"][q]["start"], state["quotes"][q]["end"]) for q in c["quotes"]))


def _shared_concepts(project: Project, a_id: str, b_id: str, sa: dict, sb: dict) -> set[str] | None:
    """Concepts both runs hold unchanged, if the runs descend from a common run; else None.

    Concept IDs are per-run counters, so equal IDs mean the same concept only
    for forks of a common ancestor, and only while their quotes are identical
    (a concept merged or split in one run is left out of the comparison).
    """
    if not (_lineage(project, a_id) & _lineage(project, b_id)):
        return None
    ca = {c["id"]: _spans(sa, c) for c in active(sa, "concepts")}
    cb = {c["id"]: _spans(sb, c) for c in active(sb, "concepts")}
    common = {c for c in set(ca) & set(cb) if ca[c] == cb[c]}
    return common or None


def _labels(state: dict, by: str) -> dict[str, dict[str, str]]:
    """element -> group id, for levels concept/theme/dimension."""
    c2t, t2a = membership(state)
    out = {"concept": {}, "theme": {}, "dimension": {}}
    if by == "concept":
        for c in active(state, "concepts"):
            cid = c["id"]
            out["concept"][cid] = cid
            if cid in c2t:
                out["theme"][cid] = c2t[cid]
                if c2t[cid] in t2a:
                    out["dimension"][cid] = t2a[c2t[cid]]
        return out
    for c in active(state, "concepts"):
        for qid in c["quotes"]:
            q = state["quotes"][qid]
            el = f'{q["segment"]}@{q["start"]}-{q["end"]}'
            out["concept"][el] = c["id"]
            if c["id"] in c2t:
                out["theme"][el] = c2t[c["id"]]
                if c2t[c["id"]] in t2a:
                    out["dimension"][el] = t2a[c2t[c["id"]]]
    return out


def _groups(lab: dict[str, str]) -> dict[str, set]:
    g: dict[str, set] = {}
    for el, grp in lab.items():
        g.setdefault(grp, set()).add(el)
    return g


def _jacc(x: set, y: set) -> float:
    return len(x & y) / len(x | y) if x | y else 0.0


def match_groups(la: dict[str, str], lb: dict[str, str]) -> list[dict]:
    """For each group in A, the best-overlapping group in B (by Jaccard over shared elements)."""
    shared = set(la) & set(lb)
    ga = {k: v & shared for k, v in _groups(la).items()}
    gb = {k: v & shared for k, v in _groups(lb).items()}
    rows = []
    for ka, ea in ga.items():
        best = max(gb.items(), key=lambda kv: _jacc(ea, kv[1]), default=(None, set()))
        rows.append({"a": ka, "b": best[0], "jaccard": round(_jacc(ea, best[1]), 3),
                     "only_a": sorted(ea - best[1]), "only_b": sorted(best[1] - ea), "both": sorted(ea & best[1])})
    return sorted(rows, key=lambda r: -r["jaccard"])


def compare_runs(project: Project, a_id: str, b_id: str) -> dict:
    ra, rb = project.run(a_id), project.run(b_id)
    sa, sb = ra.state(), rb.state()
    shared = _shared_concepts(project, a_id, b_id, sa, sb)
    by = "concept" if shared else "quote"
    la, lb = _labels(sa, by), _labels(sb, by)
    excluded = []
    if shared:
        excluded = sorted(({c["id"] for c in active(sa, "concepts")} | {c["id"] for c in active(sb, "concepts")}) - shared)
        for lab in (*la.values(), *lb.values()):
            for k in [k for k in lab if k not in shared]:
                del lab[k]
    levels = {}
    for lvl in ("concept", "theme", "dimension"):
        if by == "concept" and lvl == "concept":
            continue
        sc = partition_scores(la[lvl], lb[lvl])
        sc["coverage_a"] = len(la[lvl])
        sc["coverage_b"] = len(lb[lvl])
        sc["groups_a"] = len(set(la[lvl].values()))
        sc["groups_b"] = len(set(lb[lvl].values()))
        levels[lvl] = sc
    names = {
        "a": {**{t["id"]: t["label"] for t in active(sa, "themes")}, **{d["id"]: d["label"] for d in active(sa, "dimensions")}},
        "b": {**{t["id"]: t["label"] for t in active(sb, "themes")}, **{d["id"]: d["label"] for d in active(sb, "dimensions")}},
    }
    if by == "concept":
        element_labels = {c["id"]: c["label"] for c in sa["concepts"].values()}
    else:
        element_labels = {}
        for st in (sa, sb):
            for q in st["quotes"].values():
                t = q["text"] if len(q["text"]) <= 70 else q["text"][:67] + "..."
                element_labels.setdefault(f'{q["segment"]}@{q["start"]}-{q["end"]}', f'"{t}" ({q["segment"]})')
    return {
        "a": a_id,
        "b": b_id,
        "elements": by,
        "levels": levels,
        "theme_matches": match_groups(la["theme"], lb["theme"]),
        "dimension_matches": match_groups(la["dimension"], lb["dimension"]),
        "names": names,
        "element_labels": element_labels,
        "excluded_concepts": excluded,
    }


def consensus(project: Project, run_ids: list[str], level: str = "theme") -> dict:
    """Stability across N runs that share concepts (e.g. N independent groupings).

    For each pair of concepts: in how many runs are they grouped together?
    For each concept: how consistently does it keep the same companions?
    """
    if len(run_ids) < 2:
        raise QlsError("consensus needs at least two runs")
    states = [project.run(r).state() for r in run_ids]
    for rid, s in zip(run_ids[1:], states[1:]):
        if not _shared_concepts(project, run_ids[0], rid, states[0], s):
            raise QlsError("consensus needs runs forked from a common run (they must share 1st-order concepts)")
    labs = [_labels(s, "concept")[level] for s in states]
    # concepts present (active) in every run; unassigned ones count as their own group
    shared = set.intersection(*[{c["id"] for c in active(s, "concepts")} for s in states])
    labs = [{c: lab.get(c, f"(unassigned {c})") for c in shared} for lab in labs]
    concepts = sorted(shared)
    pair_freq = {}
    for x, y in combinations(concepts, 2):
        k = sum(1 for lab in labs if lab[x] == lab[y])
        if k:
            pair_freq[(x, y)] = k
    n = len(run_ids)
    stability = {}
    for c in concepts:
        mates = [{d for d in concepts if d != c and lab[d] == lab[c]} for lab in labs]
        scores = [_jacc(mates[i], mates[j]) if (mates[i] or mates[j]) else 1.0 for i, j in combinations(range(n), 2)]
        stability[c] = round(sum(scores) / len(scores), 3)
    pairwise = {f"{run_ids[i]}|{run_ids[j]}": partition_scores(labs[i], labs[j]) for i, j in combinations(range(n), 2)}
    core = sorted([(x, y, k) for (x, y), k in pair_freq.items() if k == n])
    return {
        "runs": run_ids,
        "level": level,
        "n_concepts": len(concepts),
        "pairwise": pairwise,
        "concept_stability": dict(sorted(stability.items(), key=lambda kv: kv[1])),
        "always_together": [{"a": x, "b": y} for x, y, _ in core],
        "labels": {c["id"]: c["label"] for c in states[0]["concepts"].values()},
    }
