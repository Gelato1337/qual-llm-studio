"""MCP server exposing qls to Claude Cowork, Claude Code, Claude Desktop and pi.

Same operations as the CLI, same decision log. Start with `qls mcp` (stdio).
Every changing tool takes `actor`: use "agent:<who>" for the assistant's own
decisions and "human" only for decisions the researcher made.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .compare import compare_runs as _compare, consensus as _consensus
from .ops import Ops
from .project import Project, QlsError
from .report import compare_report, run_report
from .runs import fork
from .views import (active, check, concept_card, membership, read_document as _read_document,
                    search_corpus as _search, status, structure_text)

INSTRUCTIONS = """\
Qual LLM Studio: visible, replayable qualitative analysis (Gioia method as reference).
A project holds transcripts (documents > turns > informant segments) and runs. A run holds
1st-order concepts (with verified quotes), 2nd-order themes, aggregate dimensions and memos.
Every change goes through a tool with a one-line reason and is logged as a decision record
with an actor: use actor="agent:<your name>" for your own decisions and actor="human" only
when recording what the researcher decided. Start with open_project, then list_runs.
For an independent grouping, fork_run(blind=True) first so you cannot see other themes.
Work from concept cards (list_concepts cards=True) and the transcripts, not labels alone.
"""

_state: dict[str, Any] = {"project": None}


def _p() -> Project:
    if _state["project"] is None:
        try:
            _state["project"] = Project.find()
        except QlsError as exc:
            raise QlsError("No project open. Call open_project(path) with the folder containing qls.toml.") from exc
    return _state["project"]


def _ops(run: str, actor: str) -> Ops:
    return Ops(_p().run(run), actor=actor)


def build_server():
    try:
        from mcp.server.mcpserver import MCPServer as Server  # mcp >= 2
    except ImportError:
        try:
            from mcp.server.fastmcp import FastMCP as Server  # mcp 1.x
        except ImportError as exc:
            raise QlsError("pip install 'qual-llm-studio[mcp]' to run the MCP server") from exc

    mcp = Server("qls", instructions=INSTRUCTIONS)

    # -- project & corpus -----------------------------------------------------

    @mcp.tool()
    def open_project(path: str) -> dict:
        """Open a qls project folder (the one containing qls.toml)."""
        _state["project"] = Project.find(path)
        p = _state["project"]
        return {"root": str(p.root), "documents": len(p.doc_ids()), "runs": p.run_ids(), "context_files": list(p.context_files())}

    @mcp.tool()
    def project_context() -> str:
        """The research question and study context files the analysis is guided by."""
        return _p().context_text()

    @mcp.tool()
    def list_documents() -> list[dict]:
        """Transcripts with participant, number of turns and informant segments."""
        return [{"id": d["id"], "participant": d.get("participant"), "turns": len(d["turns"]),
                 "segments": len(d["segments"]), "speakers": d["speakers"]} for d in _p().docs()]

    @mcp.tool()
    def read_document(doc: str, start: int = 1, limit: int = 40) -> dict:
        """Read turns of a transcript (1-based start), with the segment IDs of informant turns."""
        return _read_document(_p(), doc, start, limit)

    @mcp.tool()
    def search_corpus(pattern: str, docs: list[str] | None = None, regex: bool = False,
                      include_interviewer: bool = False, limit: int = 30) -> list[dict]:
        """Find text in informant segments (or all turns). Use it to look for evidence and counter-evidence."""
        return _search(_p(), pattern, docs, limit, regex, not include_interviewer)

    # -- runs -----------------------------------------------------------------

    @mcp.tool()
    def list_runs() -> list[dict]:
        """All runs with kind, parent and counts."""
        out = []
        for rid in _p().run_ids():
            r = _p().run(rid)
            s, m = r.state(), r.manifest()
            out.append({"run": rid, "kind": m.get("kind"), "parent": m.get("parent"), "status": m.get("status"),
                        "concepts": len(active(s, "concepts")), "themes": len(active(s, "themes")),
                        "dimensions": len(active(s, "dimensions"))})
        return out

    @mcp.tool()
    def fork_run(source: str, new: str, blind: bool = True, note: str = "", actor: str = "agent:mcp") -> dict:
        """Copy a run. blind=True keeps only the 1st-order concepts (for an independent grouping)."""
        r = fork(_p(), source, new, keep="concepts" if blind else "all", actor=actor, note=note)
        return status(_p(), r)

    @mcp.tool()
    def run_status(run: str) -> dict:
        """Counts, unassigned concepts, themes without dimension, flagged concepts."""
        return status(_p(), _p().run(run))

    @mcp.tool()
    def check_run(run: str) -> list[str]:
        """Integrity problems (lost quotes, double assignments). Empty list means OK."""
        return check(_p().run(run))

    @mcp.tool()
    def show_structure(run: str, with_concepts: bool = True) -> str:
        """Dimension > theme > concept tree as text."""
        return structure_text(_p(), _p().run(run), with_concepts)

    @mcp.tool()
    def list_concepts(run: str, unassigned_only: bool = False, theme: str | None = None,
                      cards: bool = False, quotes: int = 2) -> list[dict]:
        """Active 1st-order concepts. cards=True adds description, informants and quotes with the question they answer."""
        s = _p().run(run).state()
        c2t, _ = membership(s)
        ids = [c["id"] for c in active(s, "concepts")]
        if unassigned_only:
            ids = [c for c in ids if c not in c2t]
        if theme:
            ids = [c for c in ids if c2t.get(c) == theme]
        if not cards:
            return [{"id": c, "label": s["concepts"][c]["label"], "theme": c2t.get(c)} for c in ids]
        return [concept_card(_p(), s, c, quotes) for c in ids]

    @mcp.tool()
    def get_concept(run: str, concept_id: str, quotes: int = 10) -> dict:
        """Full concept card."""
        return concept_card(_p(), _p().run(run).state(), concept_id, quotes)

    @mcp.tool()
    def decision_log(run: str, last: int = 30) -> list[dict]:
        """The most recent decision records of a run."""
        return _p().run(run).decisions()[-last:]

    # -- changes (all logged) -------------------------------------------------

    @mcp.tool()
    def merge_concepts(run: str, concept_ids: list[str], label: str, description: str, reason: str, actor: str = "agent:mcp") -> str:
        """Merge concepts that make the same point. Quotes move to the new concept (lossless)."""
        return _ops(run, actor).merge_concepts(concept_ids, label, description, reason)

    @mcp.tool()
    def split_concept(run: str, concept_id: str, parts: list[dict], reason: str, actor: str = "agent:mcp") -> list[str]:
        """Split a concept. parts = [{"label": ..., "description": ..., "quotes": ["q1", ...]}]; every quote must be placed."""
        return _ops(run, actor).split_concept(concept_id, parts, reason)

    @mcp.tool()
    def rename_concept(run: str, concept_id: str, reason: str, label: str | None = None,
                       description: str | None = None, actor: str = "agent:mcp") -> str:
        """Change a concept's label and/or description."""
        _ops(run, actor).rename_concept(concept_id, label, description, reason)
        return concept_id

    @mcp.tool()
    def drop_concept(run: str, concept_id: str, reason: str, actor: str = "agent:mcp") -> str:
        """Remove a concept from the analysis (kept in the log; can be restored)."""
        _ops(run, actor).drop_concept(concept_id, reason)
        return concept_id

    @mcp.tool()
    def create_theme(run: str, label: str, definition: str, concept_ids: list[str], reason: str, actor: str = "agent:mcp") -> str:
        """Create a 2nd-order theme from concepts (concepts move here from any other theme)."""
        return _ops(run, actor).create_theme(label, definition, concept_ids, reason)

    @mcp.tool()
    def assign_concepts(run: str, theme_id: str, concept_ids: list[str], reason: str, actor: str = "agent:mcp") -> str:
        """Put concepts into a theme (moving them from any other theme)."""
        _ops(run, actor).assign_concepts(theme_id, concept_ids, reason)
        return theme_id

    @mcp.tool()
    def unassign_concepts(run: str, concept_ids: list[str], reason: str, actor: str = "agent:mcp") -> str:
        """Take concepts out of their themes."""
        _ops(run, actor).unassign_concepts(concept_ids, reason)
        return "ok"

    @mcp.tool()
    def rename_theme(run: str, theme_id: str, reason: str, label: str | None = None,
                     definition: str | None = None, actor: str = "agent:mcp") -> str:
        """Change a theme's label and/or definition."""
        _ops(run, actor).rename_theme(theme_id, label, definition, reason)
        return theme_id

    @mcp.tool()
    def drop_theme(run: str, theme_id: str, reason: str, actor: str = "agent:mcp") -> str:
        """Dissolve a theme; its concepts become unassigned."""
        _ops(run, actor).drop_theme(theme_id, reason)
        return theme_id

    @mcp.tool()
    def create_dimension(run: str, label: str, definition: str, theme_ids: list[str], reason: str, actor: str = "agent:mcp") -> str:
        """Create an aggregate dimension from themes."""
        return _ops(run, actor).create_dimension(label, definition, theme_ids, reason)

    @mcp.tool()
    def assign_themes(run: str, dimension_id: str, theme_ids: list[str], reason: str, actor: str = "agent:mcp") -> str:
        """Put themes into an aggregate dimension."""
        _ops(run, actor).assign_themes(dimension_id, theme_ids, reason)
        return dimension_id

    @mcp.tool()
    def rename_dimension(run: str, dimension_id: str, reason: str, label: str | None = None,
                         definition: str | None = None, actor: str = "agent:mcp") -> str:
        """Change a dimension's label and/or definition."""
        _ops(run, actor).rename_dimension(dimension_id, label, definition, reason)
        return dimension_id

    @mcp.tool()
    def drop_dimension(run: str, dimension_id: str, reason: str, actor: str = "agent:mcp") -> str:
        """Dissolve an aggregate dimension; its themes become unplaced."""
        _ops(run, actor).drop_dimension(dimension_id, reason)
        return dimension_id

    @mcp.tool()
    def add_memo(run: str, text: str, links: list[str] | None = None, actor: str = "agent:mcp") -> str:
        """Write an analytic memo linked to concepts/themes/dimensions (e.g. a counter-argument or open question)."""
        return _ops(run, actor).add_memo(text, links or [])

    # -- comparison & reports ------------------------------------------------

    @mcp.tool()
    def compare_runs(run_a: str, run_b: str) -> dict:
        """Structural agreement between two runs (Rand, ARI, NMI per level) and which groupings differ."""
        return _compare(_p(), run_a, run_b)

    @mcp.tool()
    def consensus(runs: list[str], level: str = "theme") -> dict:
        """Stability of groupings across several runs that share concepts."""
        return _consensus(_p(), runs, level)

    @mcp.tool()
    def write_report(run: str) -> str:
        """Write the HTML report of a run into reports/ and return its path."""
        out = _p().root / "reports" / f"{run}.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(run_report(_p(), run), encoding="utf-8")
        return str(out)

    @mcp.tool()
    def write_compare_report(run_a: str, run_b: str) -> str:
        """Write an HTML report showing only where two runs differ; returns its path."""
        out = _p().root / "reports" / f"compare_{run_a}_vs_{run_b}.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(compare_report(_p(), run_a, run_b), encoding="utf-8")
        return str(out)

    return mcp


def main(project: str | None = None) -> None:
    if project:
        _state["project"] = Project.find(Path(project))
    build_server().run()
