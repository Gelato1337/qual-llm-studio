"""Fixed (non-agentic) stages 3-4, and the one-shot long-context baseline.

`group` is the experiment-friendly counterpart of an agent analyst. Two
switches isolate the context question:

  view    what the model sees when grouping
          labels  concept labels only (as in the AMCIS 2025 pipeline)
          cards   + descriptions, informant counts, example quotes with questions
          memos   + coding memos (carried through merges) and analytic memos
  verify  return to data after each abstraction: every theme and dimension is
          checked against a context pack rebuilt from quotes and memos, and
          relabelled or trimmed where the data disagrees

`oneshot` gives the whole corpus to one long-context call and asks for the
full structure. Its distance to the staged runs is the cost of fragmentation.

All changes go through Ops, so every grouping decision is logged.
"""

from __future__ import annotations

import html

from .coding import concepts_schema, llm_config, load_prompt, waiting, _transcript_block, MEMO_FIELDS, MEMO_INSTRUCTIONS
from .grounding import ground
from .llm import LLM, PendingAnswers, make_llm
from .ops import Ops
from .project import Project, QlsError, Run, sha256_text
from .runs import fork
from .views import _spread, active, concept_memos, context_pack, memos_about

VIEWS = ("labels", "cards", "memos")

GROUPING_SCHEMA = {
    "type": "object",
    "properties": {
        "themes": {"type": "array", "items": {"type": "object", "properties": {
            "key": {"type": "string"}, "label": {"type": "string"}, "definition": {"type": "string"},
            "concept_ids": {"type": "array", "items": {"type": "string"}}, "reason": {"type": "string"}},
            "required": ["key", "label", "definition", "concept_ids", "reason"], "additionalProperties": False}},
        "dimensions": {"type": "array", "items": {"type": "object", "properties": {
            "label": {"type": "string"}, "definition": {"type": "string"},
            "theme_keys": {"type": "array", "items": {"type": "string"}}, "reason": {"type": "string"}},
            "required": ["label", "definition", "theme_keys", "reason"], "additionalProperties": False}},
        "notes": {"type": "string"},
    },
    "required": ["themes", "dimensions", "notes"],
    "additionalProperties": False,
}

VERIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "label_ok": {"type": "boolean"},
        "label": {"type": "string"},
        "definition": {"type": "string"},
        "misfits": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"}, "why": {"type": "string"}, "move_to": {"type": "string"}},
            "required": ["id", "why", "move_to"], "additionalProperties": False}},
        "reason": {"type": "string"},
    },
    "required": ["label_ok", "label", "definition", "misfits", "reason"],
    "additionalProperties": False,
}


def concept_listing(project: Project, state: dict, view: str, quotes: int = 2) -> str:
    if view not in VIEWS:
        raise QlsError(f"view must be one of {', '.join(VIEWS)}")
    out = []
    for c in active(state, "concepts"):
        if view == "labels":
            out.append(f"- {c['id']}: {c['label']}")
            continue
        docs = sorted({state["quotes"][q]["doc"] for q in c["quotes"]})
        lines = [f'<concept id="{c["id"]}" label="{html.escape(c["label"])}" informants="{len(docs)}">', c["description"]]
        for qid in _spread(state, c["quotes"], quotes):
            q = state["quotes"][qid]
            seg = project.segment(q["segment"])
            qq = f" (asked: {seg['question'][:160]})" if seg.get("question") else ""
            lines.append(f'quote{qq}: "{q["text"]}"')
        if view == "memos":
            for m in concept_memos(state, c["id"]):
                lines.append("coding memo: " + "; ".join(f"{k}: {v}" for k, v in m.items() if k in MEMO_FIELDS and v))
            for m in memos_about(state, c["id"]):
                lines.append(f"memo ({m.get('kind', 'analytic')}): {m['text']}")
        lines.append("</concept>")
        out.append("\n".join(lines))
    return "\n".join(out)


def _alternatives(state: dict, kind: str, exclude: str) -> str:
    items = active(state, "themes" if kind == "theme" else "dimensions")
    return "\n".join(f"- {x['id']}: {x['label']}: {x['definition']}" for x in items if x["id"] != exclude) or "(none)"


