"""Run-level measures for the context experiments.

Genericness is the AMCIS failure ("lost meaning"): labels that drift away
from what informants said towards vocabulary that would fit any study.

  informant_language   share of a label's content words that occur in the quotes
                       under it (concepts: own quotes; themes: all member quotes)
  generic_share        share of theme/dimension label words from a list of
                       all-purpose research words (challenges, factors, dynamics...)
  voices               informants behind each theme, and the share of its quotes
                       coming from the single most-quoted informant
  coverage             concepts placed in a theme / all concepts

These are screening measures, not validity: they flag where to look, and a
human rating of informativeness is still needed for the paper.
"""

from __future__ import annotations

import re
from statistics import mean

from .project import Project
from .views import active, informant_language

GENERIC = set("""
challenge challenges issue issues factor factors aspect aspects dynamic dynamics dimension dimensions
element elements driver drivers landscape ecosystem transformation transformations change changes
impact impacts implication implications consideration considerations perspective perspectives
management strategy strategies strategic innovation adoption process processes approach approaches
development developments trend trends evolution technology technologies technological digital
organisational organizational organization organisation business context contextual nature role roles
importance key various general overall emerging future current new
""".split())

_STOP = set("the a an and or but of to in on for with is are as by from into at its their this that be".split())


def _words(s: str) -> list[str]:
    return [w for w in re.findall(r"[\wÅÄÖåäö]+", s.lower()) if len(w) > 2 and w not in _STOP]


def _label_grounding(label: str, text: str) -> float | None:
    ws = [w for w in _words(label) if w not in GENERIC]
    if not ws:
        return 0.0 if _words(label) else None
    t = text.lower()
    return sum(1 for w in ws if w in t) / len(ws)


def run_metrics(project: Project, run_id: str) -> dict:
    run = project.run(run_id)
    s, m = run.state(), run.manifest()
    cs, ts, ds = active(s, "concepts"), active(s, "themes"), active(s, "dimensions")
    c_il = [v for c in cs if (v := informant_language(s, c["id"])) is not None]
    themes = []
    for t in ts:
        qids = [q for cid in t["concepts"] for q in s["concepts"][cid]["quotes"]]
        docs = [s["quotes"][q]["doc"] for q in qids]
        top = max((docs.count(d) for d in set(docs)), default=0)
        text = " ".join(s["quotes"][q]["text"] for q in qids)
        lw = _words(t["label"])
        themes.append({
            "id": t["id"], "label": t["label"], "concepts": len(t["concepts"]),
            "informants": len(set(docs)), "top_informant_share": round(top / len(docs), 2) if docs else None,
            "informant_language": None if (g := _label_grounding(t["label"], text)) is None else round(g, 2),
            "generic_share": round(sum(w in GENERIC for w in lw) / len(lw), 2) if lw else None,
        })
    dim_words = [w for d in ds for w in _words(d["label"])]

    def avg(xs):
        xs = [x for x in xs if x is not None]
        return round(mean(xs), 3) if xs else None

    placed = {c for t in ts for c in t["concepts"]}
    return {
        "run": run_id,
        "kind": m.get("kind"),
        "view": m.get("view"),
        "verify": m.get("verify"),
        "concepts": len(cs),
        "themes": len(ts),
        "dimensions": len(ds),
        "coverage": round(len(placed) / len(cs), 3) if cs else None,
        "concept_informant_language": avg(c_il),
        "theme_informant_language": avg(t["informant_language"] for t in themes),
        "theme_generic_share": avg(t["generic_share"] for t in themes),
        "dimension_generic_share": round(sum(w in GENERIC for w in dim_words) / len(dim_words), 3) if dim_words else None,
        "theme_informants_mean": avg(t["informants"] for t in themes),
        "theme_top_informant_share_mean": avg(t["top_informant_share"] for t in themes),
        "memos": len(s["memos"]),
        "concepts_with_coding_memo": sum(1 for c in cs if c.get("memo")),
        "decisions_with_evidence": sum(1 for d in run.decisions() if d.get("evidence")),
        "process": m.get("process"),
        "themes_detail": themes,
    }
