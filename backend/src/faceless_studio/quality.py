"""Technical validation of rendered MP4s with ffprobe/ffmpeg."""

from __future__ import annotations

import struct
from fractions import Fraction
from pathlib import Path

from . import media
from .config import RenderConfig
from .util import run


def moov_before_mdat(path: Path, max_boxes: int = 64) -> bool | None:
    """True if the MP4 is 'faststart' (moov atom precedes mdat)."""
    with open(path, "rb") as f:
        for _ in range(max_boxes):
            hdr = f.read(8)
            if len(hdr) < 8:
                return None
            size, typ = struct.unpack(">I4s", hdr)
            if typ == b"moov":
                return True
            if typ == b"mdat":
                return False
            if size == 1:
                size = struct.unpack(">Q", f.read(8))[0]
                f.seek(size - 16, 1)
            elif size == 0:
                return None
            else:
                f.seek(size - 8, 1)
    return None


def validate_video(path: Path, expected_duration: float, cfg: RenderConfig,
                   preview: bool = False, decode_check: bool = True) -> dict:
    checks: list[dict] = []

    def add(name: str, ok: bool, detail: str, required: bool = True) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail, "required": required})

    if not path.is_file():
        add("exists", False, str(path))
        return {"ok": False, "checks": checks}
    info = media.ffprobe(path)
    v = next((s for s in info["streams"] if s.get("codec_type") == "video"), None)
    a = next((s for s in info["streams"] if s.get("codec_type") == "audio"), None)
    W, H = (cfg.preview_width, cfg.preview_height) if preview else (cfg.width, cfg.height)
    add("video stream", v is not None, "present" if v else "missing")
    add("audio stream", a is not None, "present" if a else "missing")
    if v:
        add("codec", v.get("codec_name") == "h264", v.get("codec_name", "?"))
        add("resolution", (v.get("width"), v.get("height")) == (W, H),
            f"{v.get('width')}x{v.get('height')} (want {W}x{H})")
        add("pixel format", v.get("pix_fmt") == "yuv420p", v.get("pix_fmt", "?"))
        fr = Fraction(v.get("avg_frame_rate", "0/1") or "0/1")
        add("frame rate", abs(float(fr) - cfg.fps) < 0.01, f"{float(fr):.3f} fps")
        sar = v.get("sample_aspect_ratio", "1:1")
        add("square pixels", sar in ("1:1", "0:1", None), str(sar), required=False)
    if a:
        add("audio codec", a.get("codec_name") == "aac", a.get("codec_name", "?"))
        add("audio rate", int(a.get("sample_rate", 0)) == cfg.audio_rate,
            f"{a.get('sample_rate')} Hz")
        add("audio channels", int(a.get("channels", 0)) == 2, f"{a.get('channels')} ch",
            required=False)
    dur = float(info.get("format", {}).get("duration", 0))
    tol = max(cfg.duration_tolerance_s, 2 / cfg.fps)
    add("duration matches narration", abs(dur - expected_duration) <= tol,
        f"{dur:.3f}s vs narration {expected_duration:.3f}s (±{tol:.2f}s)")
    if v and a:
        vd = float(v.get("duration", dur))
        ad = float(a.get("duration", dur))
        add("a/v durations agree", abs(vd - ad) <= 0.1, f"video {vd:.3f}s, audio {ad:.3f}s")
        add("starts at zero", abs(float(v.get("start_time", 0))) < 0.05 and
            abs(float(a.get("start_time", 0))) < 0.05,
            f"v {v.get('start_time')} a {a.get('start_time')}")
    fs = moov_before_mdat(path)
    add("faststart", fs is True, "moov before mdat" if fs else "moov after mdat / unknown")
    if decode_check:
        res = run(["ffmpeg", "-hide_banner", "-v", "error", "-i", str(path), "-f", "null",
                   "-"], timeout=600, check=False)
        errs = [ln for ln in res.stderr.splitlines() if ln.strip()]
        add("decodes without errors", res.returncode == 0 and not errs,
            "clean" if not errs else "; ".join(errs[:3]))
    if a:
        loud = media.measure_loudness(path, cfg.loudness_lufs, cfg.true_peak_db)
        add("integrated loudness", abs(loud["input_i"] - cfg.loudness_lufs) <= 1.5,
            f"{loud['input_i']:.1f} LUFS (target {cfg.loudness_lufs})")
        add("true peak (no clipping)", loud["input_tp"] <= cfg.true_peak_db + 0.6,
            f"{loud['input_tp']:.1f} dBTP (limit {cfg.true_peak_db})")
    in_range = cfg.min_duration_s <= dur <= cfg.max_duration_s
    add("length for Shorts/Reels/TikTok", in_range,
        f"{dur:.1f}s (publishable range {cfg.min_duration_s:.0f}-{cfg.max_duration_s:.0f}s)",
        required=False)
    ok = all(c["ok"] for c in checks if c["required"])
    return {"ok": ok, "duration": dur, "size_bytes": path.stat().st_size, "checks": checks,
            "preview": preview}
