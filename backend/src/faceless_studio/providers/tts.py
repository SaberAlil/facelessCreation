"""Text-to-speech providers. Kokoro (local, CPU) is the default.

espeak-ng is available as a clearly labelled *demo* provider for tests and dry runs;
its output is marked ``demo`` and cannot pass final approval.
"""

from __future__ import annotations

import importlib.metadata as md
import logging
import os
import shutil
import threading
from pathlib import Path
from typing import Protocol

from ..config import TTSConfig
from ..util import FacelessError, ProviderUnavailable, prefer_offline, run

log = logging.getLogger("faceless")


class TTSProvider(Protocol):
    name: str
    demo: bool

    def model_id(self) -> str: ...
    def synthesize(self, text: str, out_wav: Path) -> None: ...


class KokoroTTS:
    """hexgrad/Kokoro-82M via the ``kokoro`` package (Apache-2.0 weights and voices)."""

    name = "kokoro"
    demo = False
    _lock = threading.Lock()
    _pipelines: dict[tuple, object] = {}

    def __init__(self, cfg: TTSConfig):
        self.cfg = cfg
        try:
            self.version = md.version("kokoro")
        except md.PackageNotFoundError as e:
            raise ProviderUnavailable(
                "kokoro is not installed",
                hint="bash .devcontainer/install-ai-deps.sh (CPU torch + kokoro), or use "
                "--narration FILE with your own recording",
            ) from e

    def model_id(self) -> str:
        return f"{self.cfg.kokoro_repo}@kokoro-{self.version}:{self.cfg.voice}"

    def _pipeline(self):
        key = (self.cfg.lang_code, self.cfg.kokoro_repo, self.cfg.device)
        with self._lock:
            if key not in self._pipelines:
                os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
                prefer_offline(self.cfg.kokoro_repo)
                import warnings

                warnings.filterwarnings("ignore", category=UserWarning, module="torch")
                warnings.filterwarnings("ignore", category=FutureWarning, module="torch")
                from kokoro import KPipeline

                log.info("loading Kokoro (%s, %s)", self.cfg.kokoro_repo, self.cfg.device)
                self._pipelines[key] = KPipeline(lang_code=self.cfg.lang_code,
                                                 repo_id=self.cfg.kokoro_repo,
                                                 device=self.cfg.device)
            return self._pipelines[key]

    def synthesize(self, text: str, out_wav: Path) -> None:
        import numpy as np
        import soundfile as sf

        if not text.strip():
            raise FacelessError("cannot synthesize empty text")
        pipe = self._pipeline()
        chunks = []
        with self._lock:  # KPipeline is not documented as thread-safe
            for _gs, _ps, audio in pipe(text, voice=self.cfg.voice, speed=self.cfg.speed,
                                        split_pattern=r"\n+"):
                if audio is None:
                    continue
                arr = audio.detach().cpu().numpy() if hasattr(audio, "detach") else audio
                chunks.append(np.asarray(arr, dtype="float32"))
        if not chunks:
            raise FacelessError(f"Kokoro produced no audio for: {text[:60]!r}")
        audio = np.concatenate(chunks)
        peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        if peak < 1e-4:
            raise FacelessError("Kokoro produced silence")
        if peak > 0.99:  # avoid clipping before normalisation
            audio = audio * (0.99 / peak)
        out_wav.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(out_wav), audio, 24000, subtype="PCM_16")


class EspeakTTS:
    """espeak-ng (GPL-3.0 program, invoked as a separate process). Demo quality only."""

    name = "espeak"
    demo = True

    def __init__(self, cfg: TTSConfig):
        self.cfg = cfg
        self.exe = shutil.which("espeak-ng")
        if not self.exe:
            raise ProviderUnavailable("espeak-ng not installed")

    def model_id(self) -> str:
        return "espeak-ng:en-us"

    def synthesize(self, text: str, out_wav: Path) -> None:
        if not text.strip():
            raise FacelessError("cannot synthesize empty text")
        out_wav.parent.mkdir(parents=True, exist_ok=True)
        wpm = int(165 * self.cfg.speed)
        # Text passed as one argv element (no shell); "--" ends option parsing.
        run([self.exe, "-v", "en-us", "-s", str(wpm), "-w", str(out_wav), "--", text],
            timeout=120)


def get_tts(cfg: TTSConfig, name: str | None = None) -> TTSProvider:
    name = name or cfg.provider
    if name == "kokoro":
        return KokoroTTS(cfg)
    if name == "espeak":
        return EspeakTTS(cfg)
    raise FacelessError(f"unknown TTS provider {name!r}")
