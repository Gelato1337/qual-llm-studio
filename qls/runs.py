"""Run lifecycle: forking a run so stages can be rerun from frozen upstream state."""

from __future__ import annotations

import copy

from .project import Project, QlsError, Run, now

KEEP = ("all", "concepts")


def fork(project: Project, src_id: str, new_id: str | None, keep: str = "all", kind: str | None = None,
         actor: str = "human", note: str = "") -> Run:
    """Copy a run's state into a new run.

    keep="concepts" drops themes, dimensions and memos: use it for an
    independent ("blind") grouping that must not see anyone else's themes.
    """
    if keep not in KEEP:
        raise QlsError(f"keep must be one of {KEEP}")
    src = project.run(src_id)
    s = copy.deepcopy(src.state())
    if keep == "concepts":
        s["themes"], s["dimensions"], s["memos"] = {}, {}, {}
    m = src.manifest()
    new = project.new_run(new_id, kind or ("grouping" if keep == "concepts" else m.get("kind", "fork")),
                          parent=src_id, forked_with=keep, actor=actor, note=note,
                          upstream=m.get("upstream", []) + [{"run": src_id, "kind": m.get("kind"), "corpus_hash": m.get("corpus_hash")}])
    s["run"] = new.id
    new.save_state(s)
    new.append_decision({
        "id": "dr1", "run_id": new.id, "stage": None, "actor": actor, "model": None, "prompt_hash": None,
        "op": "fork", "target": "run", "inputs": [src_id], "outputs": [new.id],
        "reason": note or f"forked from {src_id} (keep={keep})", "detail": {"keep": keep}, "ts": now(),
    })
    return new
