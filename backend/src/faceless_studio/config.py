"""Configuration: built-in defaults < config/default.yaml < config/local.yaml < env vars.

Environment overrides use ``FACELESS__SECTION__KEY=value`` (double underscores),
e.g. ``FACELESS__RENDER__ENCODER=libx264``. ``VIDEO_ENCODER`` and ``OLLAMA_HOST``
are honoured for compatibility with the dev container.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PathsConfig(_Section):
    # Relative paths resolve against the project root.
    data_dir: str = "data"
    content_dir: str = "content"
    fonts_dir: str = "/usr/share/fonts/truetype/dejavu"


class ChannelConfig(_Section):
    name: str = "The Hidden Logic"
    language: str = "en"
    default_hashtags: list[str] = Field(default_factory=lambda: ["#TheHiddenLogic", "#economics"])


class ScriptConfig(_Section):
    target_words_min: int = 135
    target_words_max: int = 165
    hard_words_min: int = 80
    hard_words_max: int = 220
    # Kokoro af_heart measured 2.16-2.59 words/s on real scripts: estimate conservatively.
    words_per_second: float = 2.2
    max_estimated_s: float = 86.0  # scripts estimated longer than this cannot be approved
    min_estimated_s: float = 45.0
    min_scenes: int = 4
    max_scenes: int = 12


class LLMConfig(_Section):
    provider: Literal["ollama", "manual"] = "ollama"
    host: str = "http://ollama:11434"
    model: str = "qwen3:4b"
    num_ctx: int = 4096
    reduced_num_ctx: int = 2048
    temperature: float = 0.6
    timeout_s: float = 600.0
    max_attempts: int = 3
    keep_alive: str | int = 0
    estimated_vram_mib: int = 3800


class TTSConfig(_Section):
    provider: Literal["kokoro", "espeak", "user"] = "kokoro"
    kokoro_repo: str = "hexgrad/Kokoro-82M"
    voice: str = "af_heart"
    lang_code: str = "a"
    speed: float = 1.0
    sample_rate: int = 24000
    pause_between_scenes_ms: int = 250  # scenes are short beats (often one sentence)
    tail_silence_ms: int = 700
    narration_lufs: float = -16.0
    device: Literal["cpu", "cuda"] = "cpu"


class SubtitleConfig(_Section):
    model: str = "small.en"
    fallback_model: str = "base.en"
    device: Literal["cpu", "cuda"] = "cpu"
    compute_type: str = "int8"
    # "words": Shorts-style, 1-3 big words at a time with the spoken word highlighted;
    # "lines": classic two-line subtitles.
    style: Literal["words", "lines"] = "words"
    min_words: int = 1
    max_words: int = 3
    max_chars_per_line: int = 15
    max_lines: int = 2
    min_duration_s: float = 0.3
    font: str = "DejaVu Sans"
    font_size: int = 90
    outline: int = 6
    shadow: int = 2
    primary_colour: str = "&H00FFFFFF"
    accent_colour: str = "&H0000D7FF"
    outline_colour: str = "&H00000000"
    highlight_keywords: bool = False
    margin_v: int = 600  # keep captions above the bottom platform overlay
    margin_l: int = 90
    margin_r: int = 170  # right-side action buttons
    divergence_ratio_warn: float = 0.9
    # Language of the burned-in captions. "en" = the narration itself; anything else is a
    # sentence-by-sentence translation by the local LLM, timed to the English narration.
    burn_language: str = "en"  # default for new videos; "none" = no burned-in subtitles
    translate_model: str = ""  # empty = llm.model


class RenderConfig(_Section):
    width: int = 1080
    height: int = 1920
    fps: int = 30
    preview_width: int = 540
    preview_height: int = 960
    encoder: Literal["auto", "h264_nvenc", "libx264"] = "auto"
    x264_crf: int = 20
    x264_preset: str = "medium"
    nvenc_cq: int = 21
    nvenc_preset: str = "p5"
    audio_bitrate: str = "192k"
    audio_rate: int = 48000
    loudness_lufs: float = -14.0
    true_peak_db: float = -1.0
    music_volume_db: float = -22.0
    overlay_on_media: bool = False  # draw the scene's overlay text on photos (off by request)
    max_speedup: float = 1.08  # narration may be sped up this much to fit max_duration_s
    motion_zoom: float = 0.10
    # Still scenes longer than this cut halfway to a close-up of the same picture, so the
    # picture changes every few seconds (0 = off).
    punch_in_min_s: float = 4.0
    fit_mode: Literal["auto", "crop", "pad"] = "auto"
    min_duration_s: float = 45.0
    max_duration_s: float = 90.0
    duration_tolerance_s: float = 0.15
    scene_timeout_s: float = 600.0
    final_timeout_s: float = 1800.0


class ResourceConfig(_Section):
    vram_headroom_mib: int = 768
    ram_headroom_mib: int = 3072
    gpu_lock_timeout_s: float = 900.0
    nvenc_vram_mib: int = 300
    sample_interval_s: float = 1.0


class ResearchConfig(_Section):
    # Name fragments used to find the research column in the calendar.
    calendar_column_hints: list[str] = Field(default_factory=lambda: ["research", "source"])
    # With no research in the calendar, look the topic up on English Wikipedia (free API)
    # as a starting source. Its claims stay flagged for your fact-check.
    auto_wikipedia: bool = True
    max_sources: int = 4
    excerpt_chars: int = 1500


class VisualsConfig(_Section):
    # Where scene pictures come from, in order. No generated graphics: if every source
    # fails for a scene, the assets step stops and asks you to pick or upload a photo.
    order: list[str] = Field(default_factory=lambda: ["library", "commons", "openverse",
                                                      "comfyui"])
    commons_licenses: list[str] = Field(default_factory=lambda: [
        "public domain", "pd", "cc0", "cc by 1.0", "cc by 2.0", "cc by 2.5", "cc by 3.0",
        "cc by 4.0", "cc-by-1.0", "cc-by-2.0", "cc-by-2.5", "cc-by-3.0", "cc-by-4.0"])
    commons_min_width: int = 900
    commons_candidates: int = 12


class StockConfig(_Section):
    pexels_enabled: bool = False
    max_download_mb: int = 80
    timeout_s: float = 30.0
    per_scene_candidates: int = 3


class ComfyConfig(_Section):
    enabled: bool = True
    use_by_default: bool = True  # every scene gets an AI image unless a library clip matches
    host: str = "http://comfyui:8188"  # compose service name, seen from the dev container
    workflow: str = "config/comfyui/sd15_portrait.json"
    checkpoint: str = "v1-5-pruned-emaonly.safetensors"
    checkpoint_license: str = "CreativeML OpenRAIL-M (stable-diffusion-v1-5)"
    checkpoint_commercial_use: bool = True  # OpenRAIL-M allows commercial use of outputs
    style: str = ("professional documentary photograph, natural light, photorealistic, "
                  "35mm lens, sharp focus, highly detailed, depth of field")
    width: int = 512
    height: int = 768
    steps: int = 28
    reduced_steps: int = 16
    cfg: float = 7.0
    timeout_s: float = 300.0
    estimated_vram_mib: int = 3500


class CacheConfig(_Section):
    max_gb: float = 10.0


class Settings(_Section):
    project_root: Path = Path(".")
    paths: PathsConfig = Field(default_factory=PathsConfig)
    channel: ChannelConfig = Field(default_factory=ChannelConfig)
    script: ScriptConfig = Field(default_factory=ScriptConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    tts: TTSConfig = Field(default_factory=TTSConfig)
    subtitles: SubtitleConfig = Field(default_factory=SubtitleConfig)
    render: RenderConfig = Field(default_factory=RenderConfig)
    resources: ResourceConfig = Field(default_factory=ResourceConfig)
    research: ResearchConfig = Field(default_factory=ResearchConfig)
    visuals: VisualsConfig = Field(default_factory=VisualsConfig)
    reviewer: str = ""  # your name, recorded with approvals (set it in the dashboard)
    stock: StockConfig = Field(default_factory=StockConfig)
    comfyui: ComfyConfig = Field(default_factory=ComfyConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)

    @field_validator("project_root")
    @classmethod
    def _abs_root(cls, v: Path) -> Path:
        return v.resolve()

    def resolve(self, p: str | Path) -> Path:
        path = Path(p).expanduser()
        return path if path.is_absolute() else (self.project_root / path).resolve()

    @property
    def data_dir(self) -> Path:
        return self.resolve(self.paths.data_dir)

    @property
    def content_dir(self) -> Path:
        return self.resolve(self.paths.content_dir)

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def library_dir(self) -> Path:
        return self.data_dir / "library"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "db" / "faceless.sqlite3"

    @property
    def locks_dir(self) -> Path:
        return self.data_dir / "locks"

    def font_file(self, bold: bool = True) -> Path:
        name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
        return self.resolve(self.paths.fonts_dir) / name


def find_project_root(start: Path | None = None) -> Path:
    env = os.environ.get("FACELESS_HOME")
    if env:
        return Path(env).resolve()
    here = (start or Path.cwd()).resolve()
    for cand in [here, *here.parents]:
        if (cand / "config" / "default.yaml").is_file():
            return cand
    # Fallback: repository layout backend/src/faceless_studio/config.py
    return Path(__file__).resolve().parents[3]


def _deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _env_overrides(environ: dict[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in environ.items():
        if not key.startswith("FACELESS__"):
            continue
        parts = [p.lower() for p in key[len("FACELESS__") :].split("__") if p]
        if len(parts) < 2:
            continue
        node = out
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = yaml.safe_load(value) if value else value
    if environ.get("VIDEO_ENCODER"):
        out.setdefault("render", {}).setdefault("encoder", environ["VIDEO_ENCODER"])
    if environ.get("OLLAMA_HOST"):
        out.setdefault("llm", {}).setdefault("host", environ["OLLAMA_HOST"])
    if environ.get("APP_DATA_DIR"):
        out.setdefault("paths", {}).setdefault("data_dir", environ["APP_DATA_DIR"])
    return out


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top level must be a mapping")
    return data


def load_settings(
    root: Path | None = None,
    extra_file: Path | None = None,
    environ: dict[str, str] | None = None,
) -> Settings:
    root = (root or find_project_root()).resolve()
    merged: dict[str, Any] = {}
    for f in [root / "config" / "default.yaml", root / "config" / "local.yaml", extra_file]:
        if f is not None:
            merged = _deep_merge(merged, _read_yaml(Path(f)))
    merged = _deep_merge(merged, _env_overrides(dict(os.environ if environ is None else environ)))
    merged["project_root"] = root
    return Settings.model_validate(merged)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()
