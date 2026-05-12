"""
Ollama inference client.

Endpoints used in the app:
  * /api/chat        — recipe runs (json mode + streaming)
  * /api/pull        — Settings tab model pulls
  * /api/version     — health check / connection banner
  * /api/tags        — listing pulled models for the dropdown
  * /api/ps          — GPU usage signal for resource detection

Retry-with-doubled-tokens on empty responses is included because
thinking models burn the budget on <think> blocks and run out before
producing JSON.

`think` is three-state, mirroring the pattern used in our existing
elephant pipeline:
  * "low" / "medium" / "high"  — gpt-oss style effort
  * True / False               — explicit enable/disable for Qwen3 etc.
  * None                       — parameter omitted (standard models)
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

import requests

DEFAULT_HOST = "http://localhost:11434"
DEFAULT_TIMEOUT = 300
DEFAULT_NUM_CTX = 32000
DEFAULT_TEMPERATURE = 0.1
DEFAULT_MAX_TOKENS = 4000
DEFAULT_RETRIES = 3
DEFAULT_RETRY_DELAY = 5.0


@dataclass
class ChatResult:
    """Return value from a chat call. Keeps thinking output separate."""

    content: str
    thinking: str
    raw: dict


class OllamaError(RuntimeError):
    """Raised when Ollama is unreachable or returns an error we can't recover from."""


