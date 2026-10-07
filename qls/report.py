"""Reports a reader can check: data structure, claim -> quote table, decisions, memos, open expectations.

    qls report RUN [--format md|html] [-o FILE]
    qls diff RUN_A RUN_B

The data structure is read from the recipe: types linked by `groups` form the
hierarchy (Gioia: Dimension -> Theme -> Concept) and `evidenced_by` links carry
the quotes. Every quote is printed from its source offsets, so the report shows
exactly the words the store verified.
"""

from __future__ import annotations

import html
from collections import Counter

from .analysis import compare
from .store import Store
from .tools import Session

DECISION_KINDS = {"supersede", "unlink", "update", "human", "checkpoint", "resume"}
MEMO_FIRST = ["uncertainty", "negative_case", "surprise", "method"]


def label(o: dict) -> str:
    f = o["fields"]
    return next((str(f[k]) for k in ("label", "in_vivo", "term", "canonical", "text") if f.get(k)), o["id"])


def hierarchy(recipe) -> list[str]:
    """Types chained by `groups` links, top first."""
    child = {t.name: t.links["groups"].to[0] for t in recipe.types.values() if "groups" in t.links}
    targets = set(child.values())
    top = [t for t in child if t not in targets]
    if not top:
        return []
    chain = [top[0]]
    while chain[-1] in child and child[chain[-1]] not in chain:
        chain.append(child[chain[-1]])
    return chain


def _quote(store: Store, q: dict, cache: dict) -> dict:
    f = q["fields"]
    key = (f["source"], f.get("version"))
    if key not in cache:
        cache[key] = store.source(f["source"], f.get("version"))["text"]
    return {"id": q["id"], "source": f["source"], "unit": f.get("unit"), "start": f["start"], "end": f["end"],
            "text": cache[key][f["start"]:f["end"]]}


def build(store: Store, run: str) -> dict:
    r = store.run(run)
    session = Session(store, run, "reviewer:report")
    recipe = session.recipe
    levels = hierarchy(recipe)
    objs = {o["id"]: o for o in store.objects(run)}
    cache: dict = {}

    def node(oid: str, reason: str = "", depth: int = 0) -> dict:
        o = objs[oid]
        n = {"id": oid, "type": o["type"], "label": label(o), "fields": o["fields"], "reason": reason}
        if depth + 1 < len(levels):
            n["children"] = [node(l["dst"], l["reason"], depth + 1) for l in store.links(run, src=oid, rel="groups") if l["dst"] in objs]
        else:
            n["quotes"] = [{**_quote(store, objs[l["dst"]], cache), "reason": l["reason"]}
                           for l in store.links(run, src=oid, rel="evidenced_by") if l["dst"] in objs]
        return n

    tree, unplaced = [], {}
    for i, t in enumerate(levels):
        placed = {l["dst"] for p in levels[:i] for o in store.objects(run, p) for l in store.links(run, src=o["id"], rel="groups")}
        roots = [o["id"] for o in store.objects(run, t) if i == 0 or o["id"] not in placed]
        if i == 0:
            tree = [node(oid) for oid in roots]
        elif roots:
            unplaced[t] = [node(oid, depth=i) for oid in roots]

    events = store.effective_events(run)
    decisions = [e for e in events if e["kind"] in DECISION_KINDS or (e["kind"] == "note" and "agent_session" not in e["payload"])]
    sessions = [e["payload"]["agent_session"] for e in events if e["kind"] == "note" and "agent_session" in e["payload"]]
    memos = [o for o in objs.values() if o["type"] == "Memo"]
    memos.sort(key=lambda m: (MEMO_FIRST.index(m["fields"].get("kind")) if m["fields"].get("kind") in MEMO_FIRST else 99, m["created_seq"]))
    for m in memos:
        m["about"] = [l["dst"] for l in store.links(run, src=m["id"], rel="about")]
    return {
        "run": run, "recipe": r["recipe"], "recipe_hash": r["recipe_hash"], "status": r["status"], "parent": r["parent"],
        "fork_at": r["fork_at"], "config": r["config"], "state_hash": store.state_hash(run), "levels": levels,
        "counts": dict(Counter(o["type"] for o in objs.values())), "events": len(events), "actors": dict(Counter(e["actor"] for e in events)),
        "tree": tree, "unplaced": unplaced, "decisions": decisions, "sessions": sessions, "memos": memos,
        "open": session.check(),
    }


# -- markdown ------------------------------------------------------------------

def _cell(s) -> str:
    return " ".join(str(s or "").split()).replace("|", "\\|")


def _leaves(n: dict):
    if "quotes" in n:
        yield n
    for c in n.get("children", []):
        yield from _leaves(c)


