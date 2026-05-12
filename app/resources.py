"""Resource detection for adaptive parallelism.

Tries multiple signals (in order):
  1. SLURM env vars (LUMI, other HPC) — authoritative when present
  2. nvidia-smi (NVIDIA GPUs)
  3. rocm-smi (AMD GPUs — LUMI MI250X)
  4. Ollama /api/ps response (whether GPU memory is in use)
  5. Fall back to CPU-only

Returns a `Resources` dataclass with an n_parallel suggestion.
The suggestion is conservative; users can override in Settings.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass

import requests

log = logging.getLogger(__name__)


@dataclass
class Resources:
    n_gpus: int = 0
    backend: str = "cpu"  # 'cpu' | 'nvidia' | 'amd' | 'unknown_gpu'
    cpu_count: int = 1
    detection_notes: str = ""

    @property
    def suggested_parallelism(self) -> int:
        """Conservative parallelism for batch recipe runs.

        Reasoning:
          * CPU-only: small models on CPU saturate memory bandwidth quickly.
            More than ~8 concurrent requests rarely speeds up.
          * 1 GPU: model forward pass serializes per-GPU; concurrency helps
            for I/O / tokenization overlap. ~12 is a good empirical default.
          * N GPUs: Ollama may route across them or may not, depending on
            config. We assume linear scaling but cap at 64 to avoid OOM
            from runaway request queues.
        """
        if self.n_gpus >= 1:
            return min(12 * self.n_gpus, 64)
        return min(max(self.cpu_count, 1), 8)


def detect(ollama_host: str | None = None) -> Resources:
    """Best-effort resource detection. Never raises."""
    res = Resources()
    res.cpu_count = os.cpu_count() or 1
    notes: list[str] = []

    # 1. SLURM (HPC; authoritative if set)
    slurm_gpus = os.environ.get("SLURM_GPUS_ON_NODE") or os.environ.get("SLURM_GPUS_PER_NODE")
    if slurm_gpus:
        try:
            res.n_gpus = int(slurm_gpus)
            res.backend = "amd" if "MI" in os.environ.get("ROCR_VISIBLE_DEVICES", "") or shutil.which("rocm-smi") else "nvidia"
            notes.append(f"SLURM reports {res.n_gpus} GPU(s)")
            res.detection_notes = "; ".join(notes)
            return res
        except ValueError:
            pass

    # 2. nvidia-smi
    if shutil.which("nvidia-smi"):
        try:
            out = subprocess.run(
                ["nvidia-smi", "--list-gpus"],
                capture_output=True, text=True, timeout=2.0,
            )
            if out.returncode == 0:
                lines = [l for l in out.stdout.splitlines() if l.strip()]
                if lines:
                    res.n_gpus = len(lines)
                    res.backend = "nvidia"
                    notes.append(f"nvidia-smi found {res.n_gpus} GPU(s)")
        except (subprocess.TimeoutExpired, OSError):
            pass

    # 3. rocm-smi (AMD)
    if res.n_gpus == 0 and shutil.which("rocm-smi"):
        try:
            out = subprocess.run(
                ["rocm-smi", "--showid"],
                capture_output=True, text=True, timeout=2.0,
            )
            if out.returncode == 0:
                # rocm-smi --showid output contains lines like "GPU[0]:" per GPU
                gpus = sum(1 for line in out.stdout.splitlines() if "GPU[" in line)
                if gpus > 0:
                    res.n_gpus = gpus
                    res.backend = "amd"
                    notes.append(f"rocm-smi found {res.n_gpus} GPU(s)")
        except (subprocess.TimeoutExpired, OSError):
            pass

    # 4. Ask Ollama itself if it's using GPU (best-effort signal)
    if res.n_gpus == 0 and ollama_host:
        try:
            r = requests.get(ollama_host.rstrip("/") + "/api/ps", timeout=1.0)
            if r.ok:
                data = r.json()
                for m in data.get("models", []):
                    if m.get("size_vram", 0) > 0:
                        # GPU is in use. Can't count, but we know it's not 0.
                        res.n_gpus = 1
                        res.backend = "unknown_gpu"
                        notes.append("Ollama reports GPU memory in use (count unknown)")
                        break
        except (requests.RequestException, ValueError):
            pass

    if res.n_gpus == 0:
        notes.append(f"no GPU detected; CPU-only with {res.cpu_count} cores")

    res.detection_notes = "; ".join(notes)
    return res
