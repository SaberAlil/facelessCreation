"""Environment checks with actionable guidance. Never installs or downloads anything."""

from __future__ import annotations

import importlib.metadata as md
import importlib.util
import platform
import shutil
from dataclasses import dataclass

import httpx

from . import media
from .config import Settings
from .resources import meminfo_mib, query_gpu
from .util import hf_repo_cached, run


@dataclass
class Check:
    name: str
    status: str  # ok | warn | fail | info
    detail: str
    hint: str = ""


def _pkg(name: str) -> str | None:
    try:
        return md.version(name)
    except md.PackageNotFoundError:
        return None


def _hf_cached(repo: str) -> bool:
    return hf_repo_cached(repo)


def run_checks(s: Settings, probe_encoders: bool = True, network: bool = True) -> list[Check]:
    c: list[Check] = []
    c.append(Check("platform", "info", f"{platform.system()} {platform.release()} "
                   f"{platform.machine()}, Python {platform.python_version()}"))

    # FFmpeg
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        ver = run(["ffmpeg", "-version"], timeout=20).stdout.splitlines()[0]
        c.append(Check("ffmpeg", "ok", ver))
        filters = run(["ffmpeg", "-hide_banner", "-filters"], timeout=20).stdout
        for f, why in (("ass", "burned-in captions"), ("loudnorm", "loudness normalisation"),
                       ("zoompan", "motion on stills"), ("drawtext", "graphics text"),
                       ("sidechaincompress", "music ducking"), ("gradients", "graphics")):
            present = any(line.split()[1:2] == [f] for line in filters.splitlines()
                          if line.strip())
            c.append(Check(f"ffmpeg filter {f}", "ok" if present else "fail", why,
                           "" if present else "install an FFmpeg build with libass/freetype"))
        if probe_encoders:
            ok, reason = media.probe_nvenc()
            c.append(Check("encoder h264_nvenc", "ok" if ok else "warn", reason,
                           "" if ok else "libx264 (CPU) will be used"))
        c.append(Check("encoder libx264", "ok" if media.encoder_listed("libx264") else "fail",
                       "CPU fallback encoder"))
    else:
        c.append(Check("ffmpeg", "fail", "ffmpeg/ffprobe not found",
                       "Debian/Ubuntu: sudo apt install ffmpeg · macOS: brew install ffmpeg · "
                       "Windows: use WSL2 or winget install Gyan.FFmpeg"))

    # Fonts
    font = s.font_file()
    c.append(Check("font", "ok" if font.is_file() else "fail", str(font),
                   "" if font.is_file() else "install fonts-dejavu-core or set paths.fonts_dir"))

    # Hardware
    lscpu = run(["lscpu"], timeout=10, check=False).stdout if shutil.which("lscpu") else ""
    model = next((ln.split(":", 1)[1].strip() for ln in lscpu.splitlines()
                  if ln.startswith("Model name")), platform.processor() or "unknown")
    mem = meminfo_mib()
    c.append(Check("cpu", "info", f"{model}"))
    if mem:
        avail = mem.get("MemAvailable", 0)
        st = "ok" if avail >= s.resources.ram_headroom_mib + 2048 else "warn"
        c.append(Check("ram", st, f"{mem.get('MemTotal', 0)} MiB total, {avail} MiB available",
                       "" if st == "ok" else "close other apps before TTS/Whisper"))
    gpu = query_gpu()
    if gpu:
        c.append(Check("gpu", "ok", f"{gpu.name}, {gpu.total_mib} MiB "
                       f"({gpu.free_mib} MiB free), driver {gpu.driver}"))
    else:
        c.append(Check("gpu", "info", "no NVIDIA GPU visible: CPU mode (fully supported)"))
    du = shutil.disk_usage(s.data_dir if s.data_dir.exists() else s.project_root)
    free_gb = du.free / 1e9
    c.append(Check("disk", "ok" if free_gb > 10 else "warn", f"{free_gb:.1f} GB free at "
                   f"{s.data_dir}", "" if free_gb > 10 else "free space; renders + models "
                   "need several GB"))

    # Python providers
    torch_v = _pkg("torch")
    for pkg, role in (("kokoro", "TTS"), ("faster-whisper", "captions"),
                      ("soundfile", "audio IO"), ("openpyxl", "XLSX calendar")):
        v = _pkg(pkg)
        c.append(Check(f"python {pkg}", "ok" if v else "warn", f"{v or 'missing'} ({role})",
                       "" if v else "bash .devcontainer/install-ai-deps.sh"))
    if torch_v:
        c.append(Check("python torch", "info", torch_v))
    c.append(Check("model kokoro", "ok" if _hf_cached(s.tts.kokoro_repo) else "warn",
                   s.tts.kokoro_repo, "" if _hf_cached(s.tts.kokoro_repo) else
                   "downloads on first 'faceless voice' (~330 MB)"))
    wrepo = f"Systran/faster-whisper-{s.subtitles.model}"
    c.append(Check("model whisper", "ok" if _hf_cached(wrepo) else "warn", wrepo,
                   "" if _hf_cached(wrepo) else "downloads on first 'faceless subtitles'"))
    c.append(Check("espeak-ng", "ok" if shutil.which("espeak-ng") else "info",
                   "demo TTS fallback / Kokoro phonemizer fallback"))
    spacy_ok = importlib.util.find_spec("en_core_web_sm") is not None
    c.append(Check("spacy en_core_web_sm", "ok" if spacy_ok else "info",
                   "used by Kokoro's misaki G2P", "" if spacy_ok else
                   "misaki pip-installs it (~13 MB) on first use"))

    # Optional network providers
    if network:
        try:
            r = httpx.get(f"{s.llm.host}/api/tags", timeout=5)
            names = [m["name"] for m in r.json().get("models", [])]
            st = "ok" if s.llm.model in names else "warn"
            c.append(Check("ollama", st, f"{s.llm.host}: models {names or 'none'}",
                           "" if st == "ok" else f"ollama pull {s.llm.model} (~2.5 GB, "
                           "needs approval); or use a manual script"))
        except (httpx.HTTPError, ValueError):
            c.append(Check("ollama", "info", f"not reachable at {s.llm.host} (optional)",
                           "manual scripts work without it"))
        if s.comfyui.enabled:
            try:
                httpx.get(f"{s.comfyui.host}/system_stats", timeout=5).raise_for_status()
                c.append(Check("comfyui", "ok", s.comfyui.host))
            except httpx.HTTPError:
                c.append(Check("comfyui", "warn", f"enabled but not reachable at "
                               f"{s.comfyui.host}"))
    import os
    c.append(Check("pexels", "ok" if os.environ.get("PEXELS_API_KEY") else "info",
                   "PEXELS_API_KEY set" if os.environ.get("PEXELS_API_KEY") else
                   "PEXELS_API_KEY not set (optional stock provider)"))
    return c
