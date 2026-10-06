"""Static HTML reports: one file, no server, opens in any browser.

run report:      Gioia data structure, concept cards with quotes in context,
                 concept x informant matrix, memos, decision log.
compare report:  agreement scores per level, then only the groupings that
                 differ (identical ones are collapsed).
"""

from __future__ import annotations

from html import escape as e

from .compare import compare_runs
from .project import Project
from .views import active, concept_card, concept_informant_matrix, membership

CSS = """
:root{--bg:#fbfaf7;--fg:#1d1d1b;--muted:#6b6a64;--line:#e3e0d8;--card:#fff;--accent:#2f5d8a;--warn:#a5531b;--hl:#eef3f8}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--fg:#e9e7e1;--muted:#a19f97;--line:#34332f;--card:#1f1f1d;--accent:#8fb4dc;--warn:#e19a62;--hl:#22303d}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px 64px}h1{font-size:1.5rem;margin:0 0 4px}h2{font-size:1.15rem;margin:36px 0 10px;border-bottom:1px solid var(--line);padding-bottom:4px}
.muted{color:var(--muted)}.small{font-size:.85rem}code{font-size:.85em}
table{border-collapse:collapse;width:100%}th,td{border:1px solid var(--line);padding:6px 8px;vertical-align:top;text-align:left}th{background:var(--card);font-weight:600}
.ds td{background:var(--card)}.ds .dim{font-weight:700}.ds .theme{font-weight:600}
details{background:var(--card);border:1px solid var(--line);border-radius:6px;margin:6px 0;padding:6px 10px}summary{cursor:pointer}
blockquote{margin:8px 0;padding:6px 10px;border-left:3px solid var(--accent);background:var(--hl)}
.q{color:var(--muted);font-size:.85rem;margin-bottom:2px}.flag{color:var(--warn);font-size:.8rem;margin-left:6px}
.pill{display:inline-block;border:1px solid var(--line);border-radius:10px;padding:0 7px;font-size:.8rem;margin-right:4px;color:var(--muted)}
.wrap{overflow-x:auto}.mx td{text-align:center;min-width:28px}.mx td.l{text-align:left;min-width:260px}
.h1{background:color-mix(in srgb,var(--accent) 25%,transparent)}.h2{background:color-mix(in srgb,var(--accent) 50%,transparent)}.h3{background:color-mix(in srgb,var(--accent) 75%,transparent)}
.score{font-variant-numeric:tabular-nums}
"""


def _page(title: str, body: str) -> str:
    return (f"<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{e(title)}</title><style>{CSS}</style></head><body><main>{body}</main></body></html>")


def _concept_html(card: dict) -> str:
    flags = "".join(f"<span class=flag>{e(f)}</span>" for f in card["flags"])
    il = card["informant_language"]
    meta = (f"<span class=pill>{len(card['informants'])} informants</span><span class=pill>{card['n_quotes']} quotes</span>"
            + (f"<span class=pill>informant words {il:.0%}</span>" if il is not None else ""))
    quotes = "".join(
        (f"<div class=q>Q: {e(q['question'][:240])}</div>" if q.get("question") else "")
        + f"<blockquote>{e(q['text'])}<div class='muted small'>{e(q['segment'])}</div></blockquote>"
        for q in card["quotes"]
    )
    return (f"<details><summary><b>{e(card['id'])}</b> {e(card['label'])}{flags}</summary>"
            f"<p>{e(card['description'])}</p><p>{meta}</p>{quotes}</details>")


