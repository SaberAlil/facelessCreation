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

# English Kokoro voices (Apache-2.0, same repo as the model). The first letter is the
# accent (a = American, b = British), the second the voice (f = female, m = male).
VOICES = {
    "af_heart": "Heart · warm American female (default)",
    "af_bella": "Bella · lively American female",
    "af_nicole": "Nicole · soft American female",
    "am_michael": "Michael · calm American male",
    "am_fenrir": "Fenrir · deep American male",
    "am_puck": "Puck · energetic American male",
    "bf_emma": "Emma · British female",
    "bm_george": "George · British male",
    "bm_fable": "Fable · young British male",
}
VOICE_MAX_BYTES = 5 * 1024 * 1024


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

    def voice_file(self) -> str:
        """Local path of the voice pack. Voices other than the one installed with the model
        (about 0.5 MB each) are downloaded once from the same pinned model revision; the
        model cache is otherwise used offline."""
        if self.cfg.voice not in VOICES:
            raise FacelessError(f"unknown voice {self.cfg.voice!r}")
        base = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
        snaps = base / ("models--" + self.cfg.kokoro_repo.replace("/", "--")) / "snapshots"
        revs = sorted(snaps.iterdir()) if snaps.is_dir() else []
        if not revs:
            return self.cfg.voice  # first run: kokoro downloads model and voice itself
        f = revs[0] / "voices" / f"{self.cfg.voice}.pt"
        if f.is_file():
            return str(f)
        import httpx

        from ..util import atomic_write_bytes
        url = (f"https://huggingface.co/{self.cfg.kokoro_repo}/resolve/{revs[0].name}/"
               f"voices/{self.cfg.voice}.pt")
        log.info("downloading Kokoro voice %s", self.cfg.voice)
        try:
            r = httpx.get(url, follow_redirects=True, timeout=60)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise ProviderUnavailable(f"could not download voice {self.cfg.voice}: {e}") from e
        if len(r.content) > VOICE_MAX_BYTES:
            raise FacelessError("voice file unexpectedly large")
        atomic_write_bytes(f, r.content)
        return str(f)

    def synthesize(self, text: str, out_wav: Path) -> None:
        import numpy as np
        import soundfile as sf

        if not text.strip():
            raise FacelessError("cannot synthesize empty text")
        pipe = self._pipeline()
        voice = self.voice_file()
        chunks = []
        with self._lock:  # KPipeline is not documented as thread-safe
            for _gs, _ps, audio in pipe(text, voice=voice, speed=self.cfg.speed,
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
