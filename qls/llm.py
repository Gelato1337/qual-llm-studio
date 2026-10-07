"""Model access for the fixed pipeline stages (coding, consolidation).

Providers:
  anthropic  Claude via the official SDK, structured JSON output.
  openai     any OpenAI-compatible endpoint (OpenAI, OpenRouter, vLLM, Ollama).
  mock       deterministic, offline; for tests and dry runs.

Every call returns the parsed JSON plus metadata (requested vs served model,
token usage, parameters) that the stage writes into the run manifest.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

from .util import QlsError


class LLMError(QlsError):
    pass


@dataclass
class LLMResult:
    data: Any
    text: str
    meta: dict = field(default_factory=dict)


def make_llm(cfg: dict) -> "LLM":
    provider = cfg.get("provider", "anthropic")
    if provider == "anthropic":
        return AnthropicLLM(cfg)
    if provider == "openai":
        return OpenAILLM(cfg)
    if provider == "mock":
        return MockLLM(cfg)
    if provider == "external":
        return ExternalLLM(cfg)
    raise QlsError(f"Unknown llm.provider {provider!r} (anthropic | openai | external | mock)")


class LLM:
    def __init__(self, cfg: dict):
        self.cfg = dict(cfg)

    def describe(self) -> dict:
        keys = ("provider", "model", "effort", "fallbacks", "base_url", "temperature", "seed", "max_tokens")
        return {k: self.cfg[k] for k in keys if k in self.cfg and self.cfg[k] not in ("", None)}

    def json(self, system: str, user: str, schema: dict, name: str) -> LLMResult:  # pragma: no cover
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------


class AnthropicLLM(LLM):
    FALLBACK_BETA = "server-side-fallback-2026-07-01"

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        try:
            import anthropic
        except ImportError as exc:
            raise QlsError("pip install 'qual-llm-studio[anthropic]' to use provider = \"anthropic\"") from exc
        self._anthropic = anthropic
        self.client = anthropic.Anthropic()

    def json(self, system: str, user: str, schema: dict, name: str) -> LLMResult:
        a = self._anthropic
        kwargs: dict[str, Any] = {
            "model": self.cfg.get("model", "claude-opus-5-5"),
            "max_tokens": int(self.cfg.get("max_tokens", 64000)),
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "output_config": {
                "effort": self.cfg.get("effort", "high"),
                "format": {"type": "json_schema", "schema": schema},
            },
        }
        t0 = time.time()
        try:
            if self.cfg.get("fallbacks", True):
                stream = self.client.beta.messages.stream(betas=[self.FALLBACK_BETA], fallbacks="default", **kwargs)
            else:
                stream = self.client.messages.stream(**kwargs)
            with stream as s:
                msg = s.get_final_message()
        except a.AuthenticationError as exc:
            raise LLMError("Anthropic authentication failed. Set ANTHROPIC_API_KEY or run `ant auth login`.") from exc
        except a.BadRequestError as exc:
            raise LLMError(f"Anthropic rejected the request: {exc.message}") from exc
        except a.APIStatusError as exc:
            raise LLMError(f"Anthropic API error {exc.status_code}: {exc.message}") from exc
        except a.APIConnectionError as exc:
            raise LLMError(f"Could not reach the Anthropic API: {exc}") from exc

        meta = {
            "provider": "anthropic",
            "model_requested": kwargs["model"],
            "model_served": msg.model,
            "stop_reason": msg.stop_reason,
            "input_tokens": msg.usage.input_tokens,
            "output_tokens": msg.usage.output_tokens,
            "request_id": getattr(msg, "_request_id", None),
            "elapsed_s": round(time.time() - t0, 1),
        }
        if msg.stop_reason == "refusal":
            details = getattr(msg, "stop_details", None)
            raise LLMError(f"Model refused ({getattr(details, 'category', None)}): {getattr(details, 'explanation', '')}")
        if msg.stop_reason == "max_tokens":
            raise LLMError(f"Output hit max_tokens ({kwargs['max_tokens']}). Raise llm.max_tokens or split the input.")
        text = "".join(b.text for b in msg.content if b.type == "text")
        return LLMResult(parse_json(text), text, meta)


# ---------------------------------------------------------------------------
# OpenAI-compatible (OpenAI, OpenRouter, vLLM, Ollama, ...)
# ---------------------------------------------------------------------------


class OpenAILLM(LLM):
    def __init__(self, cfg: dict):
        super().__init__(cfg)
        try:
            import openai
        except ImportError as exc:
            raise QlsError("pip install 'qual-llm-studio[openai]' to use provider = \"openai\"") from exc
        self._openai = openai
        key = os.environ.get(cfg.get("api_key_env") or "OPENAI_API_KEY") or "not-needed"
        self.client = openai.OpenAI(base_url=cfg.get("base_url") or None, api_key=key)

    def json(self, system: str, user: str, schema: dict, name: str) -> LLMResult:
        o = self._openai
        base: dict[str, Any] = {
            "model": self.cfg["model"],
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": float(self.cfg.get("temperature", 0.0)),
        }
        if self.cfg.get("seed") is not None:
            base["seed"] = int(self.cfg["seed"])
        if self.cfg.get("max_tokens"):
            base["max_tokens"] = int(self.cfg["max_tokens"])
        formats = [
            {"type": "json_schema", "json_schema": {"name": name, "schema": schema, "strict": True}},
            {"type": "json_object"},
        ]
        t0 = time.time()
        last_exc: Exception | None = None
        for fmt in formats:
            try:
                resp = self.client.chat.completions.create(response_format=fmt, **base)
                break
            except o.BadRequestError as exc:  # endpoint may not support json_schema
                last_exc = exc
            except o.APIConnectionError as exc:
                raise LLMError(f"Could not reach {self.cfg.get('base_url') or 'OpenAI'}: {exc}") from exc
            except o.APIStatusError as exc:
                raise LLMError(f"API error {exc.status_code}: {exc.message}") from exc
        else:
            raise LLMError(f"Endpoint rejected structured output: {last_exc}")
        choice = resp.choices[0]
        text = choice.message.content or ""
        meta = {
            "provider": "openai",
            "base_url": self.cfg.get("base_url") or "https://api.openai.com/v1",
            "model_requested": base["model"],
            "model_served": resp.model,
            "system_fingerprint": getattr(resp, "system_fingerprint", None),
            "finish_reason": choice.finish_reason,
            "input_tokens": getattr(resp.usage, "prompt_tokens", None),
            "output_tokens": getattr(resp.usage, "completion_tokens", None),
            "response_format": fmt["type"],
            "elapsed_s": round(time.time() - t0, 1),
        }
        if choice.finish_reason == "length":
            raise LLMError("Output truncated (finish_reason=length). Raise llm.max_tokens.")
        return LLMResult(parse_json(text), text, meta)


# ---------------------------------------------------------------------------
# External: requests and answers as files
# ---------------------------------------------------------------------------


class PendingAnswers(LLMError):
    """One or more model requests are waiting for an answer file."""

    def __init__(self, requests: list[str]):
        self.requests = list(requests)
        super().__init__(f"{len(self.requests)} model request(s) waiting for an answer")


class ExternalLLM(LLM):
    """The model is whoever answers the request files.

    Each call writes <dir>/requests/<name>-<hash>.md (system prompt, input,
    JSON schema). The answer goes to <dir>/answers/<name>-<hash>.json.
    Rerunning the same command picks answers up. The hash covers the full
    request, so a changed prompt or input never reuses an old answer.

    Use it when no API key is available but an assistant is (Claude Code or
    Cowork answering with its own model), or to replay recorded answers.
    Set `model` to say who answers, e.g. "external:claude-code-session".
    """

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        from pathlib import Path

        self.dir = Path(cfg["external_dir"])

    def json(self, system: str, user: str, schema: dict, name: str) -> LLMResult:
        import hashlib

        payload = json.dumps({"name": name, "system": system, "user": user, "schema": schema}, sort_keys=True, ensure_ascii=False)
        key = f"{name}-{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]}"
        ans = self.dir / "answers" / f"{key}.json"
        if ans.exists():
            text = ans.read_text(encoding="utf-8")
            return LLMResult(parse_json(text), text, {"provider": "external", "model_requested": self.cfg.get("model"),
                                                      "model_served": self.cfg.get("model"), "answer": f"answers/{key}.json"})
        req = self.dir / "requests" / f"{key}.md"
        if not req.exists():
            req.parent.mkdir(parents=True, exist_ok=True)
            (self.dir / "answers").mkdir(parents=True, exist_ok=True)
            req.write_text(
                f"# Model request `{key}`\n\nAnswer with JSON only, matching the schema at the end, and save it as\n"
                f"`answers/{key}.json` next to this folder.\n\n## System prompt\n\n{system}\n\n## Input\n\n{user}\n\n"
                f"## JSON schema\n\n```json\n{json.dumps(schema, indent=1, ensure_ascii=False)}\n```\n",
                encoding="utf-8",
            )
        raise PendingAnswers([str(req)])


# ---------------------------------------------------------------------------
# Mock (offline, deterministic)
# ---------------------------------------------------------------------------

_SEG_TAG = re.compile(r'<segment id="([^"]+)"[^>]*>\n?(.*?)\n?</segment>', re.S)
_CONCEPT_TAG = re.compile(r'<concept id="([^"]+)" label="([^"]*)"')
_STOP = set("the a an and or but of to in on for with is are was were it that this i we you they be have has do".split())


class MockLLM(LLM):
    """Deterministic stand-in so the pipeline can be exercised without keys.

    code:        one concept per segment, quoting its first sentence.
    consolidate: merges concepts whose labels share the same first two content words.
    """

    def json(self, system: str, user: str, schema: dict, name: str) -> LLMResult:
        if name == "first_order_concepts":
            data = self._code(user, with_memo="memo" in schema["properties"]["concepts"]["items"]["properties"])
        elif name == "concept_merges":
            data = self._consolidate(user)
        elif name == "gioia_grouping":
            data = self._group(user)
        elif name in ("theme_check", "dimension_check"):
            data = {"label_ok": True, "label": "", "definition": "", "misfits": [], "reason": "fits (mock)"}
        elif name == "oneshot":
            coded = self._code(user, with_memo="memo" in schema["properties"]["concepts"]["items"]["properties"])
            for i, c in enumerate(coded["concepts"], 1):
                c["key"] = f"k{i}"
            data = {"concepts": coded["concepts"], **self._group_keys([(c["key"], c["label"]) for c in coded["concepts"]]),
                    "notes": "mock one-shot"}
        else:
            raise LLMError(f"mock provider has no behaviour for {name!r}")
        return LLMResult(data, json.dumps(data), {"provider": "mock", "model_requested": "mock", "model_served": "mock"})

    @staticmethod
    def _words(s: str) -> list[str]:
        return [w for w in re.findall(r"[\wÅÄÖåäö]+", s.lower()) if w not in _STOP and len(w) > 2]

    def _group_keys(self, items: list[tuple[str, str]]) -> dict:
        groups: dict[str, list[str]] = {}
        for cid, label in items:
            groups.setdefault((self._words(label) or ["other"])[0], []).append(cid)
        themes = [{"key": f"T{i}", "label": f"About {w}", "definition": f"Concepts starting with {w} (mock)",
                   "concept_ids": ids, "reason": "same first word (mock)"} for i, (w, ids) in enumerate(sorted(groups.items()), 1)]
        dims = [{"label": "Everything (mock)", "definition": "all themes", "theme_keys": [t["key"] for t in themes], "reason": "mock"}]
        return {"themes": themes, "dimensions": dims}

    def _group(self, user: str) -> dict:
        items = re.findall(r'<concept id="([^"]+)" label="([^"]*)"', user) or re.findall(r"^- (c\d+): (.*)$", user, re.M)
        return {**self._group_keys(items), "notes": "mock grouping"}

    def _code(self, user: str, with_memo: bool = False) -> dict:
        concepts = []
        for seg_id, text in _SEG_TAG.findall(user):
            first = re.split(r"(?<=[.!?])\s+", text.strip())[0]
            quote = " ".join(first.split()[:25])
            words = self._words(first)[:4]
            if not words:
                continue
            c = {
                "label": " ".join(words),
                "description": f"The informant says: {first[:120]}",
                "quotes": [{"segment_id": seg_id, "quote": quote}],
            }
            if with_memo:
                c["memo"] = {"meaning_here": f"mock meaning of {words[0]}", "not_this": "", "conditions": "", "doubt": ""}
            concepts.append(c)
        return {"concepts": concepts}

    def _consolidate(self, user: str) -> dict:
        groups: dict[str, list[str]] = {}
        labels: dict[str, str] = {}
        for cid, label in _CONCEPT_TAG.findall(user):
            key = " ".join(self._words(label)[:2])
            groups.setdefault(key, []).append(cid)
            labels.setdefault(key, label)
        merges = [
            {"ids": ids, "label": labels[k], "description": f"Merged concepts about {k}.", "reason": "same first words (mock)"}
            for k, ids in groups.items() if k and len(ids) > 1
        ]
        return {"merges": merges}


# ---------------------------------------------------------------------------


def parse_json(text: str) -> Any:
    """Parse model JSON, tolerating code fences and leading/trailing prose."""
    s = text.strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s)
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    start = min([i for i in (s.find("{"), s.find("[")) if i >= 0], default=-1)
    if start >= 0:
        ends = [i + 1 for i in range(len(s) - 1, start, -1) if s[i] in "}]"][:50]
        for end in ends:
            try:
                return json.loads(s[start:end])
            except json.JSONDecodeError:
                continue
    raise LLMError(f"Model output is not valid JSON: {text[:300]!r}")
