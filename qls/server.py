"""MCP server: the agent tools of tools.py over stdio, for any harness (pi, Claude Code, Cowork).

The server is bound to one project, one run and one actor when it starts:

    qls mcp --run r1 --actor agent:coder-01        # an analyst
    qls mcp --run r1 --actor reviewer:anna --readonly   # a reviewer: read tools only

An agent therefore cannot choose its run mid-way or write as a human; researcher
actions (approve, reject, edit, resume) exist only in the CLI.
"""

from __future__ import annotations

import inspect
import os
from importlib import resources

from .tools import AGENT_TOOLS, Session, is_human
from .util import QlsError

READ_TOOLS = ["recipe_info", "list_sources", "read_source", "search", "get", "neighbors", "get_codebook", "get_terms",
              "status", "check", "get_memo", "get_feedback", "read_board"]

INSTRUCTIONS = """\
Tools for qualitative analysis on a strict evidence store. Start with recipe_info() (the method),
ways_of_working() (how to quote, cite and memo), and list_sources(). Every write is validated:
when a tool refuses, it says why and how to fix it. Quotes must be verified with add_quote();
every link that needs a reason must have one; memos must be about something. When the recipe
asks for a checkpoint, call checkpoint() and stop until the researcher resumes the run.
"""


def build_server(session: Session, readonly: bool = False):
    try:
        from mcp.server.mcpserver import MCPServer as Server  # mcp >= 2
    except ImportError:
        try:
            from mcp.server.fastmcp import FastMCP as Server  # mcp 1.x
        except ImportError as exc:
            raise QlsError("pip install 'qual-llm-studio[mcp]' to run the MCP server") from exc

    mcp = Server("qls", instructions=INSTRUCTIONS)
    names = READ_TOOLS if readonly else AGENT_TOOLS

    for name in names:
        fn = getattr(session, name)
        wrapped = _wrap(fn, name)
        mcp.tool(name=name, description=(inspect.getdoc(fn) or name))(wrapped)

    @mcp.tool(name="ways_of_working", description="How to quote, cite, label and write memos (AGENTS.md).")
    def ways_of_working() -> str:
        return resources.files("qls.agents").joinpath("AGENTS.md").read_text(encoding="utf-8")

    return mcp


def _wrap(fn, name):
    """Expose the bound method with its signature; turn errors into refusals the model can read."""
    sig = inspect.signature(fn)

    def tool(**kwargs):
        try:
            return fn(**kwargs)
        except QlsError as exc:
            return {"ok": False, "error": str(exc)}

    tool.__name__ = name
    tool.__doc__ = inspect.getdoc(fn)
    tool.__signature__ = sig
    tool.__annotations__ = {p.name: p.annotation for p in sig.parameters.values() if p.annotation is not inspect._empty}
    if sig.return_annotation is not inspect._empty:
        tool.__annotations__["return"] = sig.return_annotation
    return tool


def main(project: str | None, run: str | None, actor: str | None, model: str | None = None, readonly: bool = False) -> None:
    from .project import Project

    run = run or os.environ.get("QLS_RUN")
    if not run or not actor:
        raise QlsError("qls mcp needs --run (or $QLS_RUN) and --actor (or $QLS_ACTOR)")
    if is_human(actor):
        raise QlsError("Researcher actions go through the CLI (qls respond / resume), not an MCP server.")
    readonly = readonly or actor.startswith("reviewer")
    session = Session(Project.find(project).store, run, actor, model or os.environ.get("QLS_MODEL"))
    build_server(session, readonly).run()
