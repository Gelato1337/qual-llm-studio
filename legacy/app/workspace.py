"""Workspace state persistence.

Single source of truth for cross-tab state that needs to survive page
refresh and be inspectable on disk. Layout:

    runs/_workspace/
        state.json              # small JSON-able state (host, model, picks, params)
        loaded_sources.parquet  # the loaded-sources DataFrame (concat of all sources, before structure)
        sources_meta.json       # metadata about loaded sources (names, kinds, tabular columns)
        docs.parquet            # the final ingested docs DataFrame (after structure + segment)
        chat_history.json       # free-chat conversation
        recipe_tests.json       # recent recipe-test results

The Workspace class hides the file layout. Tabs read/write through it.

Resilience principle: any individual artifact missing or corrupt does
NOT prevent the workspace from loading. We log and continue with empty
defaults. The workspace must never block app startup.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

import pandas as pd

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pure-data state objects (JSON-serializable)
# ---------------------------------------------------------------------------


@dataclass
class SourceMeta:
    """Lightweight description of one loaded source. Persists to JSON.

    We don't persist the full DataFrame here — that lives in
    `loaded_sources.parquet`. This is just enough to rebuild the UI's
    list of loaded sources after a refresh.
    """
    name: str
    kind: str                    # "tabular" | "documents"
    n_rows: int                  # rows (tabular) or documents (unstructured)
    columns: list[str] = field(default_factory=list)  # tabular only


@dataclass
class IngestState:
    """Choices the user has made on the Data tab."""
    text_columns: list[str] = field(default_factory=list)
    id_column: str | None = None
    metadata_columns: list[str] = field(default_factory=list)
    json_path: str = ""          # for JSON ingestion
    segmenter: str = "none"
    segmenter_params: dict[str, Any] = field(default_factory=dict)
    segment_only: list[str] = field(default_factory=list)
    sample_n: int = 0            # 0 = all rows


@dataclass
class State:
    """The whole persistent state. JSON-serializable."""
    host: str = "http://localhost:11434"
    model: str = ""
    picked_recipe: str = ""
    recipe_json_buffer: str = ""    # what's currently in the JSON editor
    ingest: IngestState = field(default_factory=IngestState)
    sources_meta: list[SourceMeta] = field(default_factory=list)
    chat_mode: str = "free"          # "free" | "test"
    last_saved_ts: float = 0.0


# ---------------------------------------------------------------------------
# Workspace orchestrator
# ---------------------------------------------------------------------------


class Workspace:
    """Disk-backed workspace. Use .load() at app start, .save() after changes."""

    STATE_FILE = "state.json"
    SOURCES_PARQUET = "loaded_sources.parquet"
    DOCS_PARQUET = "docs.parquet"
    CHAT_FILE = "chat_history.json"
    RECIPE_TESTS_FILE = "recipe_tests.json"

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.state = State()
        self.loaded_sources_df: pd.DataFrame | None = None
        self.docs_df: pd.DataFrame | None = None
        self.chat_history: list[dict] = []
        self.recipe_tests: list[dict] = []

    # ---- Lifecycle ---------------------------------------------------------

    def load(self) -> "Workspace":
        """Load all artifacts from disk. Best-effort; missing files are OK."""
        self._load_state()
        self._load_loaded_sources_df()
        self._load_docs_df()
        self._load_chat_history()
        self._load_recipe_tests()
        return self

    def save(self) -> None:
        """Persist current in-memory state to disk."""
        self.state.last_saved_ts = time.time()
        self._save_state()
        self._save_loaded_sources_df()
        self._save_docs_df()
        self._save_chat_history()
        self._save_recipe_tests()

    def reset(self) -> None:
        """Wipe everything — both memory and disk."""
        self.state = State()
        self.loaded_sources_df = None
        self.docs_df = None
        self.chat_history = []
        self.recipe_tests = []
        for fname in (
            self.STATE_FILE, self.SOURCES_PARQUET, self.DOCS_PARQUET,
            self.CHAT_FILE, self.RECIPE_TESTS_FILE,
        ):
            p = self.root / fname
            if p.exists():
                try:
                    p.unlink()
                except OSError as exc:
                    log.warning("Could not delete %s: %s", p, exc)

    # ---- State (JSON) ------------------------------------------------------

    def _load_state(self) -> None:
        path = self.root / self.STATE_FILE
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("workspace state corrupt, ignoring: %s", exc)
            return

        # Manually rehydrate nested dataclasses
        ingest_data = data.pop("ingest", {})
        sources_data = data.pop("sources_meta", [])
        try:
            self.state = State(**data)
            self.state.ingest = IngestState(**ingest_data)
            self.state.sources_meta = [SourceMeta(**m) for m in sources_data]
        except TypeError as exc:
            # Schema drift — start fresh but log loudly
            log.warning("workspace state schema drift, resetting: %s", exc)
            self.state = State()

    def _save_state(self) -> None:
        path = self.root / self.STATE_FILE
        try:
            data = asdict(self.state)
            path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        except OSError as exc:
            log.warning("could not save workspace state: %s", exc)

    # ---- DataFrames (parquet) ---------------------------------------------

    def _load_loaded_sources_df(self) -> None:
        path = self.root / self.SOURCES_PARQUET
        if not path.exists():
            return
        try:
            self.loaded_sources_df = pd.read_parquet(path)
        except Exception as exc:  # noqa: BLE001 — many possible parquet errors
            log.warning("could not read loaded_sources.parquet: %s", exc)

    def _save_loaded_sources_df(self) -> None:
        path = self.root / self.SOURCES_PARQUET
        if self.loaded_sources_df is None or len(self.loaded_sources_df) == 0:
            if path.exists():
                path.unlink()
            return
        try:
            self.loaded_sources_df.to_parquet(path, index=False)
        except Exception as exc:  # noqa: BLE001
            log.warning("could not save loaded_sources: %s", exc)

    def _load_docs_df(self) -> None:
        path = self.root / self.DOCS_PARQUET
        if not path.exists():
            return
        try:
            self.docs_df = pd.read_parquet(path)
        except Exception as exc:  # noqa: BLE001
            log.warning("could not read docs.parquet: %s", exc)

    def _save_docs_df(self) -> None:
        path = self.root / self.DOCS_PARQUET
        if self.docs_df is None or len(self.docs_df) == 0:
            if path.exists():
                path.unlink()
            return
        try:
            self.docs_df.to_parquet(path, index=False)
        except Exception as exc:  # noqa: BLE001
            log.warning("could not save docs: %s", exc)

    # ---- Chat history (JSON) ----------------------------------------------

    def _load_chat_history(self) -> None:
        path = self.root / self.CHAT_FILE
        if not path.exists():
            return
        try:
            self.chat_history = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("chat history corrupt: %s", exc)

    def _save_chat_history(self) -> None:
        path = self.root / self.CHAT_FILE
        try:
            path.write_text(json.dumps(self.chat_history, indent=2, ensure_ascii=False))
        except OSError as exc:
            log.warning("could not save chat history: %s", exc)

    def _load_recipe_tests(self) -> None:
        path = self.root / self.RECIPE_TESTS_FILE
        if not path.exists():
            return
        try:
            self.recipe_tests = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("recipe tests corrupt: %s", exc)

    def _save_recipe_tests(self) -> None:
        path = self.root / self.RECIPE_TESTS_FILE
        try:
            # Cap the on-disk history so it doesn't grow forever
            recent = self.recipe_tests[-50:]
            path.write_text(json.dumps(recent, indent=2, ensure_ascii=False))
        except OSError as exc:
            log.warning("could not save recipe tests: %s", exc)
