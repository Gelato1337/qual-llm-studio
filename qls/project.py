"""Project folder: settings, context files, the store, and ingestion.

    qls.toml          settings
    context/*.md      research question and study context (hashed into every run)
    qls.db            sources, runs, event log, graph view (SQLite)
    sources/          original files
    corpus/text/      extracted text per source (Docling output is cached here)
"""

from __future__ import annotations

import json
import os
import shutil
import tomllib
from pathlib import Path

from .ingest import build_document, read_text, safe_id
from .store import Store
from .util import QlsError, sha256_bytes, sha256_text

CONFIG = "qls.toml"

DEFAULT_CONFIG = """\
[project]
name = "{name}"
label_language = "English"

[transcripts]
interviewer_labels = ["I", "Q", "H", "Interviewer", "Haastattelija", "Moderator"]
max_segment_chars = 2000
"""

CONTEXT = {
    "research_question.md": "# Research question\n\nThe question that guides coding.\n",
    "study_context.md": "# Study context\n\nSetting, informants, sampling, and what kind of concept the study looks for.\n",
}


class Project:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        if not (self.root / CONFIG).exists():
            raise QlsError(f"No {CONFIG} in {self.root}. Run `qls init`.")
        self.config = tomllib.loads((self.root / CONFIG).read_text(encoding="utf-8"))
        self._store: Store | None = None

    @classmethod
    def init(cls, root: str | Path, name: str | None = None) -> "Project":
        root = Path(root).resolve()
        root.mkdir(parents=True, exist_ok=True)
        if not (root / CONFIG).exists():
            (root / CONFIG).write_text(DEFAULT_CONFIG.format(name=name or root.name), encoding="utf-8")
        for d in ("context", "sources", "corpus/text", "reports", "exports"):
            (root / d).mkdir(parents=True, exist_ok=True)
        for f, body in CONTEXT.items():
            if not (root / "context" / f).exists():
                (root / "context" / f).write_text(body, encoding="utf-8")
        return cls(root)

    @classmethod
    def find(cls, start: str | Path | None = None) -> "Project":
        start = start or os.environ.get("QLS_PROJECT") or os.getcwd()
        p = Path(start).resolve()
        for cand in [p, *p.parents]:
            if (cand / CONFIG).exists():
                return cls(cand)
        raise QlsError(f"No {CONFIG} found in {p} or its parents. Run `qls init <folder>`.")

    @property
    def store(self) -> Store:
        if self._store is None:
            self._store = Store(self.root / "qls.db")
        return self._store

    def cfg(self, section: str, key: str, default=None):
        return self.config.get(section, {}).get(key, default)

    def context_files(self) -> dict[str, str]:
        d = self.root / "context"
        return {p.name: p.read_text(encoding="utf-8") for p in sorted(d.glob("*.md"))} if d.exists() else {}

    def context_text(self) -> str:
        return "\n\n".join(f'<context file="{n}">\n{b.strip()}\n</context>' for n, b in self.context_files().items())

    def context_hash(self) -> str:
        return sha256_text(json.dumps(self.context_files(), sort_keys=True))

    # -- ingestion --------------------------------------------------------------

    def ingest(self, path: str | Path, source_id: str | None = None, participant: str | None = None,
               interviewer: list[str] | None = None, title: str | None = None) -> dict:
        path = Path(path)
        if not path.exists():
            raise QlsError(f"File not found: {path}")
        sid = safe_id(source_id or path.stem)
        raw = path.read_bytes()
        cache, meta_f = self.root / "corpus" / "text" / f"{sid}.md", self.root / "corpus" / "text" / f"{sid}.json"
        if cache.exists() and meta_f.exists() and json.loads(meta_f.read_text())["source_sha256"] == sha256_bytes(raw):
            text, parser = cache.read_text(encoding="utf-8"), json.loads(meta_f.read_text())["parser"]
        else:
            text, parser = read_text(path)
            cache.write_text(text, encoding="utf-8")
            meta_f.write_text(json.dumps({"source_sha256": sha256_bytes(raw), "parser": parser}))
        labels = list(self.cfg("transcripts", "interviewer_labels", [])) + list(interviewer or [])
        doc = build_document(sid, text, labels, int(self.cfg("transcripts", "max_segment_chars", 2000)))
        if not doc["segments"]:
            raise QlsError(f"{path.name}: no informant text found; check speaker labels (--interviewer).")
        canonical, units = canonical_text(doc)
        dest = self.root / "sources" / path.name
        if dest.resolve() != path.resolve():
            shutil.copy2(path, dest)
        meta = {"participant": participant or sid, "file": f"sources/{path.name}", "file_sha256": sha256_bytes(raw),
                "parser": parser, "speakers": doc["speakers"]}
        version = self.store.add_source(sid, canonical, units, title or path.stem, meta)
        return {"id": sid, "version": version, "turns": len(doc["turns"]), "segments": len(doc["segments"]),
                "speakers": doc["speakers"], "parser": parser}


def canonical_text(doc: dict) -> tuple[str, list[dict]]:
    """Render turns as 'Speaker: text' blocks; every turn and segment becomes an offset range in that text."""
    parts, units, pos = [], [], 0
    turn_pos: dict[str, int] = {}
    for t in doc["turns"]:
        prefix = "" if t["role"] == "meta" else f"{t['speaker']}: "
        start = pos + len(prefix)
        parts.append(prefix + t["text"] + "\n\n")
        units.append({"id": t["id"], "kind": "turn", "role": t["role"], "speaker": t["speaker"],
                      "start": start, "end": start + len(t["text"])})
        turn_pos[t["id"]] = start
        pos += len(prefix) + len(t["text"]) + 2
    text = "".join(parts)
    cursor: dict[str, int] = {}
    for s in doc["segments"]:
        base = turn_pos[s["turn"]]
        turn_text = next(t["text"] for t in doc["turns"] if t["id"] == s["turn"])
        i = turn_text.find(s["text"], cursor.get(s["turn"], 0))
        if i < 0:
            raise QlsError(f"segment {s['id']} is not a slice of its turn")
        cursor[s["turn"]] = i + len(s["text"])
        units.append({"id": s["id"], "kind": "segment", "role": "informant", "speaker": s["speaker"], "turn": s["turn"],
                      "start": base + i, "end": base + i + len(s["text"]), "question": s.get("question"),
                      "question_turn": s.get("question_turn"), "question_gap": s.get("question_gap", 0),
                      "section": s.get("section")})
    return text, units
