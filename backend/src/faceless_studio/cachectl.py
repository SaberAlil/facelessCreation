"""Bounded cache: LRU cleanup of TTS/stock caches and rebuildable render clips.

Never touches job inputs, approved outputs, exports or the media library.
"""

from __future__ import annotations

from pathlib import Path

from .config import Settings


def _size(p: Path) -> int:
    if p.is_file():
        return p.stat().st_size
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.exists() else 0


def usage(s: Settings) -> dict[str, int]:
    return {"cache": _size(s.cache_dir), "jobs": _size(s.jobs_dir),
            "library": _size(s.library_dir), "db": _size(s.db_path)}


def _candidates(s: Settings) -> list[Path]:
    files = [f for f in s.cache_dir.rglob("*") if f.is_file()]
    # Per-job scene clips and fitted stills are rebuilt on demand by 'render'.
    files += [f for f in s.jobs_dir.glob("*/render/scenes/*/*") if f.is_file()]
    return files


def clean(s: Settings, max_gb: float | None = None, dry_run: bool = False) -> tuple[int, int]:
    limit = int((max_gb if max_gb is not None else s.cache.max_gb) * 1e9)
    files = sorted(_candidates(s), key=lambda f: f.stat().st_atime)  # oldest first
    total = sum(f.stat().st_size for f in files)
    removed = freed = 0
    for f in files:
        if total <= limit:
            break
        sz = f.stat().st_size
        if not dry_run:
            f.unlink(missing_ok=True)
        total -= sz
        freed += sz
        removed += 1
    return removed, freed