def to_markdown(rep: dict) -> str:
    L = rep["levels"]
    out = [f"# Run `{rep['run']}`", "",
           f"Recipe `{rep['recipe']}` ({rep['recipe_hash'][:19]}…), status **{rep['status']}**, {rep['events']} events, "
           f"state `{rep['state_hash'][:19]}…`" + (f", forked from `{rep['parent']}` at #{rep['fork_at']}" if rep["parent"] else "") + ".", "",
           "Objects: " + ", ".join(f"{k} {v}" for k, v in sorted(rep["counts"].items())) + ".  ",
           "Actors: " + ", ".join(f"`{k}` {v}" for k, v in rep["actors"].items()) + ".", ""]
    if L:
        out += ["## Data structure", "", "| " + " | ".join(reversed(L)) + " |", "|" + "---|" * len(L)]
        for top in rep["tree"]:
            rows = _rows(top, len(L))
            out += ["| " + " | ".join(_cell(c) for c in reversed(row)) + " |" for row in rows]
        for t, nodes in rep["unplaced"].items():
            out += ["", f"Not yet placed ({t}): " + ", ".join(f"{n['id']} {n['label']}" for n in nodes)]
        out += ["", "## Claims and evidence", "", "| Concept | Quote | Source | Why this quote | Words |", "|---|---|---|---|---|"]
        leaves = [l for top in rep["tree"] for l in _leaves(top)] + [l for ns in rep["unplaced"].values() for n in ns for l in _leaves(n)]
        for c in leaves:
            for q in c["quotes"]:
                out.append(f"| {c['id']} {_cell(c['label'])} | {q['id']} | {q['source']} {q['start']}–{q['end']} | {_cell(q['reason'])} | “{_cell(q['text'])}” |")
    out += ["", "## Memos", ""]
    for m in rep["memos"]:
        out.append(f"- **{m['id']}** ({m['fields'].get('kind') or m['fields'].get('level')}) about {', '.join(m['about'])}: {_cell(m['fields'].get('text'))}")
    out += ["", "## Decisions", "", "| # | Actor | Kind | What | Reason |", "|---|---|---|---|---|"]
    for e in rep["decisions"]:
        out.append(f"| {e['seq']} | {e['actor']} | {e['kind']} | {_cell(_what(e))} | {_cell(e['reason'])} |")
    if rep["sessions"]:
        out += ["", "## Agent sessions", ""] + [f"- `{s['actor']}` via {s['harness']} {s['provider']}/{s['model']}: exit {s['exit']}, "
                                                f"{s['events_written']} events, {s['elapsed_s']} s, prompt {s['prompt_hash'][:19]}…" for s in rep["sessions"]]
    out += ["", f"## Open expectations ({len(rep['open'])})", ""] + [f"- {p['item']}: {p['text']}" for p in rep["open"][:50]]
    return "\n".join(out) + "\n"


def _rows(n: dict, depth: int) -> list[list[str]]:
    """Rows top-down; a parent label is printed only on its first row."""
    me = f"{n['id']} {n['label']}"
    kids = n.get("children")
    if kids is None:
        return [[me] + [""] * (depth - 1)]
    rows = [r for c in kids for r in _rows(c, depth - 1)] or [[""] * (depth - 1)]
    return [[me if i == 0 else ""] + r for i, r in enumerate(rows)]


def _what(e: dict) -> str:
    p = e["payload"]
    k = e["kind"]
    if k == "supersede":
        return f"{', '.join(p['old'])} → {', '.join(p.get('new') or []) or p.get('status', 'superseded')}"
    if k == "unlink":
        return f"{p['src']} −{p['rel']}→ {p['dst']}"
    if k == "update":
        return f"{p['id']}: {', '.join(p['fields'])}"
    if k == "human":
        return f"{p['action']} {p.get('target') or ''}".strip() + (f": {p['text']}" if p.get("text") and p["text"] != e["reason"] else "")
    if k == "checkpoint":
        return p.get("summary", "")
    return ""


# -- html: the Gioia figure as nested columns -----------------------------------

CSS = """
:root{--bg:#fff;--fg:#1d1d1f;--mut:#6b6b70;--line:#d8d8dc;--c1:#eef4ff;--c2:#f3f0ff;--c3:#fff4e8}
@media (prefers-color-scheme:dark){:root{--bg:#141416;--fg:#ececf0;--mut:#9a9aa2;--line:#33333a;--c1:#1b2433;--c2:#231d33;--c3:#33281b}}
body{background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif;max-width:1200px;margin:0 auto;padding:16px}
h1,h2{font-weight:600}.mut{color:var(--mut)}table{border-collapse:collapse;width:100%}td,th{border-top:1px solid var(--line);padding:6px;vertical-align:top;text-align:left}
.ds{display:grid;gap:8px}.row{display:grid;grid-template-columns:2.4fr 1fr;gap:8px;border-top:1px solid var(--line);padding-top:8px}
.box{border-radius:8px;padding:8px}.l0{background:var(--c3)}.l1{background:var(--c2)}.l2{background:var(--c1)}
.sub{display:grid;gap:8px}.pair{display:grid;grid-template-columns:1.4fr 1fr;gap:8px}
details summary{cursor:pointer}blockquote{margin:4px 0 4px 8px;padding-left:8px;border-left:3px solid var(--line)}
@media (max-width:700px){.row,.pair{grid-template-columns:1fr}}
"""