def group(project: Project, source: str, into: str | None = None, view: str = "memos", verify: bool = True,
          llm: LLM | None = None, log=print) -> Run:
    llm = llm or make_llm(llm_config(project))
    label_language = project.cfg("project", "label_language", "English")
    system = load_prompt("grouping").replace("{label_language}", label_language)
    vsystem = load_prompt("verify").replace("{label_language}", label_language)
    run = fork(project, source, into or f"{source}-group-{view}{'-v' if verify else ''}", keep="concepts",
               kind="grouping", actor="pipeline", note=f"fixed grouping, view={view}, verify={verify}",
               replace_unfinished=True)
    ops = Ops(run, actor="pipeline", model=llm.cfg.get("model"), prompt_hash=sha256_text(system))
    calls, problems = [], {"unknown_concepts": [], "double_assigned": [], "unknown_theme_keys": []}

    # stages 3-4 in one call
    listing = concept_listing(project, ops.s, view)
    user = (f"{project.context_text()}\n\n<concepts view=\"{view}\">\n{listing}\n</concepts>\n\n"
            "Group these 1st-order concepts into 2nd-order themes and aggregate dimensions.")
    log(f"  grouping {len(active(ops.s, 'concepts'))} concepts (view={view}) ...")
    try:
        res = llm.json(system, user, GROUPING_SCHEMA, "gioia_grouping")
    except PendingAnswers as pa:
        raise waiting(run, pa.requests)
    calls.append({"step": "group", **res.meta})
    keys: dict[str, str] = {}
    with ops.batch():
        placed: set[str] = set()
        for t in res.data.get("themes", []):
            ids = []
            for cid in dict.fromkeys(t.get("concept_ids", [])):
                if ops.s["concepts"].get(cid, {}).get("status") != "active":
                    problems["unknown_concepts"].append(cid)
                elif cid in placed:
                    problems["double_assigned"].append(cid)  # first theme wins
                else:
                    ids.append(cid)
                    placed.add(cid)
            if not ids:
                continue
            keys[t["key"]] = ops.create_theme(t["label"], t.get("definition", ""), ids, t.get("reason") or "model grouping")
        for d in res.data.get("dimensions", []):
            tids = [keys[k] for k in d.get("theme_keys", []) if k in keys]
            problems["unknown_theme_keys"] += [k for k in d.get("theme_keys", []) if k not in keys]
            if tids:
                ops.create_dimension(d["label"], d.get("definition", ""), tids, d.get("reason") or "model grouping")
        if res.data.get("notes", "").strip():
            ops.add_memo(res.data["notes"], [], kind="summary")

    # return to data: every theme is checked against the same post-grouping state, then
    # every dimension against the state after theme checks (requests within a level are
    # independent, so external answerers can work through them in any order)
    changes = {"relabelled": 0, "moved": 0, "unassigned": 0}
    if verify:
        for kind in ("theme", "dimension"):
            items = active(ops.s, "themes" if kind == "theme" else "dimensions")
            results, pending = {}, []
            for item in items:
                pack = context_pack(project, ops.s, [item["id"]], max_chars=int(project.cfg("grouping", "verify_chars", 20000)))
                vuser = (f"{project.context_text()}\n\nYou are checking {kind} {item['id']}.\n\n{pack}\n\n"
                         f"Alternative places (for move_to):\n{_alternatives(ops.s, kind, item['id'])}")
                log(f"  verifying {kind} {item['id']} against the data ...")
                try:
                    results[item["id"]] = llm.json(vsystem, vuser, VERIFY_SCHEMA, f"{kind}_check")
                except PendingAnswers as pa:
                    pending += pa.requests
            if pending:
                raise waiting(run, pending)
            for item in items:
                v = results[item["id"]]
                calls.append({"step": f"verify {item['id']}", **v.meta})
                members = list(item["concepts"] if kind == "theme" else item["themes"])
                with ops.batch():
                    if not v.data.get("label_ok", True) and v.data.get("label", "").strip():
                        rename = ops.rename_theme if kind == "theme" else ops.rename_dimension
                        ops.with_evidence(members)
                        rename(item["id"], v.data["label"], v.data.get("definition") or None,
                               f"return to data: {v.data.get('reason') or 'label more specific than before'}")
                        changes["relabelled"] += 1
                    for mf in v.data.get("misfits", []):
                        mid, target = mf.get("id"), (mf.get("move_to") or "").strip()
                        current = item["concepts"] if kind == "theme" else item["themes"]
                        if mid not in members or mid not in current:
                            continue
                        why = f"return to data: {mf.get('why', '').strip() or 'does not fit'}"
                        ops.add_memo(f"{mid} does not fit {item['id']} ({item['label']}): {mf.get('why', '')}",
                                     [mid, item["id"]], kind="counter")
                        if kind == "theme":
                            if target in ops.s["themes"] and ops.s["themes"][target]["status"] == "active" and target != item["id"]:
                                ops.with_evidence([mid]).assign_concepts(target, [mid], why)
                                changes["moved"] += 1
                            else:
                                ops.with_evidence([mid]).unassign_concepts([mid], why)
                                changes["unassigned"] += 1
                        elif target in ops.s["dimensions"] and ops.s["dimensions"][target]["status"] == "active" and target != item["id"]:
                            ops.with_evidence([mid]).assign_themes(target, [mid], why)
                            changes["moved"] += 1

    run.update_manifest(stage=3, view=view, verify=verify, llm=llm.describe(), calls=calls, problems=problems,
                        verify_changes=changes, prompt_hashes={"group": sha256_text(system), "verify": sha256_text(vsystem)},
                        served_models=sorted({c.get("model_served") for c in calls if c.get("model_served")}), status="done")
    log(f"  {len(active(ops.s, 'themes'))} themes, {len(active(ops.s, 'dimensions'))} dimensions; verify: {changes}")
    return run


# ---------------------------------------------------------------------------
# One-shot long-context baseline
# ---------------------------------------------------------------------------

