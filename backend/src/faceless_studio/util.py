"""Small shared helpers: hashing, safe paths, atomic writes, subprocess calls."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger("faceless")


class FacelessError(Exception):
    """Expected, user-facing error. ``exit_code`` maps to the CLI exit status."""

    exit_code = 1

    def __init__(self, message: str, hint: str | None = None):
        super().__init__(message)
        self.hint = hint


class NotFoundError(FacelessError):
    exit_code = 3


class GateError(FacelessError):
    """A review gate or precondition blocks the requested step."""

    exit_code = 4


class ValidationFailed(FacelessError):
    exit_code = 5


class ProviderUnavailable(FacelessError):
    exit_code = 6


class ToolFailed(FacelessError):
    exit_code = 7


def utcnow() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def hash_obj(obj: Any) -> str:
    return sha256_bytes(canonical_json(obj).encode("utf-8"))


def hash_files(paths: Sequence[Path]) -> str:
    h = hashlib.sha256()
    for p in paths:
        h.update(p.name.encode())
        h.update(sha256_file(p).encode() if p.exists() else b"<missing>")
    return h.hexdigest()


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def write_json(path: Path, obj: Any) -> None:
    atomic_write_text(path, json.dumps(obj, indent=2, ensure_ascii=False, default=str) + "\n")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def tmp_path_for(final: Path) -> Path:
    """Sibling temporary path with the same suffix (FFmpeg picks format by suffix)."""
    return final.with_name(f".{final.stem}.partial{final.suffix}")


def safe_child(base: Path, *parts: str) -> Path:
    """Join ``parts`` under ``base`` and refuse anything that escapes it."""
    base_r = base.resolve()
    target = base_r.joinpath(*parts).resolve()
    if target != base_r and base_r not in target.parents:
        raise FacelessError(f"path escapes {base_r}: {'/'.join(parts)}")
    return target


_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def safe_filename(name: str, default: str = "file") -> str:
    cleaned = _SAFE_NAME.sub("-", name).strip(".-")[:120]
    return cleaned or default


def user_file(path: str | Path, must_exist: bool = True) -> Path:
    """Validate a user-supplied input file path (regular file, no devices/fifos)."""
    p = Path(path).expanduser().resolve()
    if must_exist:
        if not p.exists():
            raise NotFoundError(f"file not found: {p}")
        if not p.is_file():
            raise FacelessError(f"not a regular file: {p}")
    return p


def ffmpeg_filter_escape(value: str | Path) -> str:
    """Escape a value (usually a path) for use inside an FFmpeg filtergraph option.

    Two levels, as documented in ffmpeg-filters "Notes on filtergraph escaping":
    option-value escaping (``\\ ' :``), then graph-level escaping (``\\ ' [ ] , ;``).
    """
    s = str(value)
    s = s.replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:")
    s = s.replace("\\", "\\\\").replace("'", "\\'")
    for ch in "[],;":
        s = s.replace(ch, "\\" + ch)
    return s


@dataclass
class RunResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str
    duration_s: float


def run(
    args: Sequence[str | Path],
    timeout: float,
    check: bool = True,
    log_file: Path | None = None,
    env: dict[str, str] | None = None,
) -> RunResult:
    """Run a command from an argument list (never through a shell) with a timeout."""
    argv = [str(a) for a in args]
    log.debug("run: %s", " ".join(argv))
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, **env} if env else None,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except FileNotFoundError as e:
        raise ProviderUnavailable(f"program not found: {argv[0]}") from e
    except subprocess.TimeoutExpired as e:
        raise ToolFailed(f"{Path(argv[0]).name} timed out after {timeout:.0f}s") from e
    res = RunResult(argv, proc.returncode, proc.stdout, proc.stderr, time.monotonic() - t0)
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"\n### {utcnow()} ({res.duration_s:.1f}s, rc={res.returncode})\n")
            f.write(" ".join(argv) + "\n")
            f.write(proc.stderr[-20000:])
    if check and proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-12:])
        raise ToolFailed(f"{Path(argv[0]).name} failed (exit {proc.returncode}):\n{tail}")
    return res


def hf_repo_cached(repo: str) -> bool:
    """True if a Hugging Face repo snapshot is in the local cache."""
    base = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
    d = base / ("models--" + repo.replace("/", "--")) / "snapshots"
    return d.is_dir() and any(d.iterdir())


def prefer_offline(repo: str) -> None:
    """Use the local model cache without network calls once a model is installed."""
    if hf_repo_cached(repo):
        os.environ["HF_HUB_OFFLINE"] = "1"


def word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9$%'’.-]+", text))
