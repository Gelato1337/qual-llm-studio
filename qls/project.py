"""Project layout, configuration, corpus access and runs.

A project is a folder:

    qls.toml                 settings (models, transcript conventions)
    context/                 what the researcher feeds into the analysis
      research_question.md
      study_context.md
      interview_guide.md     optional; used to flag concepts that echo the guide
    sources/                 original files (copied on ingest)
    corpus/docs/<doc>.json   parsed documents: turns and segments with stable IDs
    runs/<run>/              manifest.json, state.json, decisions.jsonl, logs

Raw text lives only in corpus/. Runs refer to it by IDs and character spans.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CONFIG_NAME = "qls.toml"

DEFAULT_CONFIG = """\
# Qual LLM Studio project settings.
# What the analysis is *about* lives in context/*.md, not here.

[project]
name = "{name}"
# Language for concept labels and descriptions. Quotes always stay verbatim
# in the transcript's own language.
label_language = "English"

[transcripts]
# Speaker labels that mark the interviewer. Matching is case-insensitive.
interviewer_labels = ["I", "Q", "H", "Interviewer", "Haastattelija", "Moderator"]
# Informant turns longer than this are split into several segments.
max_segment_chars = 2000

[coding]
# How many independent coding passes per transcript (the AMCIS pipeline used 3).
passes = 1
# Minimum fuzzy-match score (0-100) for a quote to count as found in the transcript.
quote_threshold = 90
# Retries with feedback when quotes cannot be found.
max_retries = 2
# Write a short coding memo with every concept (meaning here, what it is not,
# conditions, doubts). Later stages read it. Set false for the no-memo condition.
concept_memos = true

[llm]
# anthropic | openai | external | mock
#   openai covers any OpenAI-compatible endpoint: OpenAI, OpenRouter, vLLM, Ollama.
#   external writes each request to external/requests/ and reads the answer from
#   external/answers/ (an assistant without an API key, or replaying old answers).
provider = "anthropic"
model = "claude-opus-5-5"
# Anthropic: low | medium | high | xhigh | max
effort = "high"
# Anthropic: let the API re-run a refused request on a fallback model.
# The model that actually answered is logged in every run manifest.
fallbacks = true
max_tokens = 64000
# OpenAI-compatible endpoints only:
base_url = ""
api_key_env = "OPENAI_API_KEY"
temperature = 0.0
seed = 0

[analyst]
# Agent harness used by `qls analyst` for grouping runs.
harness = "pi"
provider = "anthropic"
model = "claude-opus-5-5"
thinking = "high"
# Give every analyst run its own pi config folder (runs/<run>/pi-agent) holding a
# copy of pi/models.json, so user-level pi settings and extensions cannot leak in.
# Authentication then comes from environment variables (e.g. ANTHROPIC_API_KEY).
isolate_pi_config = true
"""

# pi releases older than the model they are asked to run do not know its ID and
# would send it outdated thinking settings. Registering the model here with
# "reasoning": false makes pi omit the thinking parameter; Claude Opus 5.5 then
# thinks adaptively by default. Delete entries once your pi version lists the model.
PI_MODELS = """\
{
  "providers": {
    "anthropic": {
      "baseUrl": "https://api.anthropic.com",
      "apiKey": "ANTHROPIC_API_KEY",
      "api": "anthropic-messages",
      "models": [
        {"id": "claude-opus-5-5", "name": "Claude Opus 5.5", "reasoning": false,
         "input": ["text", "image"], "contextWindow": 1000000, "maxTokens": 128000}
      ]
    }
  }
}
"""

CONTEXT_FILES = {
    "research_question.md": "# Research question\n\nWrite the research question that guides 1st-order coding here.\n",
    "study_context.md": (
        "# Study context\n\n"
        "Describe what the analyst needs to know: setting, who the informants are, how they were\n"
        "sampled, what kind of concept you are looking for (e.g. 'technology trends', not\n"
        "'technologies'), and anything else you would tell a new co-author.\n"
    ),
}

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_bytes(b: bytes) -> str:
    return "sha256:" + hashlib.sha256(b).hexdigest()


def sha256_text(s: str) -> str:
    return sha256_bytes(s.encode("utf-8"))


def write_json(path: Path, data: Any) -> None:
    """Atomic JSON write (tmp file + rename) so readers never see half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


class QlsError(Exception):
    """User-facing error: printed without a traceback."""


# ---------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------


