"""LLM connection state — single source of truth for tabs.

Used by every handler that needs to talk to a model. Three pieces:

  * `LLMConfig` dataclass — host + default model + judge model + optimizer model
  * `make_client(cfg)` — build an OllamaClient (None if host unset)
  * `connection_status(cfg)` — short string + flag for the banner

The banner reads `connection_status()` and renders accordingly. Handlers
read the LLMConfig from gr.State and use the explicit model field they
need (default vs judge vs optimizer).

Important wiring rule: the inference_state dict is updated on EVERY model
dropdown change AND read at handler-execution time, not at handler-bind
time. So even if Gradio event ordering is a little weird, the click reads
the live state value.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .inference import DEFAULT_HOST, OllamaClient


@dataclass
class LLMConfig:
    host: str = DEFAULT_HOST
    default_model: str = ""
    judge_model: str = ""
    optimizer_model: str = ""

    def as_state(self) -> dict[str, Any]:
        """Convert to the dict shape the rest of the app uses in gr.State."""
        return {
            "host": self.host,
            "model": self.default_model,
            "default_model": self.default_model,
            "judge_model": self.judge_model,
            "optimizer_model": self.optimizer_model,
            "client": OllamaClient(host=self.host) if self.host else None,
        }

    @classmethod
    def from_state(cls, state: dict | None) -> "LLMConfig":
        if not state:
            return cls()
        return cls(
            host=state.get("host") or DEFAULT_HOST,
            default_model=state.get("model") or state.get("default_model") or "",
            judge_model=state.get("judge_model") or "",
            optimizer_model=state.get("optimizer_model") or "",
        )


def make_client(state: dict | None) -> OllamaClient | None:
    cfg = LLMConfig.from_state(state)
    if not cfg.host:
        return None
    return OllamaClient(host=cfg.host)


def connection_status(state: dict | None) -> tuple[str, bool]:
    """Return (banner_md, is_connected_with_model). The banner shows up
    persistently at the top of the UI. Empty string means "everything OK,
    no banner needed"."""
    cfg = LLMConfig.from_state(state)
    if not cfg.host:
        return ("⚠️ **No Ollama host configured.** Go to **Settings** to set one.", False)
    client = OllamaClient(host=cfg.host)
    if not client.is_alive(timeout=1.0):
        return (
            f"⚠️ **Ollama unreachable at `{cfg.host}`.** "
            "Start Ollama or check the host in **Settings**.",
            False,
        )
    if not cfg.default_model:
        return (
            "⚠️ **No model selected.** Pick a model in **Settings** "
            f"(connected to `{cfg.host}`).",
            False,
        )
    return ("", True)
