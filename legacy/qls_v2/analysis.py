"""Compare runs by structure, not labels.

Elements are the fixed informant segments created at ingestion, so two runs that
quoted different spans are still compared on the same units (the unitisation
problem in intercoder agreement, Campbell et al. 2013). For each level we map every
segment to the set of groups it falls in:

    concept level    segment -> concepts quoting it
    theme level      segment -> themes of those concepts
    dimension level  segment -> dimensions of those themes

A segment with several groups gets the set as its label. Scores per level: Rand
index, adjusted Rand index, NMI (on segments covered in both runs), and pairwise
co-assignment F1 (do two segments share any group?), which handles overlap.
"""

from __future__ import annotations

import math
from collections import Counter
from itertools import combinations

from .store import Store


def _c2(x: int) -> float:
    return x * (x - 1) / 2


def partition_scores(a: dict[str, str], b: dict[str, str]) -> dict:
    keys = sorted(set(a) & set(b))
    n = len(keys)
    if n < 2:
        return {"n": n, "rand": None, "ari": None, "nmi": None}
    nij = Counter((a[k], b[k]) for k in keys)
    ai, bj = Counter(a[k] for k in keys), Counter(b[k] for k in keys)
    tp = sum(_c2(v) for v in nij.values())
    sa, sb, total = sum(_c2(v) for v in ai.values()), sum(_c2(v) for v in bj.values()), _c2(n)
    rand = (total + 2 * tp - sa - sb) / total
    expected, mx = sa * sb / total, (sa + sb) / 2
    ari = 1.0 if mx == expected else (tp - expected) / (mx - expected)

    def h(c: Counter) -> float:
        return -sum(v / n * math.log(v / n) for v in c.values())

    mi = sum(v / n * math.log(n * v / (ai[x] * bj[y])) for (x, y), v in nij.items())
    ha, hb = h(ai), h(bj)
    nmi = 1.0 if ha + hb == 0 else mi / ((ha + hb) / 2)
    return {"n": n, "rand": round(rand, 4), "ari": round(ari, 4), "nmi": round(nmi, 4)}


def pair_f1(a: dict[str, set], b: dict[str, set]) -> float | None:
    keys = sorted(set(a) & set(b))
    pa = {(x, y) for x, y in combinations(keys, 2) if a[x] & a[y]}
    pb = {(x, y) for x, y in combinations(keys, 2) if b[x] & b[y]}
    if not pa and not pb:
        return None
    tp = len(pa & pb)
    return round(2 * tp / (len(pa) + len(pb)), 4) if (pa or pb) else None


def segment_groups(store: Store, run: str) -> dict[str, dict[str, set]]:
    """level -> segment -> set of group ids (active objects only)."""
    quotes = {q["id"]: q["fields"]["unit"] for q in store.objects(run, "Quote")}
    c2segs: dict[str, set] = {}
    for c in store.objects(run, "Concept"):
        c2segs[c["id"]] = {quotes[l["dst"]] for l in store.links(run, src=c["id"], rel="evidenced_by") if l["dst"] in quotes}
    t_of: dict[str, set] = {}
    for t in store.objects(run, "Theme"):
        for l in store.links(run, src=t["id"], rel="groups"):
            t_of.setdefault(l["dst"], set()).add(t["id"])
    d_of: dict[str, set] = {}
    for d in store.objects(run, "Dimension"):
        for l in store.links(run, src=d["id"], rel="groups"):
            d_of.setdefault(l["dst"], set()).add(d["id"])
    out = {"concept": {}, "theme": {}, "dimension": {}}
    for cid, segs in c2segs.items():
        themes = t_of.get(cid, set())
        dims = set().union(*[d_of.get(t, set()) for t in themes]) if themes else set()
        for s in segs:
            out["concept"].setdefault(s, set()).add(cid)
            if themes:
                out["theme"].setdefault(s, set()).update(themes)
            if dims:
                out["dimension"].setdefault(s, set()).update(dims)
    return out


def compare(store: Store, a: str, b: str) -> dict:
    ga, gb = segment_groups(store, a), segment_groups(store, b)
    levels = {}
    for lvl in ("concept", "theme", "dimension"):
        la = {k: "|".join(sorted(v)) for k, v in ga[lvl].items()}
        lb = {k: "|".join(sorted(v)) for k, v in gb[lvl].items()}
        sc = partition_scores(la, lb)
        sc.update(pair_f1=pair_f1(ga[lvl], gb[lvl]), segments_a=len(la), segments_b=len(lb),
                  groups_a=len({g for v in ga[lvl].values() for g in v}), groups_b=len({g for v in gb[lvl].values() for g in v}))
        levels[lvl] = sc
    return {"a": a, "b": b, "levels": levels}