def run_report(project: Project, run_id: str, max_quotes: int = 5) -> str:
    run = project.run(run_id)
    s = run.state()
    m = run.manifest()
    c2t, t2a = membership(s)
    cs, ts, ds = active(s, "concepts"), active(s, "themes"), active(s, "dimensions")
    out = [f"<h1>Run {e(run_id)}</h1>",
           f"<p class=muted>{e(m.get('kind', ''))} · parent {e(str(m.get('parent')))} · created {e(m.get('created', ''))}"
           f" · model {e(str((m.get('llm') or {}).get('model') or m.get('analyst', {}).get('model') or '–'))}</p>",
           f"<p><span class=pill>{len(cs)} concepts</span><span class=pill>{len(ts)} themes</span>"
           f"<span class=pill>{len(ds)} dimensions</span><span class=pill>{len(s['memos'])} memos</span>"
           f"<span class=pill>{len(run.decisions())} decisions</span></p>"]

    # data structure
    out.append("<h2>Data structure</h2><div class=wrap><table class=ds><tr><th>1st-order concepts</th><th>2nd-order themes</th><th>Aggregate dimensions</th></tr>")

    def theme_rows(tids: list[str], dim_cell: str | None) -> list[str]:
        rows = []
        total = sum(max(len(s["themes"][t]["concepts"]), 1) for t in tids)
        first_dim = True
        for tid in tids:
            t = s["themes"][tid]
            cids = t["concepts"] or [None]
            for i, cid in enumerate(cids):
                r = "<tr>"
                r += f"<td>{e(s['concepts'][cid]['label']) if cid else '<span class=muted>(none)</span>'}</td>"
                if i == 0:
                    r += f"<td class=theme rowspan={len(cids)}>{e(t['label'])}<div class='muted small'>{e(t['definition'])}</div></td>"
                if first_dim and dim_cell is not None:
                    r += f"<td class=dim rowspan={total}>{dim_cell}</td>"
                    first_dim = False
                rows.append(r + "</tr>")
        return rows

    for d in ds:
        if d["themes"]:
            out += theme_rows(d["themes"], f"{e(d['label'])}<div class='muted small'>{e(d['definition'])}</div>")
    loose_t = [t["id"] for t in ts if t["id"] not in t2a]
    if loose_t:
        out += theme_rows(loose_t, "<span class=muted>(no dimension)</span>")
    out.append("</table></div>")
    loose_c = [c for c in cs if c["id"] not in c2t]
    if loose_c:
        out.append(f"<p class=muted>{len(loose_c)} concepts not assigned to a theme.</p>")

    # concept cards
    out.append("<h2>Concepts</h2>")
    by_theme: dict[str | None, list[str]] = {}
    for c in cs:
        by_theme.setdefault(c2t.get(c["id"]), []).append(c["id"])
    for tid in [t["id"] for t in ts] + [None]:
        if tid not in by_theme:
            continue
        out.append(f"<h3>{e(s['themes'][tid]['label']) if tid else 'Unassigned'}</h3>")
        out += [_concept_html(concept_card(project, s, cid, max_quotes)) for cid in by_theme[tid]]

    # matrix
    docs, rows = concept_informant_matrix(project, s)
    out.append("<h2>Concept × informant</h2><p class='muted small'>Quotes per informant. Shows whether a concept rests on many voices or a few.</p>")
    out.append("<div class=wrap><table class=mx><tr><th>Concept</th>" + "".join(f"<th>{e(d)}</th>" for d in docs) + "</tr>")
    for r in sorted(rows, key=lambda r: -sum(1 for v in r["counts"].values() if v)):
        cells = "".join(f"<td class=h{min(v, 3)}>{v or ''}</td>" if v else "<td></td>" for v in (r["counts"][d] for d in docs))
        out.append(f"<tr><td class=l>{e(r['id'])} {e(r['label'])}</td>{cells}</tr>")
    out.append("</table></div>")

    # memos
    if s["memos"]:
        out.append("<h2>Memos</h2>")
        for mm in s["memos"].values():
            out.append(f"<details><summary>{e(mm['id'])} · {e(mm['author'])} · {e(mm['text'][:90])}</summary>"
                       f"<p>{e(mm['text'])}</p><p class='muted small'>links: {e(', '.join(mm['links']))}</p></details>")

    # decisions
    decs = run.decisions()
    out.append(f"<h2>Decision log</h2><details><summary>{len(decs)} decisions</summary><div class=wrap><table>"
               "<tr><th>#</th><th>stage</th><th>actor</th><th>op</th><th>in → out</th><th>reason</th></tr>")
    for d in decs:
        label = d.get("detail", {}).get("label") or d.get("detail", {}).get("new") or ""
        out.append(f"<tr><td>{e(d['id'])}</td><td>{e(str(d.get('stage') or ''))}</td><td>{e(d['actor'])}</td>"
                   f"<td>{e(d['op'])} {e(d['target'])}</td><td class=small>{e(', '.join(d['inputs'][:8]))}"
                   f"{' …' if len(d['inputs']) > 8 else ''} → {e(', '.join(d['outputs']))} <b>{e(label)}</b></td><td>{e(d['reason'])}</td></tr>")
    out.append("</table></div></details>")
    return _page(f"Run {run_id}", "\n".join(out))


