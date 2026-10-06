"""Fixed pipeline stages that call a model directly.

Stage 1  code_corpus   transcript -> 1st-order concepts with grounded quotes
Stage 2  consolidate   exact-label merges, then model-proposed merges

Both write through Ops, so every concept and merge is a decision record.
Grouping into themes and dimensions is left to an analyst (pi, Claude
Cowork, a human), see `qls analyst` and the plugin skills.
"""

from __future__ import annotations

import html
import re
from importlib import resources
from typing import Callable

from .grounding import ground
from .llm import LLM, LLMError, make_llm
from .ops import Ops
from .project import Project, QlsError, Run, sha256_text
from .runs import fork

Log = Callable[[str], None]

CONCEPTS_SCHEMA = {
    "type": "object",
    "properties": {
        "concepts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "description": {"type": "string"},
                    "quotes": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"segment_id": {"type": "string"}, "quote": {"type": "string"}},
                            "required": ["segment_id", "quote"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["label", "description", "quotes"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["concepts"],
    "additionalProperties": False,
}

MEMO_FIELDS = {
    "meaning_here": "What this point means for this informant, in their situation, in one or two sentences.",
    "not_this": "What it is not: the nearby point it could be confused with and how it differs.",
    "conditions": "When, for whom, or why it holds according to the informant; empty if they did not say.",
    "doubt": "What is surprising, ambiguous or uncertain about it; empty if nothing.",
}


def concepts_schema(with_memo: bool) -> dict:
    """The stage-1 output schema; with_memo adds the coding memo (the experiment switch)."""
    if not with_memo:
        return CONCEPTS_SCHEMA
    import copy

    sch = copy.deepcopy(CONCEPTS_SCHEMA)
    item = sch["properties"]["concepts"]["items"]
    item["properties"]["memo"] = {
        "type": "object",
        "properties": {k: {"type": "string", "description": v} for k, v in MEMO_FIELDS.items()},
        "required": list(MEMO_FIELDS),
        "additionalProperties": False,
    }
    item["required"].append("memo")
    return sch


MEMO_INSTRUCTIONS = """
## Coding memo

For every concept also write a short coding memo. Later stages will read it when they group
concepts, so write down what a human coder would keep in their head: {fields}
Stay with what the transcript shows; do not speculate about the informant's mood or motives.
Leave a field empty rather than filling it with generic text.
"""

MERGES_SCHEMA = {
    "type": "object",
    "properties": {
        "merges": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "ids": {"type": "array", "items": {"type": "string"}},
                    "label": {"type": "string"},
                    "description": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["ids", "label", "description", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["merges"],
    "additionalProperties": False,
}

_STOP = set(
    "the a an and or but of to in on for with is are was were it that this these those be been have has had do does "
    "what how why which who whom your you we they i me my our their there here about from as at by into than then so "
    "ja on ei se että kuin mitä miten mikä joka tai mutta myös sitten nyt".split()
)


def load_prompt(name: str) -> str:
    return resources.files("qls.prompts").joinpath(f"{name}.md").read_text(encoding="utf-8")


def llm_config(project: Project, **overrides) -> dict:
    cfg = dict(project.config.get("llm", {}))
    cfg.update({k: v for k, v in overrides.items() if v is not None})
    return cfg


def _content_words(s: str) -> set[str]:
    return {w for w in re.findall(r"[\wÅÄÖåäö]+", (s or "").lower()) if len(w) > 2 and w not in _STOP}


def _flags(label: str, segs: list[dict], quotes: list[dict]) -> list[str]:
    flags = []
    lw = _content_words(label)
    qw = set().union(*[_content_words(s.get("question") or "") for s in segs]) if segs else set()
    if lw and qw and len(lw & qw) / len(lw) >= 0.6:
        flags.append("echoes_question")  # AMCIS challenge #2: guide contamination
    if all(len(q["text"].split()) < 8 for q in quotes):
        flags.append("short_quote")  # AMCIS challenge #5: quote too thin to show the concept
    return flags


def _transcript_block(doc: dict, segments: list[dict]) -> str:
    lines = [f'<transcript doc="{doc["id"]}" participant="{html.escape(str(doc.get("participant", "")))}">']
    for s in segments:
        q = f' question="{html.escape(s["question"][:400])}"' if s.get("question") else ""
        lines.append(f'<segment id="{s["id"]}"{q}>\n{s["text"]}\n</segment>')
    lines.append("</transcript>")
    return "\n".join(lines)


def _chunks(segments: list[dict], max_chars: int) -> list[list[dict]]:
    out, cur, size = [], [], 0
    for s in segments:
        if cur and size + len(s["text"]) > max_chars:
            out.append(cur)
            cur, size = [], 0
        cur.append(s)
        size += len(s["text"])
    if cur:
        out.append(cur)
    return out


# ---------------------------------------------------------------------------
# Stage 1
# ---------------------------------------------------------------------------


def code_corpus(project: Project, run_id: str | None = None, docs: list[str] | None = None,
                passes: int | None = None, llm: LLM | None = None, log: Log = print) -> Run:
    llm = llm or make_llm(llm_config(project))
    passes = int(passes or project.cfg("coding", "passes", 1))
    threshold = float(project.cfg("coding", "quote_threshold", 90))
    retries = int(project.cfg("coding", "max_retries", 2))
    max_chars = int(project.cfg("coding", "max_chars_per_call", 150000))
    label_language = project.cfg("project", "label_language", "English")

    with_memo = bool(project.cfg("coding", "concept_memos", True))
    system = load_prompt("first_order").replace("{label_language}", label_language)
    if with_memo:
        fields = " ".join(f"`{k}` ({v})" for k, v in MEMO_FIELDS.items())
        system += MEMO_INSTRUCTIONS.replace("{fields}", fields)
    schema = concepts_schema(with_memo)
    context = project.context_text()
    prompt_hash = sha256_text(system)
    doc_ids = docs or project.doc_ids()
    if not doc_ids:
        raise QlsError("No documents ingested. Run `qls ingest <files>` first.")

    run = project.new_run(run_id, "coding", stage=1, llm=llm.describe(), prompt_hash=prompt_hash,
                          passes=passes, quote_threshold=threshold, docs=doc_ids, concept_memos=with_memo, status="running")
    model = llm.cfg.get("model")
    ops = Ops(run, actor="pipeline", model=model, prompt_hash=prompt_hash)
    calls, stats = [], {"concepts": 0, "quotes_ok": 0, "quotes_failed": 0, "exact": 0, "normalized": 0, "fuzzy": 0, "recovered_on_retry": 0}
    failures = []

    for doc_id in doc_ids:
        doc = project.doc(doc_id)
        for p in range(1, passes + 1):
            for ci, segs in enumerate(_chunks(doc["segments"], max_chars), start=1):
                user = f"{context}\n\n{_transcript_block(doc, segs)}\n\nReturn the 1st-order concepts for this transcript."
                log(f"  coding {doc_id} pass {p}" + (f" part {ci}" if ci > 1 else "") + " ...")
                try:
                    res = llm.json(system, user, schema, "first_order_concepts")
                except LLMError as exc:
                    run.update_manifest(status="failed", error=f"{doc_id}: {exc}")
                    raise
                calls.append({"doc": doc_id, "pass": p, "part": ci, **res.meta})
                pending = res.data.get("concepts", [])
                for attempt in range(retries + 1):
                    bad = []
                    with ops.batch():
                        for c in pending:
                            grounded, missing = [], []
                            for q in c.get("quotes", []):
                                hit = ground(q.get("quote", ""), q.get("segment_id"), segs, threshold)
                                if hit:
                                    seg, sp = hit
                                    grounded.append({"doc": doc_id, "segment": seg["id"], "start": sp.start, "end": sp.end,
                                                     "text": seg["text"][sp.start:sp.end], "score": sp.score, "method": sp.method})
                                    stats[sp.method] += 1
                                else:
                                    missing.append(q)
                            if grounded:
                                used = [s for s in segs if s["id"] in {g["segment"] for g in grounded}]
                                ops.add_concept(c["label"], c.get("description", ""), grounded,
                                                reason="coded from transcript" + (f" (retry {attempt})" if attempt else ""),
                                                origin={"doc": doc_id, "pass": p, "attempt": attempt},
                                                flags=_flags(c["label"], used, grounded), memo=c.get("memo"))
                                stats["concepts"] += 1
                                stats["quotes_ok"] += len(grounded)
                                if attempt:
                                    stats["recovered_on_retry"] += 1
                            if missing:
                                bad.append({**c, "quotes": missing, "_had_grounded": bool(grounded)})
                    if not bad or attempt == retries:
                        for c in bad:
                            stats["quotes_failed"] += len(c["quotes"])
                            failures.append({"doc": doc_id, "pass": p, "label": c["label"], "quotes": c["quotes"]})
                        break
                    listing = "\n".join(
                        f'- concept "{c["label"]}": segment {q.get("segment_id")}: "{q.get("quote")}"' for c in bad for q in c["quotes"]
                    )
                    retry_user = (
                        f"{user}\n\nThese quotes from your previous answer were not found verbatim in the transcript:\n{listing}\n\n"
                        "Return only these concepts again, with each quote copied exactly from the segment text "
                        "(character for character, original language). Drop a quote if the informant did not say it. "
                        "Do not repeat concepts that already had a valid quote unless you are adding a corrected quote."
                    )
                    log(f"    {sum(len(c['quotes']) for c in bad)} quote(s) not found, retrying")
                    res = llm.json(system, retry_user, schema, "first_order_concepts")
                    calls.append({"doc": doc_id, "pass": p, "part": ci, "retry": attempt + 1, **res.meta})
                    pending = res.data.get("concepts", [])

    run.update_manifest(status="done", calls=calls, grounding=stats, ungrounded=failures,
                        served_models=sorted({c.get("model_served") for c in calls if c.get("model_served")}))
    log(f"  {stats['concepts']} concepts, {stats['quotes_ok']} quotes grounded, {stats['quotes_failed']} rejected")
    return run


# ---------------------------------------------------------------------------
# Stage 2
# ---------------------------------------------------------------------------


def _norm_label(s: str) -> str:
    return " ".join(re.findall(r"[\wÅÄÖåäö]+", s.lower()))


def consolidate(project: Project, run_id: str, into: str | None = None, use_model: bool = True,
                llm: LLM | None = None, log: Log = print) -> Run:
    run = fork(project, run_id, into, keep="concepts", kind="consolidation", actor="pipeline",
               note="stage 2: consolidate 1st-order concepts") if into else project.run(run_id)
    label_language = project.cfg("project", "label_language", "English")
    system = load_prompt("consolidate").replace("{label_language}", label_language)
    prompt_hash = sha256_text(system)
    ops = Ops(run, actor="pipeline", prompt_hash=prompt_hash)

    # 2a. identical labels (deterministic, as in the AMCIS pipeline)
    groups: dict[str, list[str]] = {}
    for c in ops.s["concepts"].values():
        if c["status"] == "active":
            groups.setdefault(_norm_label(c["label"]), []).append(c["id"])
    n_exact = 0
    with ops.batch():
        for ids in groups.values():
            if len(ids) > 1:
                first = ops.s["concepts"][ids[0]]
                ops.merge_concepts(ids, first["label"], first["description"], "identical label")
                n_exact += 1
    log(f"  merged {n_exact} groups with identical labels")

    meta: dict = {"exact_label_merges": n_exact}
    if use_model:
        llm = llm or make_llm(llm_config(project))
        ops.model = llm.cfg.get("model")
        active = [c for c in ops.s["concepts"].values() if c["status"] == "active"]
        lines = []
        for c in active:
            docs = sorted({ops.s["quotes"][q]["doc"] for q in c["quotes"]})
            q0 = ops.s["quotes"][c["quotes"][0]]["text"]
            q0 = q0 if len(q0) < 300 else q0[:297] + "..."
            lines.append(
                f'<concept id="{c["id"]}" label="{html.escape(c["label"])}" informants="{len(docs)}">\n'
                f'{html.escape(c["description"])}\nExample quote: "{html.escape(q0)}"\n</concept>'
            )
        user = f"{project.context_text()}\n\n<concepts>\n" + "\n".join(lines) + "\n</concepts>\n\nPropose merges."
        log(f"  asking model about {len(active)} concepts ...")
        res = llm.json(system, user, MERGES_SCHEMA, "concept_merges")
        applied, rejected, used = 0, [], set()
        with ops.batch():
            for m in res.data.get("merges", []):
                ids = [i for i in dict.fromkeys(m.get("ids", []))]
                bad = [i for i in ids if i in used or ops.s["concepts"].get(i, {}).get("status") != "active"]
                if len(ids) < 2 or bad:
                    rejected.append({"merge": m, "why": f"invalid or reused ids {bad}" if bad else "fewer than two ids"})
                    continue
                ops.merge_concepts(ids, m["label"], m.get("description", ""), m.get("reason") or "model: same point")
                used.update(ids)
                applied += 1
        meta.update(llm=llm.describe(), call=res.meta, model_merges=applied, rejected_merges=rejected)
        log(f"  applied {applied} model-proposed merges, rejected {len(rejected)}")

    run.update_manifest(stage=2, prompt_hash=prompt_hash, consolidation=meta, status="done")
    return run
