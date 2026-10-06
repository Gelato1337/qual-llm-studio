"""
Google Drive integration.

In Colab, drive.mount('/content/drive') exposes the user's Drive at
/content/drive/MyDrive. We write run folders there so results survive
the runtime being killed.

Outside Colab, this module degrades gracefully — `is_available()`
returns False, and `save_run_to_drive` raises a clear error. The UI
disables the Drive button when not available.

Design decisions:
  * No automatic backup. Researchers click Save explicitly.
  * Default folder: /content/drive/MyDrive/QualLLMStudio/runs/
  * Copy, not symlink. Drive mount has its own quirks; copies are robust.
"""

from __future__ import annotations

import shutil
from pathlib import Path

DRIVE_MOUNT = Path("/content/drive")
DEFAULT_FOLDER = DRIVE_MOUNT / "MyDrive" / "QualLLMStudio" / "runs"


def is_available() -> bool:
    """True if Drive is mounted and writable."""
    return DRIVE_MOUNT.exists() and (DRIVE_MOUNT / "MyDrive").exists()


def mount(force: bool = False) -> bool:
    """Mount Drive if running in Colab. Returns True on success.

    Idempotent — calling when already mounted is fine. Returns False
    quietly if not in Colab.
    """
    try:
        from google.colab import drive  # type: ignore
    except ImportError:
        return False
    drive.mount("/content/drive", force_remount=force)
    return is_available()


def save_run_to_drive(run_folder: str | Path, drive_subfolder: str | None = None) -> Path:
    """Copy `run_folder` to Drive, preserving the folder name.

    Args:
        run_folder: local path to the run folder (typically runs/<pipeline>/v<N>_<model>/)
        drive_subfolder: optional sub-path under DEFAULT_FOLDER. If the
            run came from runs/aspect/v3_qwen3/, pass "aspect" so it lands
            at /content/drive/MyDrive/QualLLMStudio/runs/aspect/v3_qwen3/.

    Returns the destination path.
    """
    if not is_available():
        raise RuntimeError(
            "Google Drive is not mounted. Call drive.mount() first, "
            "or use this in a Colab runtime."
        )

    src = Path(run_folder)
    if not src.exists():
        raise FileNotFoundError(f"Run folder not found: {src}")

    dest_root = DEFAULT_FOLDER
    if drive_subfolder:
        dest_root = dest_root / drive_subfolder
    dest_root.mkdir(parents=True, exist_ok=True)

    dest = dest_root / src.name
    if dest.exists():
        # Make a unique destination instead of overwriting silently.
        i = 2
        while (alt := dest_root / f"{src.name}_dup{i}").exists():
            i += 1
        dest = alt
    shutil.copytree(src, dest)
    return dest
