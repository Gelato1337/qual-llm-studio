"""Ingestion: files -> text -> speaker turns -> informant segments.

Text formats (.txt, .md, .srt, .vtt) are read directly. Everything else
(PDF, DOCX, ODT, HTML, audio, video, ...) goes through Docling.

The transcript parser recognises "Speaker: text" lines (also **Speaker:**
from Markdown exports and VTT <v Speaker> tags), decides who the interviewer
is, and turns every informant turn into one or more segments. Each segment
remembers the interviewer question it answers, which is shown as context and
used to flag concepts that only echo the interview guide (AMCIS challenge #2).
"""

from __future__ import annotations

import re
import shutil
from collections import Counter
from pathlib import Path

from .project import Project, QlsError, now, sha256_bytes

TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".srt", ".vtt"}
INTERVIEWER_HINT = re.compile(r"interview|haastattel|moderator|researcher|tutkija|fasilit", re.I)

_TS = r"\[?\(?\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d+)?\)?\]?"
_TIMESTAMP_PREFIX = re.compile(rf"^\s*{_TS}\s*(?:-->\s*{_TS})?\s*")
_SPEAKER = re.compile(
    r"^\s*(?:\*\*|__)?"
    r"(?P<label>[A-ZÅÄÖ][\wÅÄÖåäö.\-]*(?: [\wÅÄÖåäö.\-]+){0,3}|[A-Za-z]\d{0,3})"
    r"(?:\*\*|__)?\s*[:：](?:\*\*|__)?\s+(?P<text>\S.*)$"
)
_VTT_VOICE = re.compile(r"^\s*<v\s+([^>]+)>(.*?)(?:</v>)?\s*$")


# ---------------------------------------------------------------------------
# Reading files
# ---------------------------------------------------------------------------


def read_text(path: Path) -> tuple[str, str]:
    """Return (text, parser_name)."""
    suffix = path.suffix.lower()
    if suffix in TEXT_SUFFIXES:
        raw = path.read_text(encoding="utf-8", errors="replace")
        if suffix in (".srt", ".vtt"):
            return _strip_subtitles(raw), "subtitles"
        return raw, "text"
    return _docling_text(path), "docling"


def _strip_subtitles(raw: str) -> str:
    out = []
    for line in raw.splitlines():
        s = line.strip()
        if not s or s == "WEBVTT" or s.isdigit() or "-->" in s:
            continue
        m = _VTT_VOICE.match(s)
        if m:
            s = f"{m.group(1).strip()}: {m.group(2).strip()}"
        out.append(s)
    return "\n".join(out)


def _docling_text(path: Path) -> str:
    try:
        from docling.datamodel.base_models import InputFormat
        from docling.document_converter import AudioFormatOption, DocumentConverter
    except ImportError as exc:
        raise QlsError(
            f"{path.name}: Docling is needed for {path.suffix} files. "
            "Install with: pip install 'qual-llm-studio[docling]'"
        ) from exc

    format_options = {}
    try:  # audio needs the ASR pipeline wired explicitly (docling[asr] extra)
        from docling.datamodel import asr_model_specs
        from docling.datamodel.pipeline_options import AsrPipelineOptions
        from docling.pipeline.asr_pipeline import AsrPipeline

        opts = AsrPipelineOptions()
        opts.asr_options = asr_model_specs.WHISPER_TURBO
        format_options[InputFormat.AUDIO] = AudioFormatOption(pipeline_cls=AsrPipeline, pipeline_options=opts)
    except ImportError:
        pass

    result = DocumentConverter(format_options=format_options).convert(str(path))
    return result.document.export_to_markdown()


# ---------------------------------------------------------------------------
# Transcript parsing
# ---------------------------------------------------------------------------


def parse_turns(text: str) -> list[tuple[str | None, str]]:
    """Split text into (speaker, text) turns. speaker is None if unlabelled."""
    lines = [_TIMESTAMP_PREFIX.sub("", ln).rstrip() for ln in text.splitlines()]
    candidates = Counter(m.group("label").strip() for ln in lines if (m := _SPEAKER.match(ln)))
    # A label must recur to count as a speaker; "Note: ..." once is just prose.
    speakers = {lab for lab, n in candidates.items() if n >= 2}

    turns: list[list] = []
    for ln in lines:
        m = _SPEAKER.match(ln)
        if m and m.group("label").strip() in speakers:
            turns.append([m.group("label").strip(), [m.group("text").strip()]])
        elif ln.strip():
            if not turns:
                turns.append([None, []])
            turns[-1][1].append(ln.strip())
        elif turns and turns[-1][1] and turns[-1][1][-1] != "":
            turns[-1][1].append("")  # paragraph break inside a turn
    out = []
    for spk, parts in turns:
        body = "\n".join(parts).strip()
        body = re.sub(r"\n{2,}", "\n\n", body)
        if body:
            out.append((spk, body))
    if not speakers:
        # No speaker labels: treat each paragraph as an informant turn.
        paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        return [(None, p) for p in paras]
    return out


