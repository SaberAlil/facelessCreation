"""GPU/RAM resource management.

- ``gpu_lock``: cross-process exclusive lock (``fcntl.flock``) so only one GPU-heavy
  stage runs at a time. The kernel releases the lock if the holder dies.
- ``check_vram`` / ``check_ram``: refuse to start a stage without the configured headroom.
- ``StageMonitor``: records duration, peak RSS (self + children) and sampled peak GPU
  memory use for every stage.

GPU memory is read with ``nvidia-smi`` (whole-GPU numbers, so it also sees Ollama and
ComfyUI running in other containers).
"""

from __future__ import annotations

import contextlib
import fcntl
import logging
import os
import resource
import shutil
import subprocess
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .util import FacelessError, utcnow

log = logging.getLogger("faceless")


class ResourceUnavailable(FacelessError):
    exit_code = 8


class GpuOutOfMemory(FacelessError):
    exit_code = 9


OOM_MARKERS = (
    "out of memory",
    "cuda error: out of memory",
    "cudaerrormemoryallocation",
    "cublas_status_alloc_failed",
    "failed to allocate",
    "insufficient memory",
    "torch.outofmemoryerror",
)


def looks_like_oom(text: str) -> bool:
    t = text.lower()
    return any(m in t for m in OOM_MARKERS)


@dataclass
class GpuInfo:
    name: str
    total_mib: int
    used_mib: int
    free_mib: int
    driver: str


def query_gpu() -> GpuInfo | None:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run(
            [exe, "--query-gpu=name,memory.total,memory.used,memory.free,driver_version",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout.strip().splitlines()
    except (subprocess.SubprocessError, OSError):
        return None
    if not out:
        return None
    parts = [p.strip() for p in out[0].split(",")]
    try:
        return GpuInfo(parts[0], int(parts[1]), int(parts[2]), int(parts[3]), parts[4])
    except (IndexError, ValueError):
        return None


def meminfo_mib() -> dict[str, int]:
    out: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            k, v = line.split(":", 1)
            out[k] = int(v.split()[0]) // 1024
    except OSError:
        pass
    return out


def check_ram(required_mib: int, headroom_mib: int, stage: str) -> int:
    avail = meminfo_mib().get("MemAvailable")
    if avail is None:
        return -1
    if avail < required_mib + headroom_mib:
        raise ResourceUnavailable(
            f"{stage}: only {avail} MiB RAM available; need {required_mib} + "
            f"{headroom_mib} MiB headroom",
            hint="close other applications or lower resources.ram_headroom_mib after measuring",
        )
    return avail


def check_vram(required_mib: int, headroom_mib: int, stage: str) -> GpuInfo:
    gpu = query_gpu()
    if gpu is None:
        raise ResourceUnavailable(f"{stage}: no NVIDIA GPU visible")
    if gpu.free_mib < required_mib + headroom_mib:
        raise ResourceUnavailable(
            f"{stage}: {gpu.free_mib} MiB VRAM free; need {required_mib} + {headroom_mib} "
            "MiB headroom",
            hint="stop ComfyUI or other GPU programs (check nvidia-smi) and retry",
        )
    return gpu


class GpuLockTimeout(ResourceUnavailable):
    pass


@contextlib.contextmanager
def file_lock(lock_dir: Path, name: str, owner: str, timeout_s: float, poll_s: float = 0.5
              ) -> Iterator[None]:
    """Exclusive cross-process lock ``<lock_dir>/<name>.lock``; re-entrant per thread.

    ``flock`` locks are released by the kernel if the holding process dies, so a crash
    can never leave the GPU "locked" forever.
    """
    depths: dict[str, int] = _held.__dict__.setdefault("depths", {})
    if depths.get(name, 0) > 0:
        depths[name] += 1
        try:
            yield
        finally:
            depths[name] -= 1
        return
    lock_dir.mkdir(parents=True, exist_ok=True)
    path = lock_dir / f"{name}.lock"
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    deadline = time.monotonic() + timeout_s
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    holder = _read_holder(path)
                    raise GpuLockTimeout(
                        f"{name} busy for {timeout_s:.0f}s (held by: {holder or 'unknown'})",
                        hint="wait for the other stage to finish; the lock frees itself "
                        "if its process exits",
                    ) from None
                time.sleep(poll_s)
        os.ftruncate(fd, 0)
        os.write(fd, f"{owner} pid={os.getpid()} since={utcnow()}\n".encode())
        depths[name] = 1
        try:
            yield
        finally:
            depths[name] = 0
            with contextlib.suppress(OSError):
                os.ftruncate(fd, 0)
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def gpu_lock(lock_dir: Path, owner: str, timeout_s: float, poll_s: float = 0.5):
    """Only one GPU-heavy stage (LLM, image generation, NVENC render) at a time."""
    return file_lock(lock_dir, "gpu", owner, timeout_s, poll_s)


_held = threading.local()


def _read_holder(path: Path) -> str:
    try:
        return path.read_text().strip()
    except OSError:
        return ""


@dataclass
class StageMetrics:
    device: str = "cpu"
    started: str = field(default_factory=utcnow)
    duration_s: float = 0.0
    peak_rss_mib: float | None = None
    peak_children_rss_mib: float | None = None
    peak_vram_used_mib: float | None = None
    baseline_vram_used_mib: float | None = None
    fallbacks: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "device": self.device,
            "duration_s": round(self.duration_s, 3),
            "peak_rss_mib": self.peak_rss_mib,
            "peak_children_rss_mib": self.peak_children_rss_mib,
            "peak_vram_used_mib": self.peak_vram_used_mib,
            "baseline_vram_used_mib": self.baseline_vram_used_mib,
        }


class StageMonitor:
    """Context manager measuring a stage. Samples GPU memory in a thread if a GPU exists.

    Peak RSS uses ``getrusage`` high-water marks: values are process-lifetime peaks, so
    in a long-running process they are upper bounds for the stage.
    """

    def __init__(self, device: str = "cpu", sample_interval_s: float = 1.0,
                 sample_gpu: bool | None = None):
        self.metrics = StageMetrics(device=device)
        self._interval = sample_interval_s
        self._sample_gpu = (shutil.which("nvidia-smi") is not None) if sample_gpu is None \
            else sample_gpu
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._t0 = 0.0

    def _sample(self) -> None:
        while not self._stop.is_set():
            gpu = query_gpu()
            if gpu is not None:
                cur = self.metrics.peak_vram_used_mib or 0
                self.metrics.peak_vram_used_mib = max(cur, gpu.used_mib)
            self._stop.wait(self._interval)

    def __enter__(self) -> StageMonitor:
        self._t0 = time.monotonic()
        if self._sample_gpu:
            gpu = query_gpu()
            if gpu is not None:
                self.metrics.baseline_vram_used_mib = gpu.used_mib
                self._thread = threading.Thread(target=self._sample, daemon=True)
                self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        self.metrics.duration_s = time.monotonic() - self._t0
        self.metrics.peak_rss_mib = round(
            resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
        self.metrics.peak_children_rss_mib = round(
            resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024, 1)