class Project:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        cfg_path = self.root / CONFIG_NAME
        if not cfg_path.exists():
            raise QlsError(f"No {CONFIG_NAME} in {self.root}. Run `qls init` first.")
        self.config = tomllib.loads(cfg_path.read_text(encoding="utf-8"))

    # -- discovery ----------------------------------------------------------

    @classmethod
    def find(cls, start: str | Path | None = None) -> "Project":
        """Locate the project: explicit path, $QLS_PROJECT, or walk up from cwd."""
        if start is None and os.environ.get("QLS_PROJECT"):
            start = os.environ["QLS_PROJECT"]
        p = Path(start or os.getcwd()).resolve()
        for cand in [p, *p.parents]:
            if (cand / CONFIG_NAME).exists():
                return cls(cand)
        raise QlsError(f"No {CONFIG_NAME} found in {p} or its parents. Run `qls init <folder>`.")

    @classmethod
    def init(cls, root: str | Path, name: str | None = None) -> "Project":
        root = Path(root).resolve()
        root.mkdir(parents=True, exist_ok=True)
        cfg = root / CONFIG_NAME
        if not cfg.exists():
            cfg.write_text(DEFAULT_CONFIG.format(name=name or root.name), encoding="utf-8")
        for d in ("context", "sources", "corpus/docs", "runs", "reports", "exports"):
            (root / d).mkdir(parents=True, exist_ok=True)
        for fname, body in CONTEXT_FILES.items():
            f = root / "context" / fname
            if not f.exists():
                f.write_text(body, encoding="utf-8")
        pi_models = root / "pi" / "models.json"
        if not pi_models.exists():
            pi_models.parent.mkdir(exist_ok=True)
            pi_models.write_text(PI_MODELS, encoding="utf-8")
        return cls(root)

    # -- config helpers -----------------------------------------------------

    def cfg(self, section: str, key: str, default: Any = None) -> Any:
        return self.config.get(section, {}).get(key, default)

    # -- context ------------------------------------------------------------

    def context_files(self) -> dict[str, str]:
        d = self.root / "context"
        if not d.exists():
            return {}
        return {p.name: p.read_text(encoding="utf-8") for p in sorted(d.glob("*.md"))}

    def context_text(self) -> str:
        parts = []
        for name, body in self.context_files().items():
            parts.append(f"<context file=\"{name}\">\n{body.strip()}\n</context>")
        return "\n\n".join(parts)

    def context_hashes(self) -> dict[str, str]:
        return {name: sha256_text(body) for name, body in self.context_files().items()}

    # -- corpus -------------------------------------------------------------

    @property
    def docs_dir(self) -> Path:
        return self.root / "corpus" / "docs"

    def doc_ids(self) -> list[str]:
        return sorted(p.stem for p in self.docs_dir.glob("*.json"))

    def doc(self, doc_id: str) -> dict:
        p = self.docs_dir / f"{doc_id}.json"
        if not p.exists():
            raise QlsError(f"Unknown document {doc_id!r}. Known: {', '.join(self.doc_ids()) or 'none'}")
        return read_json(p)

    def docs(self) -> list[dict]:
        return [self.doc(d) for d in self.doc_ids()]

    def save_doc(self, doc: dict) -> None:
        write_json(self.docs_dir / f"{doc['id']}.json", doc)

    def segment(self, seg_id: str) -> dict:
        doc_id = seg_id.split(":", 1)[0]
        for s in self.doc(doc_id)["segments"]:
            if s["id"] == seg_id:
                return s
        raise QlsError(f"Unknown segment {seg_id!r}")

    def corpus_hash(self) -> str:
        h = hashlib.sha256()
        for d in self.doc_ids():
            h.update((self.docs_dir / f"{d}.json").read_bytes())
        return "sha256:" + h.hexdigest()

    # -- runs ---------------------------------------------------------------

    @property
    def runs_dir(self) -> Path:
        return self.root / "runs"

    def run_ids(self) -> list[str]:
        return sorted(p.name for p in self.runs_dir.iterdir() if (p / "manifest.json").exists()) if self.runs_dir.exists() else []

    def run(self, run_id: str) -> "Run":
        r = Run(self, run_id)
        if not r.exists():
            raise QlsError(f"Unknown run {run_id!r}. Known: {', '.join(self.run_ids()) or 'none'}")
        return r

    def new_run(self, run_id: str | None, kind: str, parent: str | None = None, replace_unfinished: bool = False,
                **manifest: Any) -> "Run":
        run_id = run_id or f"{kind}-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
        if not NAME_RE.match(run_id):
            raise QlsError(f"Invalid run name {run_id!r}: use letters, digits, '-', '_' or '.'")
        r = Run(self, run_id)
        if r.exists() and replace_unfinished and r.manifest().get("status") in ("waiting", "failed", "running"):
            import shutil

            shutil.rmtree(r.dir)  # an unfinished attempt is redone from scratch (answers are cached)
        if r.exists():
            raise QlsError(f"Run {run_id!r} already exists.")
        r.dir.mkdir(parents=True)
        r.write_manifest({
            "run_id": run_id,
            "kind": kind,
            "parent": parent,
            "created": now(),
            "corpus_hash": self.corpus_hash(),
            "context_hashes": self.context_hashes(),
            **manifest,
        })
        r.save_state(empty_state(run_id))
        return r


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


def empty_state(run_id: str) -> dict:
    return {
        "run": run_id,
        "counters": {"c": 0, "q": 0, "t": 0, "a": 0, "m": 0},
        "concepts": {},
        "quotes": {},
        "themes": {},
        "dimensions": {},
        "memos": {},
    }


@dataclass
class Run:
    project: Project
    id: str

    @property
    def dir(self) -> Path:
        return self.project.runs_dir / self.id

    def exists(self) -> bool:
        return (self.dir / "manifest.json").exists()

    # manifest
    def manifest(self) -> dict:
        return read_json(self.dir / "manifest.json")

    def write_manifest(self, m: dict) -> None:
        write_json(self.dir / "manifest.json", m)

    def update_manifest(self, **kw: Any) -> None:
        m = self.manifest()
        m.update(kw)
        self.write_manifest(m)

    # state
    def state(self) -> dict:
        return read_json(self.dir / "state.json")

    def save_state(self, s: dict) -> None:
        write_json(self.dir / "state.json", s)

    # decisions
    def append_decision(self, rec: dict) -> None:
        with open(self.dir / "decisions.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def decisions(self) -> list[dict]:
        p = self.dir / "decisions.jsonl"
        if not p.exists():
            return []
        return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