def assign_roles(turns: list[tuple[str | None, str]], interviewer_labels: list[str]) -> dict[str, str]:
    labels = [s for s, _ in turns if s]
    if not labels:
        return {}
    wanted = {x.lower() for x in interviewer_labels}
    roles = {s: ("interviewer" if s.lower() in wanted or INTERVIEWER_HINT.search(s) else "informant") for s in set(labels)}
    if "interviewer" not in roles.values() and len(roles) >= 2:
        # Heuristic: the interviewer asks the most questions per turn.
        def q_ratio(spk: str) -> float:
            ts = [t for s, t in turns if s == spk]
            return sum(t.rstrip().endswith("?") for t in ts) / max(len(ts), 1)

        roles[max(roles, key=q_ratio)] = "interviewer"
    return roles


def _split_long(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    units = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(units) == 1:
        units = re.split(r"(?<=[.!?])\s+", text)
    chunks, cur = [], ""
    for u in units:
        if cur and len(cur) + len(u) + 1 > max_chars:
            chunks.append(cur.strip())
            cur = ""
        cur += ("\n\n" if "\n\n" in text else " ") + u if cur else u
    if cur.strip():
        chunks.append(cur.strip())
    return chunks


def build_document(doc_id: str, text: str, interviewer_labels: list[str], max_segment_chars: int) -> dict:
    turns_raw = parse_turns(text)
    roles = assign_roles(turns_raw, interviewer_labels)
    turns, segments = [], []
    last_q = None
    for i, (spk, body) in enumerate(turns_raw, start=1):
        # Unlabelled text in a labelled transcript (title, preamble) is kept but never coded.
        role = roles.get(spk, "informant") if spk else ("meta" if roles else "informant")
        tid = f"{doc_id}:t{i:03d}"
        turns.append({"id": tid, "speaker": spk or role, "role": role, "text": body})
        if role == "meta":
            continue
        if role == "interviewer":
            last_q = {"turn": tid, "text": body}
            continue
        for chunk in _split_long(body, max_segment_chars):
            segments.append({
                "id": f"{doc_id}:s{len(segments) + 1:03d}",
                "turn": tid,
                "speaker": spk or "informant",
                "text": chunk,
                "question": last_q["text"] if last_q else None,
                "question_turn": last_q["turn"] if last_q else None,
            })
    return {"speakers": roles, "turns": turns, "segments": segments}


def safe_id(stem: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_-]+", "-", stem).strip("-") or "doc"
    return s[:48]


def ingest_file(project: Project, path: str | Path, doc_id: str | None = None,
                participant: str | None = None, interviewer: list[str] | None = None) -> dict:
    path = Path(path)
    if not path.exists():
        raise QlsError(f"File not found: {path}")
    doc_id = safe_id(doc_id or path.stem)
    if doc_id in project.doc_ids():
        raise QlsError(f"Document {doc_id!r} already ingested. Use --id to give it another name.")
    raw = path.read_bytes()
    text, parser = read_text(path)
    labels = list(project.cfg("transcripts", "interviewer_labels", [])) + list(interviewer or [])
    body = build_document(doc_id, text, labels, int(project.cfg("transcripts", "max_segment_chars", 2000)))
    if not body["segments"]:
        raise QlsError(f"{path.name}: no informant text found. Check the speaker labels (--interviewer).")
    dest = project.root / "sources" / path.name
    if dest.resolve() != path.resolve():
        shutil.copy2(path, dest)
    doc = {
        "id": doc_id,
        "participant": participant or doc_id,
        "source": f"sources/{path.name}",
        "source_sha256": sha256_bytes(raw),
        "parser": parser,
        "ingested": now(),
        **body,
    }
    project.save_doc(doc)
    return doc