ONESHOT_PREAMBLE = """You will do a complete Gioia analysis in one pass: 1st-order concepts with verbatim quotes,
2nd-order themes, and aggregate dimensions, for all transcripts at once.

"""


def oneshot_schema(with_memo: bool) -> dict:
    import copy

    c = copy.deepcopy(concepts_schema(with_memo)["properties"]["concepts"])
    c["items"]["properties"]["key"] = {"type": "string"}
    c["items"]["required"].insert(0, "key")
    g = copy.deepcopy(GROUPING_SCHEMA)
    g["properties"]["themes"]["items"]["properties"]["concept_ids"]["description"] = "keys of the concepts above"
    return {"type": "object", "properties": {"concepts": c, **g["properties"]},
            "required": ["concepts", "themes", "dimensions", "notes"], "additionalProperties": False}


def oneshot(project: Project, run_id: str | None = None, llm: LLM | None = None, log=print) -> Run:
    cfg = llm_config(project)
    cfg["max_tokens"] = max(int(cfg.get("max_tokens", 64000)), int(project.cfg("oneshot", "max_tokens", 128000)))
    llm = llm or make_llm(cfg)
    label_language = project.cfg("project", "label_language", "English")
    with_memo = bool(project.cfg("coding", "concept_memos", True))
    system = ONESHOT_PREAMBLE + load_prompt("first_order").replace("{label_language}", label_language)
    if with_memo:
        system += MEMO_INSTRUCTIONS.replace("{fields}", " ".join(f"`{k}` ({v})" for k, v in MEMO_FIELDS.items()))
    system += "\n\n" + load_prompt("grouping").replace("{label_language}", label_language)
    system += "\n\nGive each concept a short `key` (e.g. k1, k2) and refer to concepts by key in themes."
    docs = project.docs()
    if not docs:
        raise QlsError("No documents ingested.")
    threshold = float(project.cfg("coding", "quote_threshold", 90))
    run = project.new_run(run_id, "oneshot", replace_unfinished=True, stage=1, llm=llm.describe(), prompt_hash=sha256_text(system),
                          docs=[d["id"] for d in docs], concept_memos=with_memo, status="running")
    user = project.context_text() + "\n\n" + "\n\n".join(_transcript_block(d, d["segments"]) for d in docs)
    user += "\n\nDo the full analysis."
    log(f"  one call over {len(docs)} transcripts ({len(user)} characters) ...")
    try:
        res = llm.json(system, user, oneshot_schema(with_memo), "oneshot")
    except PendingAnswers as pa:
        raise waiting(run, pa.requests)
    ops = Ops(run, actor="pipeline", model=llm.cfg.get("model"), prompt_hash=sha256_text(system))
    segs_by_doc = {d["id"]: d["segments"] for d in docs}
    all_segs = [s for d in docs for s in d["segments"]]
    keymap, stats = {}, {"concepts": 0, "quotes_ok": 0, "quotes_failed": 0, "concepts_without_quotes": 0}
    with ops.batch():
        for c in res.data.get("concepts", []):
            grounded = []
            for q in c.get("quotes", []):
                segs = segs_by_doc.get((q.get("segment_id") or "").split(":", 1)[0], all_segs)
                hit = ground(q.get("quote", ""), q.get("segment_id"), segs, threshold)
                if hit:
                    seg, sp = hit
                    grounded.append({"doc": seg["id"].split(":", 1)[0], "segment": seg["id"], "start": sp.start, "end": sp.end,
                                     "text": seg["text"][sp.start:sp.end], "score": sp.score, "method": sp.method})
                else:
                    stats["quotes_failed"] += 1
            if not grounded:
                stats["concepts_without_quotes"] += 1
                continue
            keymap[c.get("key", "")] = ops.add_concept(c["label"], c.get("description", ""), grounded,
                                                        reason="one-shot analysis", origin={"oneshot": True}, memo=c.get("memo"))
            stats["concepts"] += 1
            stats["quotes_ok"] += len(grounded)
        tkeys = {}
        for t in res.data.get("themes", []):
            ids = [keymap[k] for k in dict.fromkeys(t.get("concept_ids", [])) if k in keymap]
            ids = [i for i in ids if not any(i in x["concepts"] for x in ops.s["themes"].values())]
            if ids:
                tkeys[t["key"]] = ops.create_theme(t["label"], t.get("definition", ""), ids, t.get("reason") or "one-shot")
        for d in res.data.get("dimensions", []):
            tids = [tkeys[k] for k in d.get("theme_keys", []) if k in tkeys]
            if tids:
                ops.create_dimension(d["label"], d.get("definition", ""), tids, d.get("reason") or "one-shot")
        if res.data.get("notes", "").strip():
            ops.add_memo(res.data["notes"], [], kind="summary")
    run.update_manifest(status="done", call=res.meta, grounding=stats)
    log(f"  {stats['concepts']} concepts ({stats['quotes_failed']} quotes rejected), "
        f"{len(active(ops.s, 'themes'))} themes, {len(active(ops.s, 'dimensions'))} dimensions")
    return run
