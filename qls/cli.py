"""qls command line. Run `qls guide` for the agent-oriented reference."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path

from . import __version__
from .project import Project, QlsError


def _print(obj, as_json: bool) -> None:
    if as_json or not isinstance(obj, str):
        print(json.dumps(obj, ensure_ascii=False, indent=1))
    else:
        print(obj)


def _project(a) -> Project:
    return Project.find(a.project)


def _run_id(a, p: Project) -> str:
    rid = getattr(a, "run", None) or os.environ.get("QLS_RUN")
    if rid:
        return rid
    runs = p.run_ids()
    if len(runs) == 1:
        return runs[0]
    raise QlsError("Which run? Pass --run NAME or set QLS_RUN. Runs: " + (", ".join(runs) or "none"))


def _ops(a, p: Project):
    from .ops import Ops

    return Ops(p.run(_run_id(a, p)), actor=a.actor or os.environ.get("QLS_ACTOR") or "human")


# ---------------------------------------------------------------------------
# handlers
# ---------------------------------------------------------------------------


def cmd_init(a):
    p = Project.init(a.dir, a.name)
    print(f"Initialised {p.root}\nNext: edit context/*.md and qls.toml, then `qls ingest <transcripts>`.")


def cmd_ingest(a):
    from .ingest import ingest_file

    p = _project(a)
    if a.id and len(a.files) > 1:
        raise QlsError("--id only works with a single file")
    for f in a.files:
        d = ingest_file(p, f, doc_id=a.id, participant=a.participant, interviewer=a.interviewer)
        roles = ", ".join(f"{k}={v}" for k, v in d["speakers"].items()) or "no speaker labels (whole text = informant)"
        print(f"{d['id']}: {len(d['turns'])} turns, {len(d['segments'])} informant segments [{roles}] via {d['parser']}")


def cmd_docs(a):
    p = _project(a)
    rows = [{"id": d["id"], "participant": d.get("participant"), "turns": len(d["turns"]), "segments": len(d["segments"]),
             "speakers": d["speakers"]} for d in p.docs()]
    if a.json:
        return _print(rows, True)
    for r in rows:
        print(f"{r['id']:<16} {str(r['participant']):<16} {r['turns']:>4} turns {r['segments']:>4} segments  {r['speakers']}")


def cmd_doc_read(a):
    from .views import read_document

    r = read_document(_project(a), a.doc, a.start, a.limit)
    if a.json:
        return _print(r, True)
    print(f"{r['doc']} ({r['participant']}), turns {a.start}-{a.start + len(r['turns']) - 1} of {r['n_turns']}\n")
    for t in r["turns"]:
        segs = f" [{', '.join(t['segments'])}]" if t["segments"] else ""
        print(f"{t['id']} {t['speaker']} ({t['role']}){segs}:\n{t['text']}\n")


def cmd_search(a):
    from .views import search_corpus

    hits = search_corpus(_project(a), a.pattern, a.doc, a.limit, a.regex, not a.all_turns)
    if a.json:
        return _print(hits, True)
    for h in hits:
        print(f"{h['id']}: {h['snippet']}\n")
    print(f"({len(hits)} hits{', limit reached' if len(hits) >= a.limit else ''})")


def cmd_code(a):
    from .coding import code_corpus, llm_config
    from .llm import make_llm

    p = _project(a)
    llm = make_llm(llm_config(p, provider=a.provider, model=a.model))
    run = code_corpus(p, a.name, a.doc, a.passes, llm)
    print(f"Stage 1 done: run {run.id}. Next: qls consolidate {run.id} --into {run.id}-cons")


def cmd_consolidate(a):
    from .coding import consolidate, llm_config
    from .llm import make_llm

    p = _project(a)
    llm = None if a.no_model else make_llm(llm_config(p, provider=a.provider, model=a.model))
    run = consolidate(p, a.source, a.into, use_model=not a.no_model, llm=llm)
    print(f"Stage 2 done: run {run.id}. Next: qls fork {run.id} <name> --blind, or qls analyst --from {run.id}")


def cmd_runs(a):
    p = _project(a)
    rows = []
    for rid in p.run_ids():
        r = p.run(rid)
        m, s = r.manifest(), r.state()
        rows.append({"run": rid, "kind": m.get("kind"), "parent": m.get("parent"), "status": m.get("status"),
                     "concepts": sum(c["status"] == "active" for c in s["concepts"].values()),
                     "themes": sum(t["status"] == "active" for t in s["themes"].values()),
                     "dimensions": sum(d["status"] == "active" for d in s["dimensions"].values()),
                     "created": m.get("created")})
    if a.json:
        return _print(rows, True)
    for r in rows:
        print(f"{r['run']:<28} {str(r['kind']):<14} parent={str(r['parent']):<20} "
              f"{r['concepts']:>4}c {r['themes']:>3}t {r['dimensions']:>2}a  {r['status'] or ''}")


def cmd_fork(a):
    from .runs import fork

    p = _project(a)
    r = fork(p, a.source, a.new, keep="concepts" if a.blind else "all", actor=a.actor or os.environ.get("QLS_ACTOR") or "human",
             note=a.note or "")
    print(f"Created run {r.id} from {a.source}" + (" (concepts only)" if a.blind else ""))


def cmd_status(a):
    from .views import status

    p = _project(a)
    st = status(p, p.run(_run_id(a, p)))
    if a.json:
        return _print(st, True)
    print(f"run {st['run']} ({st['kind']}, parent {st['parent']}): {st['concepts']} concepts, {st['themes']} themes, "
          f"{st['dimensions']} dimensions, {st['memos']} memos, {st['decisions']} decisions")
    if st["unassigned_concepts"]:
        print(f"unassigned concepts ({len(st['unassigned_concepts'])}): {' '.join(st['unassigned_concepts'])}")
    if st["themes_without_dimension"]:
        print(f"themes without dimension: {' '.join(st['themes_without_dimension'])}")
    if st["empty_themes"]:
        print(f"empty themes: {' '.join(st['empty_themes'])}")
    if st["flagged_concepts"]:
        print("flagged: " + ", ".join(f"{k} ({'/'.join(v)})" for k, v in st["flagged_concepts"].items()))


def cmd_check(a):
    from .views import check

    p = _project(a)
    problems = check(p.run(_run_id(a, p)))
    if a.json:
        return _print(problems, True)
    print("\n".join(problems) if problems else "OK: nothing lost, no double assignments.")
    if problems:
        sys.exit(1)


def cmd_structure(a):
    from .views import structure_text

    p = _project(a)
    print(structure_text(p, p.run(_run_id(a, p)), not a.no_concepts))


def cmd_concepts(a):
    from .views import active, concept_card, membership

    p = _project(a)
    s = p.run(_run_id(a, p)).state()
    c2t, _ = membership(s)
    ids = [c["id"] for c in active(s, "concepts")]
    if a.unassigned:
        ids = [c for c in ids if c not in c2t]
    if a.theme:
        ids = [c for c in ids if c2t.get(c) == a.theme]
    cards = [concept_card(p, s, c, a.quotes if a.cards else 1, context=a.cards) for c in ids]
    if a.json:
        return _print(cards, True)
    for c in cards:
        flags = f"  !{','.join(c['flags'])}" if c["flags"] else ""
        print(f"{c['id']}: {c['label']}  [{len(c['informants'])} inf, {c['n_quotes']} q, theme {c['theme'] or '-'}]{flags}")
        if a.cards:
            print(f"    {c['description']}")
            for q in c["quotes"]:
                if q.get("question"):
                    print(f"    Q: {q['question'][:160]}")
                print(f"    > {q['text']}  ({q['id']}, {q['segment']})")
            print()
    print(f"({len(cards)} concepts)")


def cmd_concept_show(a):
    from .views import concept_card

    p = _project(a)
    card = concept_card(p, p.run(_run_id(a, p)).state(), a.id, max_quotes=a.quotes)
    if a.json:
        return _print(card, True)
    print(f"{card['id']}: {card['label']}  ({card['status']}{', theme ' + card['theme'] if card['theme'] else ''})")
    print(f"{card['description']}\ninformants: {', '.join(card['informants'])}; quotes: {card['n_quotes']}; "
          f"flags: {', '.join(card['flags']) or '-'}; informant words: {card['informant_language']}")
    for q in card["quotes"]:
        print(f"\n{q['id']} {q['segment']}" + (f"\n  Q: {q['question']}" if q.get("question") else ""))
        print(f"  > {q['text']}")


def cmd_concept_merge(a):
    cid = _ops(a, _project(a)).merge_concepts(a.ids, a.label, a.description or "", a.reason)
    print(f"merged {' '.join(a.ids)} -> {cid}")


def cmd_concept_split(a):
    parts = []
    for spec in a.part:
        if "|" not in spec:
            raise QlsError('--part must look like "label|q1,q2"')
        label, qs = spec.rsplit("|", 1)
        parts.append({"label": label, "quotes": [q.strip() for q in qs.split(",") if q.strip()]})
    new = _ops(a, _project(a)).split_concept(a.id, parts, a.reason)
    print(f"split {a.id} -> {' '.join(new)}")


def cmd_concept_rename(a):
    _ops(a, _project(a)).rename_concept(a.id, a.label, a.description, a.reason)
    print(f"renamed {a.id}")


def cmd_concept_drop(a):
    _ops(a, _project(a)).drop_concept(a.id, a.reason)
    print(f"dropped {a.id}")


def cmd_concept_restore(a):
    _ops(a, _project(a)).restore_concept(a.id, a.reason)
    print(f"restored {a.id}")


def cmd_theme_create(a):
    tid = _ops(a, _project(a)).create_theme(a.label, a.definition or "", a.concepts or [], a.reason)
    print(f"created {tid}")


def cmd_theme_assign(a):
    _ops(a, _project(a)).assign_concepts(a.theme, a.concepts, a.reason)
    print(f"assigned {' '.join(a.concepts)} -> {a.theme}")


def cmd_theme_unassign(a):
    _ops(a, _project(a)).unassign_concepts(a.concepts, a.reason)
    print(f"unassigned {' '.join(a.concepts)}")


def cmd_theme_rename(a):
    _ops(a, _project(a)).rename_theme(a.id, a.label, a.definition, a.reason)
    print(f"renamed {a.id}")


def cmd_theme_drop(a):
    _ops(a, _project(a)).drop_theme(a.id, a.reason)
    print(f"dropped {a.id}")


def cmd_dim_create(a):
    aid = _ops(a, _project(a)).create_dimension(a.label, a.definition or "", a.themes or [], a.reason)
    print(f"created {aid}")


def cmd_dim_assign(a):
    _ops(a, _project(a)).assign_themes(a.dim, a.themes, a.reason)
    print(f"assigned {' '.join(a.themes)} -> {a.dim}")


def cmd_dim_rename(a):
    _ops(a, _project(a)).rename_dimension(a.id, a.label, a.definition, a.reason)
    print(f"renamed {a.id}")


def cmd_dim_drop(a):
    _ops(a, _project(a)).drop_dimension(a.id, a.reason)
    print(f"dropped {a.id}")


def cmd_memo(a):
    mid = _ops(a, _project(a)).add_memo(a.text, a.link)
    print(f"memo {mid}")


def cmd_decisions(a):
    p = _project(a)
    decs = p.run(_run_id(a, p)).decisions()[-a.last:] if a.last else p.run(_run_id(a, p)).decisions()
    if a.json:
        return _print(decs, True)
    for d in decs:
        lab = d.get("detail", {}).get("label") or d.get("detail", {}).get("new") or ""
        print(f"{d['id']:>6} s{d.get('stage') or '-'} {d['actor']:<22} {d['op']:<8} {d['target']:<9} "
              f"{','.join(d['inputs'][:6])}{'…' if len(d['inputs']) > 6 else ''} -> {','.join(d['outputs'])} {lab} | {d['reason']}")


def cmd_compare(a):
    from .compare import compare_runs
    from .report import compare_report

    p = _project(a)
    if a.html:
        Path(a.html).write_text(compare_report(p, a.a, a.b), encoding="utf-8")
        print(f"wrote {a.html}")
    r = compare_runs(p, a.a, a.b)
    if a.json:
        return _print(r, True)
    print(f"{a.a} vs {a.b} (elements: {r['elements']})")
    for lvl, sc in r["levels"].items():
        print(f"  {lvl:<10} n={sc['n']:<5} Rand={sc['rand']}  ARI={sc['ari']}  NMI={sc['nmi']}  groups {sc['groups_a']}/{sc['groups_b']}")
    print("theme overlaps (A -> best B, Jaccard):")
    for m in r["theme_matches"]:
        print(f"  {r['names']['a'].get(m['a'], m['a'])} -> {r['names']['b'].get(m['b'], m['b'])}: {m['jaccard']}")


def cmd_consensus(a):
    from .compare import consensus

    r = consensus(_project(a), a.runs, a.level)
    if a.json:
        return _print(r, True)
    print(f"{len(a.runs)} runs, {r['n_concepts']} shared concepts, level={a.level}")
    for k, sc in r["pairwise"].items():
        print(f"  {k}: Rand={sc['rand']} ARI={sc['ari']} NMI={sc['nmi']}")
    print("least stable concepts (companion overlap across runs):")
    for cid, v in list(r["concept_stability"].items())[:15]:
        print(f"  {cid} {v:.2f}  {r['labels'].get(cid, '')}")


def cmd_report(a):
    from .report import run_report

    p = _project(a)
    rid = _run_id(a, p)
    out = Path(a.out) if a.out else p.root / "reports" / f"{rid}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(run_report(p, rid, a.quotes), encoding="utf-8")
    print(f"wrote {out}")


def cmd_export(a):
    from .views import active, concept_informant_matrix, membership

    p = _project(a)
    rid = _run_id(a, p)
    s = p.run(rid).state()
    c2t, t2a = membership(s)
    d = p.root / "exports" / rid
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "structure.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["concept_id", "concept", "description", "theme_id", "theme", "dimension_id", "dimension", "n_quotes", "informants"])
        for c in active(s, "concepts"):
            tid = c2t.get(c["id"])
            aid = t2a.get(tid) if tid else None
            docs = sorted({s["quotes"][q]["doc"] for q in c["quotes"]})
            w.writerow([c["id"], c["label"], c["description"], tid or "", s["themes"][tid]["label"] if tid else "",
                        aid or "", s["dimensions"][aid]["label"] if aid else "", len(c["quotes"]), ";".join(docs)])
    with open(d / "quotes.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["quote_id", "concept_id", "doc", "segment", "start", "end", "score", "text"])
        for c in active(s, "concepts"):
            for qid in c["quotes"]:
                q = s["quotes"][qid]
                w.writerow([qid, c["id"], q["doc"], q["segment"], q["start"], q["end"], q.get("score"), q["text"]])
    docs, rows = concept_informant_matrix(p, s)
    with open(d / "concept_informant.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["concept_id", "concept", *docs])
        for r in rows:
            w.writerow([r["id"], r["label"], *[r["counts"][x] for x in docs]])
    print(f"wrote {d}/structure.csv, quotes.csv, concept_informant.csv")


def cmd_analyst(a):
    from .analyst import run_pi

    p = _project(a)
    runs = run_pi(p, a.source, a.name, a.replicates, a.provider, a.model, a.thinking, a.pi, a.dry_run, a.timeout)
    print("runs: " + " ".join(r.id for r in runs))


def cmd_guide(a):
    from .coding import load_prompt

    print(load_prompt("guide"))


def cmd_mcp(a):
    from .mcp_server import main as mcp_main

    mcp_main(a.project)


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--project", help="project folder (default: search upwards / $QLS_PROJECT)")
    common.add_argument("--json", action="store_true", help="JSON output")
    runarg = argparse.ArgumentParser(add_help=False)
    runarg.add_argument("--run", help="run name (default: $QLS_RUN)")
    actor = argparse.ArgumentParser(add_help=False)
    actor.add_argument("--actor", help="who decides: human | agent:<name> (default: $QLS_ACTOR or human)")
    reason = argparse.ArgumentParser(add_help=False)
    reason.add_argument("--reason", required=True, help="one-line reason, logged")
    edit = [common, runarg, actor, reason]

    ap = argparse.ArgumentParser(prog="qls", description="Qual LLM Studio: visible, replayable qualitative analysis.")
    ap.add_argument("--version", action="version", version=f"qls {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, fn, parents=(common,), **kw):
        sp = sub.add_parser(name, parents=list(parents), **kw)
        sp.set_defaults(fn=fn)
        return sp

    sp = add("init", cmd_init, help="create a project folder")
    sp.add_argument("dir", nargs="?", default=".")
    sp.add_argument("--name")

    sp = add("ingest", cmd_ingest, help="add transcripts (txt/md/srt/vtt, or anything Docling reads)")
    sp.add_argument("files", nargs="+")
    sp.add_argument("--id")
    sp.add_argument("--participant")
    sp.add_argument("--interviewer", action="append", help="extra interviewer speaker label")

    add("docs", cmd_docs, help="list documents")

    doc = sub.add_parser("doc", help="read a document").add_subparsers(dest="sub", required=True)
    sp = doc.add_parser("read", parents=[common])
    sp.set_defaults(fn=cmd_doc_read)
    sp.add_argument("doc")
    sp.add_argument("--from", dest="start", type=int, default=1)
    sp.add_argument("--limit", type=int, default=40)

    sp = add("search", cmd_search, help="search the corpus")
    sp.add_argument("pattern")
    sp.add_argument("--doc", action="append")
    sp.add_argument("--regex", action="store_true")
    sp.add_argument("--all-turns", action="store_true", help="include interviewer turns")
    sp.add_argument("--limit", type=int, default=30)

    sp = add("code", cmd_code, help="stage 1: 1st-order coding with verified quotes")
    sp.add_argument("--run", dest="name", help="name for the new run")
    sp.add_argument("--doc", action="append")
    sp.add_argument("--passes", type=int)
    sp.add_argument("--provider")
    sp.add_argument("--model")

    sp = add("consolidate", cmd_consolidate, help="stage 2: merge duplicate concepts")
    sp.add_argument("source")
    sp.add_argument("--into", help="fork into a new run first (recommended)")
    sp.add_argument("--no-model", action="store_true", help="only merge identical labels")
    sp.add_argument("--provider")
    sp.add_argument("--model")

    add("runs", cmd_runs, help="list runs")

    sp = add("fork", cmd_fork, parents=(common, actor), help="copy a run")
    sp.add_argument("source")
    sp.add_argument("new")
    sp.add_argument("--blind", action="store_true", help="keep concepts only (independent grouping)")
    sp.add_argument("--note")

    add("status", cmd_status, parents=(common, runarg), help="summary of a run")
    add("check", cmd_check, parents=(common, runarg), help="integrity checks")
    sp = add("structure", cmd_structure, parents=(common, runarg), help="dimension > theme > concept tree")
    sp.add_argument("--no-concepts", action="store_true")

    sp = add("concepts", cmd_concepts, parents=(common, runarg), help="list concepts")
    sp.add_argument("--unassigned", action="store_true")
    sp.add_argument("--theme")
    sp.add_argument("--cards", action="store_true", help="include descriptions and quotes")
    sp.add_argument("--quotes", type=int, default=3)

    con = sub.add_parser("concept", help="inspect or change a concept").add_subparsers(dest="sub", required=True)
    sp = con.add_parser("show", parents=[common, runarg])
    sp.set_defaults(fn=cmd_concept_show)
    sp.add_argument("id")
    sp.add_argument("--quotes", type=int, default=20)
    sp = con.add_parser("merge", parents=edit)
    sp.set_defaults(fn=cmd_concept_merge)
    sp.add_argument("ids", nargs="+")
    sp.add_argument("--label", required=True)
    sp.add_argument("--description")
    sp = con.add_parser("split", parents=edit)
    sp.set_defaults(fn=cmd_concept_split)
    sp.add_argument("id")
    sp.add_argument("--part", action="append", required=True, help='"label|q1,q2" (repeat)')
    for name, fn in (("rename", cmd_concept_rename),):
        sp = con.add_parser(name, parents=edit)
        sp.set_defaults(fn=fn)
        sp.add_argument("id")
        sp.add_argument("--label")
        sp.add_argument("--description")
    for name, fn in (("drop", cmd_concept_drop), ("restore", cmd_concept_restore)):
        sp = con.add_parser(name, parents=edit)
        sp.set_defaults(fn=fn)
        sp.add_argument("id")

    th = sub.add_parser("theme", help="2nd-order themes").add_subparsers(dest="sub", required=True)
    sp = th.add_parser("create", parents=edit)
    sp.set_defaults(fn=cmd_theme_create)
    sp.add_argument("--label", required=True)
    sp.add_argument("--definition")
    sp.add_argument("--concepts", nargs="*")
    sp = th.add_parser("assign", parents=edit)
    sp.set_defaults(fn=cmd_theme_assign)
    sp.add_argument("theme")
    sp.add_argument("concepts", nargs="+")
    sp = th.add_parser("unassign", parents=edit)
    sp.set_defaults(fn=cmd_theme_unassign)
    sp.add_argument("concepts", nargs="+")
    sp = th.add_parser("rename", parents=edit)
    sp.set_defaults(fn=cmd_theme_rename)
    sp.add_argument("id")
    sp.add_argument("--label")
    sp.add_argument("--definition")
    sp = th.add_parser("drop", parents=edit)
    sp.set_defaults(fn=cmd_theme_drop)
    sp.add_argument("id")

    dm = sub.add_parser("dim", help="aggregate dimensions").add_subparsers(dest="sub", required=True)
    sp = dm.add_parser("create", parents=edit)
    sp.set_defaults(fn=cmd_dim_create)
    sp.add_argument("--label", required=True)
    sp.add_argument("--definition")
    sp.add_argument("--themes", nargs="*")
    sp = dm.add_parser("assign", parents=edit)
    sp.set_defaults(fn=cmd_dim_assign)
    sp.add_argument("dim")
    sp.add_argument("themes", nargs="+")
    sp = dm.add_parser("rename", parents=edit)
    sp.set_defaults(fn=cmd_dim_rename)
    sp.add_argument("id")
    sp.add_argument("--label")
    sp.add_argument("--definition")
    sp = dm.add_parser("drop", parents=edit)
    sp.set_defaults(fn=cmd_dim_drop)
    sp.add_argument("id")

    sp = add("memo", cmd_memo, parents=(common, runarg, actor), help="write a memo")
    sp.add_argument("text")
    sp.add_argument("--link", action="append", default=[])

    sp = add("decisions", cmd_decisions, parents=(common, runarg), help="show the decision log")
    sp.add_argument("--last", type=int)

    sp = add("compare", cmd_compare, help="compare two runs structurally")
    sp.add_argument("a")
    sp.add_argument("b")
    sp.add_argument("--html", help="also write an HTML diff report")

    sp = add("consensus", cmd_consensus, help="stability across several runs")
    sp.add_argument("runs", nargs="+")
    sp.add_argument("--level", choices=["theme", "dimension"], default="theme")

    sp = add("report", cmd_report, parents=(common, runarg), help="HTML report of a run")
    sp.add_argument("--out")
    sp.add_argument("--quotes", type=int, default=5)

    add("export", cmd_export, parents=(common, runarg), help="CSV tables of a run")

    sp = add("analyst", cmd_analyst, help="independent grouping runs by pi agents")
    sp.add_argument("--from", dest="source", required=True, help="coding/consolidation run to start from")
    sp.add_argument("--name")
    sp.add_argument("--replicates", type=int, default=1)
    sp.add_argument("--provider")
    sp.add_argument("--model")
    sp.add_argument("--thinking")
    sp.add_argument("--pi", help="path to the pi binary")
    sp.add_argument("--timeout", type=int, default=3600)
    sp.add_argument("--dry-run", action="store_true", help="prepare runs and print the command only")

    add("guide", cmd_guide, help="print the agent-oriented command reference")
    add("mcp", cmd_mcp, help="run the MCP server (stdio)")
    return ap


def main(argv: list[str] | None = None) -> None:
    ap = build_parser()
    a = ap.parse_args(argv)
    for k in ("run", "actor", "project"):
        if not hasattr(a, k):
            setattr(a, k, None)
    try:
        a.fn(a)
    except QlsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