class OllamaClient:
    """Thin wrapper around Ollama's HTTP API.

    Stateless apart from the host address and default model — safe to share
    between pipeline modules.
    """

    def __init__(self, host: str = DEFAULT_HOST, default_model: str | None = None):
        self.host = host.rstrip("/")
        self.default_model = default_model

    # ---- health & lifecycle ------------------------------------------------

    def is_alive(self, timeout: float = 2.0) -> bool:
        try:
            r = requests.get(f"{self.host}/api/tags", timeout=timeout)
            return r.status_code == 200
        except requests.RequestException:
            return False

    def version(self, timeout: float = 2.0) -> str | None:
        """Return Ollama server version string, or None if unreachable."""
        try:
            r = requests.get(f"{self.host}/api/version", timeout=timeout)
            r.raise_for_status()
            return r.json().get("version")
        except (requests.RequestException, ValueError):
            return None

    def list_models(self) -> list[str]:
        r = requests.get(f"{self.host}/api/tags", timeout=10)
        r.raise_for_status()
        return [m["name"] for m in r.json().get("models", [])]

    def has_model(self, model: str) -> bool:
        """True if the named model is in `ollama list`. Used to verify pulls.

        Ollama tags can include or omit the ":latest" suffix. We check both
        the exact name and the bare name when the user typed ":latest"
        explicitly, so "qwen3:4b" matches "qwen3:4b" and "llama3" matches
        "llama3:latest"."""
        try:
            available = self.list_models()
        except requests.RequestException:
            return False
        if model in available:
            return True
        if ":" not in model and f"{model}:latest" in available:
            return True
        return False

    def pull(self, model: str):
        """Pull a model. Yields progress dicts.

        Note: a successful HTTP stream does NOT guarantee the model is
        actually installed afterwards. Some failures (no GPU, bad name,
        out of disk) come back as `{"error": "..."}` events without
        raising HTTP errors. Callers should verify with `has_model()`
        after the stream ends.
        """
        with requests.post(
            f"{self.host}/api/pull",
            json={"model": model, "stream": True},
            stream=True,
            timeout=None,
        ) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line:
                    continue
                import json
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue

    def pull_and_verify(self, model: str):
        """Pull model and yield progress; final yield is a verification dict.

        Yields the raw pull events as they arrive. After the stream ends,
        yields one synthetic event:
          {"_qls_status": "ok",       "model": ...}      if the model exists after pull
          {"_qls_status": "failed",   "model": ..., "error": ...} if not

        This lets the UI show progress AND surface real failures.
        """
        last_error: str | None = None
        for event in self.pull(model):
            if isinstance(event, dict) and event.get("error"):
                last_error = event["error"]
            yield event

        if self.has_model(model):
            yield {"_qls_status": "ok", "model": model}
        else:
            err = last_error or "model not present after pull (unknown reason)"
            yield {"_qls_status": "failed", "model": model, "error": err}

    # ---- chat --------------------------------------------------------------

    def chat(
        self,
        prompt: str | None = None,
        *,
        messages: list[dict] | None = None,
        system: str | None = None,
        model: str | None = None,
        json_mode: bool = False,
        temperature: float = DEFAULT_TEMPERATURE,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        num_ctx: int = DEFAULT_NUM_CTX,
        think: Any = None,
        max_retries: int = DEFAULT_RETRIES,
        retry_delay: float = DEFAULT_RETRY_DELAY,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> ChatResult:
        """Send a chat request to Ollama's /api/chat endpoint.

        Provide either `prompt` (single user message, optionally with `system`),
        or `messages` (full conversation list). Mutually exclusive.
        """
        model = model or self.default_model
        if not model:
            raise ValueError("No model specified and no default set on client.")

        if messages is None:
            if prompt is None:
                raise ValueError("Provide either `prompt` or `messages`.")
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
                "num_ctx": num_ctx,
            },
        }
        if json_mode:
            payload["format"] = "json"
        # Three-state think: None omits, anything else passes through.
        if think is not None:
            payload["think"] = think

        last_exc: Exception | None = None
        for attempt in range(max_retries):
            try:
                r = requests.post(f"{self.host}/api/chat", json=payload, timeout=timeout)
                r.raise_for_status()
                data = r.json()
                msg = data.get("message", {})
                content = msg.get("content", "") or ""
                thinking = msg.get("thinking", "") or ""
                # Some thinking models return content in `thinking` only when
                # they exhaust the budget. Promote it so callers see something.
                if not content.strip() and thinking.strip():
                    content = thinking
                return ChatResult(content=content, thinking=thinking, raw=data)
            except (requests.RequestException, ValueError) as exc:
                last_exc = exc
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
        raise OllamaError(f"Ollama chat failed after {max_retries} attempts: {last_exc}")

    def chat_with_token_doubling(
        self,
        prompt: str,
        *,
        validator,
        model: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        max_doublings: int = 3,
        **kwargs,
    ) -> ChatResult:
        """Call chat, and if `validator(content)` returns False, retry with
        doubled `max_tokens` up to `max_doublings` times.

        Used when extracting structured JSON from thinking models that
        sometimes burn the budget on <think> and never reach the JSON.
        """
        current_tokens = max_tokens
        result = self.chat(prompt, model=model, max_tokens=current_tokens, **kwargs)
        for _ in range(max_doublings):
            if validator(result.content):
                return result
            current_tokens *= 2
            result = self.chat(prompt, model=model, max_tokens=current_tokens, **kwargs)
        return result

    def chat_stream(
        self,
        prompt: str | None = None,
        *,
        messages: list[dict] | None = None,
        system: str | None = None,
        model: str | None = None,
        temperature: float = DEFAULT_TEMPERATURE,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        num_ctx: int = DEFAULT_NUM_CTX,
        think: Any = None,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        """Stream tokens from /api/chat. Yields content chunks as strings.

        Used by the Chat tab so a slow model doesn't make the UI feel frozen.
        Caller iterates and concatenates tokens. On error, yields an
        ERROR-prefixed final message instead of raising — the chat UI is
        a more forgiving place than the recipe runner."""
        import json as _json

        model = model or self.default_model
        if not model:
            yield "[ERROR] No model selected."
            return

        if messages is None:
            if prompt is None:
                yield "[ERROR] No prompt provided."
                return
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
                "num_ctx": num_ctx,
            },
        }
        if think is not None:
            payload["think"] = think

        try:
            with requests.post(
                f"{self.host}/api/chat",
                json=payload,
                stream=True,
                timeout=timeout,
            ) as r:
                r.raise_for_status()
                for line in r.iter_lines():
                    if not line:
                        continue
                    try:
                        data = _json.loads(line)
                    except _json.JSONDecodeError:
                        continue
                    msg = data.get("message", {})
                    chunk = msg.get("content") or ""
                    if chunk:
                        yield chunk
                    if data.get("done"):
                        break
        except requests.RequestException as exc:
            yield f"\n\n[ERROR] {exc}"
