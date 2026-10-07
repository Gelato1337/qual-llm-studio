"""Independent analyst runs driven by an agent harness (pi).

Each replicate gets:
  * its own run, forked "blind" from a coding/consolidation run (concepts only),
  * its own workspace folder as the working directory,
  * a `qls` wrapper on PATH bound to that run and to actor "agent:pi:<model>",
  * pi in print mode with only read + bash tools, no context files, no
    extensions, and the grouping instructions as the prompt.

pi's event stream and session are kept in the run folder, so the path the
agent took (what it read, searched, revised) can be analysed later.

This is a light sandbox (separate cwd, fixed tools, no shared state between
replicates). For hard isolation run it inside a container.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import time

from .coding import load_prompt
from .project import Project, QlsError, Run, sha256_text
from .runs import fork


def _wrapper(workspace, project: Project, run: Run, actor: str) -> None:
    w = workspace / "qls"
    w.write_text(
        "#!/bin/sh\n"
        f'export QLS_PROJECT="{project.root}"\nexport QLS_RUN="{run.id}"\nexport QLS_ACTOR="{actor}"\n'
        f'exec "{sys.executable}" -m qls "$@"\n',
        encoding="utf-8",
    )
    w.chmod(w.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def run_pi(project: Project, from_run: str, name: str | None = None, replicates: int = 1,
           provider: str | None = None, model: str | None = None, thinking: str | None = None,
           pi_bin: str | None = None, dry_run: bool = False, timeout: int = 3600, log=print) -> list[Run]:
    cfg = project.config.get("analyst", {})
    provider = provider or cfg.get("provider", "anthropic")
    model = model or cfg.get("model", "claude-opus-5-5")
    thinking = thinking or cfg.get("thinking", "high")
    pi_bin = pi_bin or cfg.get("pi_bin", "pi")
    if not dry_run and shutil.which(pi_bin) is None:
        raise QlsError(f"pi not found ({pi_bin!r}). Install with: npm install -g @mariozechner/pi-coding-agent")

    template = load_prompt("analyst_grouping")
    runs = []
    for i in range(1, replicates + 1):
        rid = name if (name and replicates == 1) else f"{name or from_run + '-pi'}-{i}"
        actor = f"agent:pi:{model}"
        run = fork(project, from_run, rid, keep="concepts", kind="grouping", actor=actor,
                   note=f"independent grouping by pi ({model}), replicate {i}/{replicates}")
        ws = run.dir / "workspace"
        ws.mkdir()
        _wrapper(ws, project, run, actor)
        prompt = template.replace("{run}", run.id).replace("{context}", project.context_text())
        cmd = [pi_bin, "-p", "--mode", "json", "--provider", provider, "--model", model, "--thinking", thinking,
               "--session-dir", str(run.dir / "pi-session"), "--no-context-files", "--no-extensions",
               "--no-skills", "--no-prompt-templates", "--tools", "read,bash", prompt]
        env = {**os.environ, "PATH": f"{ws}{os.pathsep}{os.environ.get('PATH', '')}",
               "QLS_PROJECT": str(project.root), "QLS_RUN": run.id, "QLS_ACTOR": actor}
        if cfg.get("isolate_pi_config", True):
            agent_dir = run.dir / "pi-agent"
            agent_dir.mkdir()
            models = project.root / "pi" / "models.json"
            if models.exists():
                shutil.copy2(models, agent_dir / "models.json")
            env["PI_CODING_AGENT_DIR"] = str(agent_dir)
        info = {"harness": "pi", "provider": provider, "model": model, "thinking": thinking,
                "prompt_hash": sha256_text(prompt), "command": cmd[:-1] + ["<prompt>"], "replicate": i}
        (run.dir / "analyst_prompt.md").write_text(prompt, encoding="utf-8")
        if dry_run:
            run.update_manifest(analyst=info, status="prepared")
            log(f"  prepared {run.id} (dry run): cd {ws} && {' '.join(cmd[:-1])} \"$(cat ../analyst_prompt.md)\"")
            runs.append(run)
            continue
        try:
            v = subprocess.run([pi_bin, "--version"], capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
            ver = (v.stdout or v.stderr).strip()
        except (OSError, subprocess.SubprocessError):
            ver = None
        log(f"  {run.id}: pi {ver or ''} running {provider}/{model} ...")
        t0 = time.time()
        with open(run.dir / "pi-events.jsonl", "w") as out, open(run.dir / "pi-stderr.log", "w") as err:
            try:
                proc = subprocess.run(cmd, cwd=ws, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=err, timeout=timeout)
                code = proc.returncode
            except subprocess.TimeoutExpired:
                code = "timeout"
        info.update(pi_version=ver, exit_code=code, elapsed_s=round(time.time() - t0, 1))
        run.update_manifest(analyst=info, process=process_metrics(run), status="done" if code == 0 else "failed")
        log(f"  {run.id}: exit {code} after {info['elapsed_s']}s")
        runs.append(run)
    return runs


# What an analyst did, read from pi's event stream: how much it looked at the
# data versus how much it changed the structure. Used to ask whether runs that
# read more produce more stable or more informant-close structures.
_READ_CMDS = {"doc", "search", "concept show", "concepts", "structure", "status", "decisions", "docs", "check", "guide"}
_EDIT_CMDS = {"concept merge", "concept split", "concept rename", "concept drop", "concept restore",
              "theme create", "theme assign", "theme unassign", "theme rename", "theme drop",
              "dim create", "dim assign", "dim rename", "dim drop", "memo"}


def process_metrics(run: Run) -> dict:
    import json
    import re
    import shlex

    path = run.dir / "pi-events.jsonl"
    m = {"turns": 0, "tool_calls": 0, "file_reads": 0, "qls_reads": {}, "qls_edits": {}, "docs_read": [], "searches": 0}
    if not path.exists():
        return m
    docs = set()
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "turn_start":
            m["turns"] += 1
        if ev.get("type") != "tool_execution_start":
            continue
        m["tool_calls"] += 1
        if ev.get("toolName") == "read":
            m["file_reads"] += 1
            continue
        cmd = (ev.get("args") or {}).get("command", "")
        for part in re.split(r"&&|\|\||;|\n", cmd):
            try:
                toks = shlex.split(part)
            except ValueError:
                toks = part.split()
            if "qls" not in toks:
                continue
            toks = toks[toks.index("qls") + 1:]
            if not toks:
                continue
            two = " ".join(toks[:2])
            key = two if two in _EDIT_CMDS or two == "concept show" else toks[0]
            if key in _EDIT_CMDS:
                m["qls_edits"][key] = m["qls_edits"].get(key, 0) + 1
            elif key in _READ_CMDS:
                m["qls_reads"][key] = m["qls_reads"].get(key, 0) + 1
                if toks[0] == "doc" and len(toks) > 2:
                    docs.add(toks[2])
                if toks[0] == "search":
                    m["searches"] += 1
    m["docs_read"] = sorted(docs)
    return m
