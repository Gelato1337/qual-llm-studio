"""qls: run CLI (outside the agent) and a shell transport for agent tools.

Researcher / experimenter:
    qls init DIR                      create a project
    qls ingest FILES...               add sources (txt/md/srt/vtt, or anything Docling reads)
    qls sources
    qls run new NAME --recipe gioia [--config cfg.yaml] [--no-board]
    qls run fork RUN --at SEQ --as NAME     branch from any event
    qls run replay RUN                rebuild the graph from the log and verify it is identical
    qls runs | status RUN | log RUN [--since N]
    qls review RUN                    what the agent did since the last checkpoint, uncertainty first
    qls respond RUN --approve | --reject ID --reason R | --edit ID --set k=v --reason R | --answer TEXT
    qls resume RUN
    qls compare RUN_A RUN_B
    qls recipes | recipe NAME | guide
    qls mcp --run RUN --actor agent:NAME       MCP server for a harness

Agents in a shell (pi, scripts):
    qls tool RUN NAME '{"json": "args"}'      actor from --actor or $QLS_ACTOR
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__
from .util import QlsError


def _p(a):
    from .project import Project

    return Project.find(a.project)


def _session(a, run: str, actor: str | None):
    from .tools import Session

    actor = actor or os.environ.get("QLS_ACTOR")
    if not actor:
        raise QlsError("Who is acting? Pass --actor (agent:NAME or human:NAME) or set QLS_ACTOR.")
    return Session(_p(a).store, run, actor, os.environ.get("QLS_MODEL"))


def _out(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=1) if not isinstance(obj, str) else obj)


def cmd_init(a):
    from .project import Project

    p = Project.init(a.dir, a.name)
    print(f"Initialised {p.root}. Next: edit context/*.md, then `qls ingest <files>`.")


def cmd_ingest(a):
    p = _p(a)
    if a.id and len(a.files) > 1:
        raise QlsError("--id works with one file")
    for f in a.files:
        r = p.ingest(f, a.id, a.participant, a.interviewer)
        roles = ", ".join(f"{k}={v}" for k, v in r["speakers"].items()) or "no speaker labels"
        print(f"{r['id']} v{r['version']}: {r['turns']} turns, {r['segments']} informant segments [{roles}] via {r['parser']}")


def cmd_sources(a):
    st = _p(a).store
    for sid in st.source_ids():
        s = st.source(sid)
        segs = sum(1 for u in s["units"] if u["kind"] == "segment")
        print(f"{sid:<20} v{s['version']}  {segs:>4} segments  {len(s['text']):>8} chars  {s['meta'].get('participant', '')}")


def cmd_run_new(a):
    import yaml

    from .recipe import load_recipe

    p = _p(a)
    config = yaml.safe_load(Path(a.config).read_text()) if a.config else {}
    config = config or {}
    recipe_name = a.recipe or config.get("recipe") or "gioia"
    rec = load_recipe(recipe_name)
    if Path(recipe_name).suffix in (".yaml", ".yml"):
        config["recipe_path"] = str(Path(recipe_name).resolve())
    if a.no_board:
        config["board"] = False
    config.setdefault("context_hash", p.context_hash())
    config.setdefault("context", p.context_files())
    p.store.create_run(a.name, rec.name, rec.hash, config)
    print(f"Run {a.name} created (recipe {rec.name} {rec.hash[:19]}).")


def cmd_run_fork(a):
    p = _p(a)
    src = p.store.run(a.run)
    if a.at is not None and not any(e["seq"] == a.at for e in p.store.effective_events(a.run)):
        raise QlsError(f"Event #{a.at} is not in the history of {a.run}")
    at = a.at if a.at is not None else max([e["seq"] for e in p.store.effective_events(a.run)] or [0])
    config = dict(src["config"], forked_from=a.run, fork_at=at, fork_note=a.note or "")
    p.store.create_run(a.name, src["recipe"], src["recipe_hash"], config, parent=a.run, fork_at=at)
    print(f"Run {a.name} forked from {a.run} at #{at}.")


def cmd_run_replay(a):
    st = _p(a).store
    before = st.state_hash(a.run)
    st.rebuild(a.run)
    after = st.state_hash(a.run)
    print(f"{a.run}: {len(st.effective_events(a.run))} events replayed; graph {'identical' if before == after else 'DIFFERENT'} ({after[:19]})")
    if before != after:
        sys.exit(1)


def cmd_runs(a):
    for r in _p(a).store.runs():
        fork = f" forked from {r['parent']}@#{r['fork_at']}" if r["parent"] else ""
        print(f"{r['id']:<24} {r['recipe']:<10} {r['status']:<8}{fork}")


def cmd_status(a):
    _out(_session(a, a.run, a.actor or "human").status())


def cmd_log(a):
    for e in _p(a).store.events(a.run, since=a.since):
        p = e["payload"]
        what = p.get("id") or p.get("src") or ",".join(p.get("old", [])) or p.get("action") or ""
        extra = f" {p.get('type', '')}" if e["kind"] == "create" else (f" {p.get('rel')} -> {p.get('dst')}" if "rel" in p else "")
        print(f"#{e['seq']:<5} {e['run']:<14} {e['actor']:<22} {e['kind']:<10} {what}{extra}  {e['reason'][:80]}")


def cmd_tool(a):
    from .tools import call

    args = json.loads(a.args) if a.args else {}
    _out(call(_session(a, a.run, a.actor), a.name, args))


def cmd_review(a):
    from .tools import Session

    st = _p(a).store
    s = Session(st, a.run, "human")
    r = st.run(a.run)
    evs = st.events(a.run)
    cps = [e for e in evs if e["kind"] == "checkpoint"]
    since = max([e["seq"] for e in evs if e["kind"] in ("resume",)] + [0])
    print(f"Run {a.run}: {r['status']}")
    if cps:
        cp = cps[-1]["payload"]
        print(f"\nCheckpoint #{cps[-1]['seq']} by {cps[-1]['actor']}\n  {cp['summary']}")
        for q in cp.get("questions", []):
            print(f"  ? {q}")
    new = [e for e in evs if e["seq"] > since and e["kind"] == "create"]
    memos = [e for e in new if e["payload"]["type"] == "Memo"]
    unc = [m for m in memos if m["payload"]["fields"].get("kind") == "uncertainty"]
    print(f"\nUncertainty memos ({len(unc)}), read these first:")
    for m in unc:
        about = ", ".join(l["to"] for l in m["payload"]["links"] if l["rel"] == "about")
        print(f"  {m['payload']['id']} about {about}: {m['payload']['fields']['text']}")
    print("\nCreated since last resume:")
    for e in new:
        p = e["payload"]
        if p["type"] in ("Memo", "Quote"):
            continue
        f = p["fields"]
        print(f"  #{e['seq']} {p['id']} {p['type']}: {f.get('in_vivo') or f.get('label') or f.get('text', '')[:80]}")
        for l in p["links"]:
            text = ""
            if l["to"].startswith("Q-"):
                q = st.obj(a.run, l["to"])
                text = f' "{s.quote_text(q)[:140]}"' if q else ""
            print(f"      {l['rel']} {l['to']}{text}  | {l['reason']}")
    other = [e for e in evs if e["seq"] > since and e["kind"] in ("link", "unlink", "update", "supersede")]
    if other:
        print("\nOther changes:")
        for e in other:
            print(f"  #{e['seq']} {e['kind']} {json.dumps(e['payload'], ensure_ascii=False)[:120]}  | {e['reason']}")
    open_ = s.check()
    if open_:
        print(f"\nOpen expectations ({len(open_)}):")
        for p in open_[:30]:
            print(f"  {p['expectation']}: {p['item']}")
    print(f"\nRespond: qls respond {a.run} --approve | --reject ID --reason R | --edit ID --set k=v --reason R | --answer TEXT;"
          f" then qls resume {a.run}")


def cmd_respond(a):
    s = _session(a, a.run, a.actor or os.environ.get("QLS_HUMAN") or "human")
    if a.approve:
        print(f"approved (#{s.approve(a.text or '')})")
    if a.answer:
        print(f"answer recorded (#{s.answer(a.answer)})")
    if a.reject:
        if not a.reason:
            raise QlsError("--reject needs --reason")
        _out(s.reject(a.reject, a.reason))
    if a.edit:
        if not a.reason or not a.set:
            raise QlsError("--edit needs --set field=value and --reason")
        fields = dict(kv.split("=", 1) for kv in a.set)
        _out(s.edit(a.edit, fields, a.reason))


def cmd_resume(a):
    s = _session(a, a.run, a.actor or os.environ.get("QLS_HUMAN") or "human")
    print(f"resumed (#{s.resume()})")


def cmd_compare(a):
    from .analysis import compare

    r = compare(_p(a).store, a.a, a.b)
    if a.json:
        return _out(r)
    print(f"{a.a} vs {a.b}, compared on informant segments")
    for lvl, sc in r["levels"].items():
        print(f"  {lvl:<10} n={sc['n']:<5} Rand={sc['rand']} ARI={sc['ari']} NMI={sc['nmi']} pairF1={sc['pair_f1']}"
              f"  groups {sc['groups_a']}/{sc['groups_b']}")


def cmd_recipes(a):
    from .recipe import builtin_recipes

    print("\n".join(builtin_recipes()))


def cmd_recipe(a):
    from .recipe import load_recipe

    _out(load_recipe(a.name).summary())


def cmd_guide(a):
    from importlib import resources

    print(resources.files("qls.agents").joinpath("AGENTS.md").read_text(encoding="utf-8"))


def cmd_mcp(a):
    from .server import main

    main(a.project, a.run, a.actor or os.environ.get("QLS_ACTOR"), a.model, a.readonly)


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--project", help="project folder (default: search upwards or $QLS_PROJECT)")
    common.add_argument("--json", action="store_true")
    ap = argparse.ArgumentParser(prog="qls", description="Executable qualitative methods: strict store, free agents.")
    ap.add_argument("--version", action="version", version=f"qls {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, fn, **kw):
        sp = sub.add_parser(name, parents=[common], **kw)
        sp.set_defaults(fn=fn)
        return sp

    sp = add("init", cmd_init, help="create a project")
    sp.add_argument("dir", nargs="?", default=".")
    sp.add_argument("--name")
    sp = add("ingest", cmd_ingest, help="add sources")
    sp.add_argument("files", nargs="+")
    sp.add_argument("--id")
    sp.add_argument("--participant")
    sp.add_argument("--interviewer", action="append")
    add("sources", cmd_sources, help="list sources")

    run = sub.add_parser("run", help="create, fork and replay runs").add_subparsers(dest="sub", required=True)
    sp = run.add_parser("new", parents=[common])
    sp.set_defaults(fn=cmd_run_new)
    sp.add_argument("name")
    sp.add_argument("--recipe")
    sp.add_argument("--config", help="YAML run config (model, order, context strategy, ...)")
    sp.add_argument("--no-board", action="store_true")
    sp = run.add_parser("fork", parents=[common])
    sp.set_defaults(fn=cmd_run_fork)
    sp.add_argument("run")
    sp.add_argument("--at", type=int, help="event number to branch after (default: latest)")
    sp.add_argument("--as", dest="name", required=True)
    sp.add_argument("--note")
    sp = run.add_parser("replay", parents=[common])
    sp.set_defaults(fn=cmd_run_replay)
    sp.add_argument("run")

    add("runs", cmd_runs, help="list runs")
    for name, fn in (("status", cmd_status), ("review", cmd_review), ("resume", cmd_resume)):
        sp = add(name, fn)
        sp.add_argument("run")
        sp.add_argument("--actor")
    sp = add("log", cmd_log, help="event log of a run (with inherited history)")
    sp.add_argument("run")
    sp.add_argument("--since", type=int, default=0)
    sp = add("respond", cmd_respond, help="researcher feedback at a checkpoint")
    sp.add_argument("run")
    sp.add_argument("--actor")
    sp.add_argument("--approve", action="store_true")
    sp.add_argument("--text")
    sp.add_argument("--reject")
    sp.add_argument("--edit")
    sp.add_argument("--set", action="append")
    sp.add_argument("--reason")
    sp.add_argument("--answer")
    sp = add("tool", cmd_tool, help="call an agent tool (shell transport)")
    sp.add_argument("run")
    sp.add_argument("name")
    sp.add_argument("args", nargs="?")
    sp.add_argument("--actor")
    sp = add("compare", cmd_compare, help="structural agreement between two runs")
    sp.add_argument("a")
    sp.add_argument("b")
    add("recipes", cmd_recipes, help="list built-in recipes")
    sp = add("recipe", cmd_recipe, help="show a recipe")
    sp.add_argument("name")
    add("guide", cmd_guide, help="print AGENTS.md (ways of working)")
    sp = add("mcp", cmd_mcp, help="MCP server (stdio) bound to a run and an actor")
    sp.add_argument("--run")
    sp.add_argument("--actor")
    sp.add_argument("--model")
    sp.add_argument("--readonly", action="store_true", help="read tools only (reviewers)")
    return ap


def main(argv: list[str] | None = None) -> None:
    a = build_parser().parse_args(argv)
    try:
        a.fn(a)
    except QlsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