def compare_report(project: Project, a_id: str, b_id: str) -> str:
    r = compare_runs(project, a_id, b_id)
    na, nb, labels = r["names"]["a"], r["names"]["b"], r["element_labels"]

    def el(x: str) -> str:
        return f"{e(x)} {e(labels.get(x, ''))}" if r["elements"] == "concept" else e(labels.get(x, x))

    out = [f"<h1>{e(a_id)} vs {e(b_id)}</h1>",
           f"<p class=muted>Compared by {'shared 1st-order concepts' if r['elements'] == 'concept' else 'quoted text spans'}. "
           "Labels are ignored; only what is grouped together counts.</p>"
           + (f"<p class='muted small'>Left out because they were merged, split or dropped in one run: {e(', '.join(r['excluded_concepts']))}</p>"
              if r["excluded_concepts"] else ""),
           "<h2>Agreement</h2><table><tr><th>Level</th><th>Rand</th><th>Adjusted Rand</th><th>NMI</th><th>Elements</th><th>Groups A / B</th></tr>"]
    for lvl, sc in r["levels"].items():
        fmt = lambda v: "–" if v is None else f"{v:.3f}"
        out.append(f"<tr><td>{lvl}</td><td class=score>{fmt(sc['rand'])}</td><td class=score>{fmt(sc['ari'])}</td>"
                   f"<td class=score>{fmt(sc['nmi'])}</td><td>{sc['n']}</td><td>{sc['groups_a']} / {sc['groups_b']}</td></tr>")
    out.append("</table>")
    for key, title in (("theme_matches", "2nd-order themes"), ("dimension_matches", "Aggregate dimensions")):
        rows = r[key]
        same = [x for x in rows if x["jaccard"] == 1.0]
        diff = [x for x in rows if x["jaccard"] < 1.0]
        out.append(f"<h2>{title}</h2><p class=muted>{len(same)} identical groupings (collapsed), {len(diff)} that differ.</p>")
        for x in diff:
            out.append(f"<details open><summary><b>{e(na.get(x['a'], x['a']))}</b> ↔ <b>{e(nb.get(x['b'], str(x['b'])))}</b>"
                       f" <span class=pill>overlap {x['jaccard']:.0%}</span></summary>"
                       f"<p class=small><b>Only in {e(a_id)}:</b> {'; '.join(el(i) for i in x['only_a']) or '–'}</p>"
                       f"<p class=small><b>Only in {e(b_id)}:</b> {'; '.join(el(i) for i in x['only_b']) or '–'}</p>"
                       f"<p class='small muted'>Shared: {len(x['both'])}</p></details>")
        if same:
            out.append("<details><summary>Identical groupings</summary><ul>"
                       + "".join(f"<li>{e(na.get(x['a'], x['a']))} = {e(nb.get(x['b'], str(x['b'])))}</li>" for x in same)
                       + "</ul></details>")
    return _page(f"{a_id} vs {b_id}", "\n".join(out))
