"""Attaching agents to a run, outside the agent's control.

    qls agent prompt RUN [--step code] [--sources P01 P02] [--transport mcp|shell]
    qls agent mcp-config RUN --actor agent:NAME        JSON for .mcp.json (Claude Code, Cowork, pi >= 1.0)
    qls agent pi RUN --actor agent:NAME [--model ...]   launch pi in a workspace with the shell transport

The task prompt is generated from the run (context files frozen at run creation,
recipe steps, assignment). The order of sources follows the run config's
`order_seed`, so order effects can be studied as a run parameter.
"""

from __future__ import annotations

import inspect
import json
import os
import random
import shutil
import stat
import subprocess
import sys
import time
from importlib import resources
from pathlib import Path

from .project import Project
from .store import Store
from .tools import AGENT_TOOLS, Session, is_human
from .util import QlsError, sha256_text


def _agent_actor(actor: str) -> str:
    if is_human(actor) or ":" not in actor:
        raise QlsError(f"{actor!r} is not an agent actor; use agent:NAME (researchers act through the CLI)")
    return actor


def source_order(store: Store, run: str, sources: list[str] | None = None) -> list[str]:
    ids = sources or store.source_ids()
    seed = store.run(run)["config"].get("order_seed")
    if seed is None:
        return list(ids)
    ids = sorted(ids)
    random.Random(seed).shuffle(ids)
    return ids


def tool_reference(transport: str) -> str:
    lines = []
    for name in AGENT_TOOLS:
        fn = getattr(Session, name)
        sig = str(inspect.signature(fn)).replace("(self, ", "(").replace("(self)", "()")
        doc = (inspect.getdoc(fn) or "").split("\n")[0]
        lines.append(f"- {name}{sig}: {doc}")
    head = ("Call the tools of the `qls` MCP server." if transport == "mcp" else
            "Call tools in the shell: `q <tool> '<json arguments>'`, e.g. "
            "`q add_quote '{\"source\": \"P01\", \"text\": \"...\"}'`. Output is JSON.")
    return head + "\n\n" + "\n".join(lines)


def task_prompt(project: Project, run: str, actor: str, steps: list[str] | None = None,
                sources: list[str] | None = None, transport: str = "mcp") -> str:
    _agent_actor(actor)
    store = project.store
    r = store.run(run)
    s = Session(store, run, actor)
    rec = s.recipe
    context = r["config"].get("context") or project.context_files()
    ctx = "\n\n".join(f"### {n}\n{b.strip()}" for n, b in context.items())
    chosen = [st for st in rec.steps if not steps or st["id"] in steps]
    steps_txt = "\n\n".join(f"### Step `{st['id']}`: {st['title']}" + (" (ends with a checkpoint)" if st.get("checkpoint") else "")
                            + f"\n{st['instructions'].strip()}" for st in chosen)
    order = source_order(store, run, sources)
    parts = [
        f"You are `{actor}`, an analyst in run `{run}` of a qualitative study. Method: recipe `{rec.name}` "
        f"(version {rec.version}).",
        f"## Study\n\n{ctx}",
        f"## Your steps\n\n{steps_txt}",
        f"## Your sources, in this order\n\n{', '.join(order)}",
    ]
    if transport == "shell":
        guide = resources.files("qls.agents").joinpath("AGENTS.md").read_text(encoding="utf-8")
        parts.append(guide)
    else:
        parts.append("Read `ways_of_working()` and `recipe_info()` first.")
    parts.append("## Tools\n\n" + tool_reference(transport))
    return "\n\n".join(parts)


def mcp_config(project: Project, run: str, actor: str, model: str | None = None) -> dict:
    _agent_actor(actor)
    args = ["-m", "qls", "mcp", "--project", str(project.root), "--run", run, "--actor", actor]
    if model:
        args += ["--model", model]
    return {"mcpServers": {"qls": {"command": sys.executable, "args": args}}}


def launch_pi(project: Project, run: str, actor: str, provider: str, model: str, thinking: str = "high",
              steps: list[str] | None = None, sources: list[str] | None = None, pi_bin: str = "pi",
              timeout: int = 7200, log=print) -> dict:
    _agent_actor(actor)
    if shutil.which(pi_bin) is None:
        raise QlsError(f"pi not found ({pi_bin!r}); npm install -g @mariozechner/pi-coding-agent")
    work = project.root / "agents" / run / actor.replace(":", "_")
    work.mkdir(parents=True, exist_ok=True)
    wrapper = work / "q"
    wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" -m qls tool "{run}" "$@" --actor "{actor}" --project "{project.root}"\n')
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    prompt = task_prompt(project, run, actor, steps, sources, transport="shell")
    (work / "task.md").write_text(prompt, encoding="utf-8")
    agent_dir = work / "pi-agent"
    agent_dir.mkdir(exist_ok=True)
    models = project.root / "pi" / "models.json"
    if models.exists():
        shutil.copy2(models, agent_dir / "models.json")
    env = {**os.environ, "PATH": f"{work}{os.pathsep}{os.environ.get('PATH', '')}", "PI_CODING_AGENT_DIR": str(agent_dir),
           "QLS_MODEL": f"{provider}/{model}"}
    cmd = [pi_bin, "-p", "--mode", "json", "--provider", provider, "--model", model, "--thinking", thinking,
           "--session-dir", str(work / "session"), "--no-context-files", "--no-extensions", "--no-skills",
           "--no-prompt-templates", "--tools", "read,bash", prompt]
    t0 = time.time()
    store = project.store
    start_seq = max([e["seq"] for e in store.effective_events(run)] or [0])
    with open(work / "events.jsonl", "w") as out, open(work / "stderr.log", "w") as err:
        try:
            code = subprocess.run(cmd, cwd=work, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=err, timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            code = "timeout"
    written = [e for e in store.events(run, since=start_seq, own_only=True) if e["actor"] == actor]
    info = {"actor": actor, "harness": "pi", "provider": provider, "model": model, "thinking": thinking, "exit": code,
            "elapsed_s": round(time.time() - t0, 1), "prompt_hash": sha256_text(prompt), "events_written": len(written),
            "workspace": str(work)}
    (work / "launch.json").write_text(json.dumps(info, indent=1))
    store.append(run, "note", "system", {"agent_session": info}, f"agent session of {actor} ended")
    log(f"{actor}: exit {code}, {len(written)} events written in {info['elapsed_s']}s")
    return info
