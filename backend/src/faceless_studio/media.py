"""FFmpeg/ffprobe helpers: probing, encoder selection, loudness measurement."""

from __future__ import annotations

import json
import logging
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import RenderConfig
from .util import ProviderUnavailable, ToolFailed, run

log = logging.getLogger("faceless")

FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"


def require_ffmpeg() -> None:
    for exe in (FFMPEG, FFPROBE):
        if not shutil.which(exe):
            raise ProviderUnavailable(
                f"{exe} not found",
                hint="install FFmpeg (Debian/Ubuntu: sudo apt install ffmpeg; macOS: brew "
                "install ffmpeg)",
            )


def ffprobe(path: Path, timeout: float = 60) -> dict[str, Any]:
    res = run(
        [FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
         str(path)],
        timeout=timeout,
    )
    return json.loads(res.stdout)


def duration(path: Path) -> float:
    info = ffprobe(path)
    d = info.get("format", {}).get("duration")
    if d is None:
        raise ToolFailed(f"ffprobe found no duration for {path.name}")
    return float(d)


def media_dims(path: Path) -> tuple[int | None, int | None, float | None]:
    info = ffprobe(path)
    v = next((s for s in info["streams"] if s.get("codec_type") == "video"), None)
    dur = info.get("format", {}).get("duration")
    if not v:
        return None, None, float(dur) if dur else None
    return int(v["width"]), int(v["height"]), float(dur) if dur else None


# ---------------------------------------------------------------- encoders


@dataclass
class EncoderChoice:
    name: str  # h264_nvenc | libx264
    reason: str


_probe_cache: dict[str, bool] = {}


def encoder_listed(name: str) -> bool:
    res = run([FFMPEG, "-hide_banner", "-encoders"], timeout=30, check=False)
    return any(line.split()[1:2] == [name] for line in res.stdout.splitlines() if line.strip())


def probe_nvenc(timeout: float = 30) -> tuple[bool, str]:
    """Real 10-frame NVENC encode; listing the encoder is not proof it works."""
    if "h264_nvenc" in _probe_cache:
        return _probe_cache["h264_nvenc"], "cached probe"
    if not encoder_listed("h264_nvenc"):
        _probe_cache["h264_nvenc"] = False
        return False, "h264_nvenc not compiled into this FFmpeg"
    with tempfile.TemporaryDirectory(prefix="nvenc-probe-") as d:
        out = Path(d) / "probe.mp4"
        res = run(
            [FFMPEG, "-hide_banner", "-v", "error", "-f", "lavfi", "-i",
             "testsrc2=size=256x256:rate=30", "-frames:v", "10", "-c:v", "h264_nvenc",
             "-pix_fmt", "yuv420p", "-y", str(out)],
            timeout=timeout, check=False,
        )
        ok = res.returncode == 0 and out.is_file() and out.stat().st_size > 0
        reason = "real NVENC test encode succeeded" if ok else (
            "NVENC test encode failed: " + (res.stderr.strip().splitlines() or ["?"])[-1])
    _probe_cache["h264_nvenc"] = ok
    return ok, reason


def select_encoder(cfg: RenderConfig) -> EncoderChoice:
    if cfg.encoder == "libx264":
        return EncoderChoice("libx264", "forced by config")
    ok, reason = probe_nvenc()
    if ok:
        return EncoderChoice("h264_nvenc", reason)
    if cfg.encoder == "h264_nvenc":
        log.warning("h264_nvenc requested but unusable (%s); using libx264", reason)
    return EncoderChoice("libx264", reason)


def video_codec_args(encoder: str, cfg: RenderConfig, preview: bool = False,
                     intermediate: bool = False) -> list[str]:
    """Encoder arguments. Intermediates use higher quality to limit generation loss."""
    if encoder == "h264_nvenc":
        cq = cfg.nvenc_cq - (3 if intermediate else 0) + (4 if preview else 0)
        return ["-c:v", "h264_nvenc", "-preset", "p4" if preview else cfg.nvenc_preset,
                "-tune", "hq", "-rc", "vbr", "-cq", str(cq), "-b:v", "0",
                "-profile:v", "high", "-bf", "2", "-g", str(cfg.fps * 2)]
    crf = cfg.x264_crf - (3 if intermediate else 0) + (4 if preview else 0)
    preset = "veryfast" if preview else cfg.x264_preset
    return ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-profile:v", "high",
            "-g", str(cfg.fps * 2)]


# ---------------------------------------------------------------- loudness

_LOUDNORM_JSON = re.compile(r"\{[^{}]*\"input_i\"[^{}]*\}", re.S)


def measure_loudness(path: Path, target_i: float, target_tp: float, timeout: float = 300
                     ) -> dict[str, float]:
    res = run(
        [FFMPEG, "-hide_banner", "-nostats", "-i", str(path), "-af",
         f"loudnorm=I={target_i}:TP={target_tp}:LRA=11:print_format=json", "-f", "null", "-"],
        timeout=timeout,
    )
    m = _LOUDNORM_JSON.search(res.stderr)
    if not m:
        raise ToolFailed("could not parse loudnorm measurement")
    data = json.loads(m.group(0))
    return {k: float(v) for k, v in data.items() if k != "normalization_type"}


def loudnorm_filter(measured: dict[str, float], target_i: float, target_tp: float) -> str:
    """Second-pass loudnorm filter using first-pass measurements (linear when possible)."""
    return (
        f"loudnorm=I={target_i}:TP={target_tp}:LRA=11"
        f":measured_I={measured['input_i']}:measured_TP={measured['input_tp']}"
        f":measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}"
        f":offset={measured['target_offset']}:linear=true"
    )


def normalize_audio(src: Path, dst: Path, target_i: float, target_tp: float,
                    sample_rate: int, channels: int = 1, timeout: float = 300,
                    log_file: Path | None = None) -> dict[str, float]:
    measured = measure_loudness(src, target_i, target_tp, timeout)
    if measured["input_i"] == float("-inf") or measured["input_i"] < -70:
        raise ToolFailed(f"{src.name} is silent; refusing to normalize")
    run(
        [FFMPEG, "-hide_banner", "-y", "-i", str(src), "-af",
         loudnorm_filter(measured, target_i, target_tp), "-ar", str(sample_rate),
         "-ac", str(channels), "-c:a", "pcm_s16le", str(dst)],
        timeout=timeout, log_file=log_file,
    )
    return measure_loudness(dst, target_i, target_tp, timeout)
