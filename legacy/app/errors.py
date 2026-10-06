"""Standardized error surfacing.

Three rules:
  1. Recoverable issues -> gr.Warning (toast, doesn't halt)
  2. Blocking issues    -> gr.Error   (toast, halts the handler)
  3. Everything goes to runs/errors.log with full traceback so we can
     debug after the fact.

The @safe_handler decorator wraps a Gradio click handler so any uncaught
exception becomes a gr.Error toast plus a logged traceback, instead of
the silent dead-handler behavior that's so easy to miss.
"""

from __future__ import annotations

import functools
import logging
import traceback
from pathlib import Path
from typing import Callable

import gradio as gr

# The log file lives alongside the runs directory so it doesn't pollute
# project root. Set LOG_PATH from app startup before using.
LOG_PATH: Path | None = None


def configure(runs_dir: str | Path) -> None:
    """Wire logging to runs_dir/errors.log. Idempotent."""
    global LOG_PATH
    runs_dir = Path(runs_dir)
    runs_dir.mkdir(parents=True, exist_ok=True)
    LOG_PATH = runs_dir / "errors.log"

    root = logging.getLogger()
    # Don't double-add our handler on hot-reload
    for h in root.handlers:
        if getattr(h, "_qls_handler", False):
            return
    handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    ))
    handler._qls_handler = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def safe_handler(fn: Callable) -> Callable:
    """Decorator that turns any unhandled exception into a gr.Error toast.

    Use it on Gradio click/change handlers so users see a real toast
    instead of the handler silently dying. The full traceback goes to
    the log file.
    """
    log = logging.getLogger(fn.__module__)

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except gr.Error:
            raise   # explicit gr.Error already has a clean message
        except Exception as exc:  # noqa: BLE001
            tb = traceback.format_exc()
            log.error("handler %s failed: %s\n%s", fn.__name__, exc, tb)
            # User-visible message: short, no traceback
            short = f"{type(exc).__name__}: {exc}"
            raise gr.Error(short)
    return wrapper


def warn(msg: str) -> None:
    """Non-blocking toast warning."""
    gr.Warning(msg)


def error(msg: str) -> None:
    """Blocking error toast (raises)."""
    raise gr.Error(msg)