def to_html(rep: dict) -> str:
    e = html.escape
    md_head = f"<p class=mut>Recipe <code>{e(rep['recipe'])}</code>, status <b>{e(rep['status'])}</b>, {rep['events']} events, state <code>{e(rep['state_hash'][:19])}…</code></p>"

    def leaf(n):
        qs = "".join(f"<blockquote>“{e(q['text'])}”<br><span class=mut>{e(q['id'])} · {e(q['source'])} {q['start']}–{q['end']} · {e(q['reason'])}</span></blockquote>" for q in n["quotes"])
        return f"<details class='box l2'><summary><b>{e(n['id'])}</b> {e(n['label'])}</summary>{qs}</details>"

    def mid(n):
        kids = "".join(leaf(c) for c in n.get("children", []))
        return f"<div class=pair><div class=sub>{kids}</div><div class='box l1'><b>{e(n['id'])}</b> {e(n['label'])}<div class=mut>{e(str(n['fields'].get('definition', '')))}</div></div></div>"

    rows = []
    if len(rep["levels"]) == 3:
        for d in rep["tree"]:
            rows.append(f"<div class=row><div class=sub>{''.join(mid(t) for t in d['children'])}</div><div class='box l0'><b>{e(d['id'])}</b> {e(d['label'])}</div></div>")
        for t in rep["unplaced"].get(rep["levels"][1], []):
            rows.append(f"<div class=row><div class=sub>{mid(t)}</div><div class='box mut'>(no {e(rep['levels'][0].lower())} yet)</div></div>")
    body = [f"<h1>Run {e(rep['run'])}</h1>", md_head]
    if rows:
        heads = " · ".join(reversed(rep["levels"]))
        body += [f"<h2>Data structure</h2><p class=mut>{e(heads)}, left to right; open a concept to see its quotes.</p><div class=ds>{''.join(rows)}</div>"]
        for t, nodes in rep["unplaced"].items():
            body.append(f"<p class=mut>Not yet placed ({e(t)}): {e(', '.join(n['id'] + ' ' + n['label'] for n in nodes))}</p>")
    body.append("<h2>Memos</h2><ul>" + "".join(f"<li><b>{e(m['id'])}</b> <span class=mut>{e(str(m['fields'].get('kind') or m['fields'].get('level')))} · {e(', '.join(m['about']))}</span><br>{e(str(m['fields'].get('text', '')))}</li>" for m in rep["memos"]) + "</ul>")
    body.append("<h2>Decisions</h2><table><tr><th>#</th><th>Actor</th><th>Kind</th><th>What</th><th>Reason</th></tr>"
                + "".join(f"<tr><td>{d['seq']}</td><td>{e(d['actor'])}</td><td>{e(d['kind'])}</td><td>{e(_what(d))}</td><td>{e(d['reason'])}</td></tr>" for d in rep["decisions"]) + "</table>")
    body.append(f"<h2>Open expectations ({len(rep['open'])})</h2><ul>" + "".join(f"<li>{e(p['item'])}: {e(p['text'])}</li>" for p in rep["open"][:50]) + "</ul>")
    return (f"<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>Run {e(rep['run'])}</title><style>{CSS}</style></head><body>{''.join(body)}</body></html>")


# -- diff ------------------------------------------------------------------------

def diff(store: Store, a: str, b: str) -> dict:
    """What differs between two runs: objects by id (shared after a fork), links, and agreement on segments."""
    oa = {o["id"]: o for o in store.objects(a)}
    ob = {o["id"]: o for o in store.objects(b)}
    la = {(l["src"], l["rel"], l["dst"]) for l in store.links(a)}
    lb = {(l["src"], l["rel"], l["dst"]) for l in store.links(b)}
    shared = oa.keys() & ob.keys()
    roots = {store.lineage(a)[0][0], store.lineage(b)[0][0]}
    return {
        "related": len(roots) == 1,  # ids only mean the same object when the runs share history
        "only_a": [f"{i} {oa[i]['type']} {label(oa[i])}" for i in sorted(oa.keys() - ob.keys())],
        "only_b": [f"{i} {ob[i]['type']} {label(ob[i])}" for i in sorted(ob.keys() - oa.keys())],
        "changed": [{"id": i, "a": oa[i]["fields"], "b": ob[i]["fields"]} for i in sorted(shared) if oa[i]["fields"] != ob[i]["fields"]],
        "links_only_a": sorted(" ".join(x) for x in la - lb), "links_only_b": sorted(" ".join(x) for x in lb - la),
        "agreement": compare(store, a, b)["levels"],
    }
