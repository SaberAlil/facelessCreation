from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from faceless_studio.config import load_settings
from faceless_studio.jobs import Studio
from faceless_studio.pipeline import Pipeline
from faceless_studio.util import run

REPO = Path(__file__).resolve().parents[2]
SAMPLES = REPO / "content" / "samples"

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


@pytest.fixture
def settings(tmp_path: Path):
    env = {
        "FACELESS__PATHS__DATA_DIR": str(tmp_path / "data"),
        "FACELESS__RENDER__ENCODER": "libx264",
        "FACELESS__LLM__HOST": "http://ollama.test",
        # hermetic defaults; ComfyUI and caption translation have their own tests
        "FACELESS__COMFYUI__ENABLED": "false",
        "FACELESS__SUBTITLES__BURN_LANGUAGE": "en",
        "FACELESS__RESEARCH__AUTO_WIKIPEDIA": "false",
    }
    return load_settings(root=REPO, environ=env)


@pytest.fixture
def studio(settings) -> Studio:
    return Studio(settings)


@pytest.fixture
def pipeline(studio) -> Pipeline:
    return Pipeline(studio)


class ToneTTS:
    """TEST FIXTURE: a sine tone whose length follows the word count. Not speech."""

    name = "test-tone"
    demo = True

    def model_id(self) -> str:
        return "test-tone:v1"

    def synthesize(self, text: str, out_wav: Path) -> None:
        dur = max(0.6, 0.3 * len(text.split()))
        out_wav.parent.mkdir(parents=True, exist_ok=True)
        run(["ffmpeg", "-hide_banner", "-y", "-f", "lavfi", "-i",
             f"sine=frequency=330:sample_rate=24000:duration={dur:.2f}", "-af", "volume=0.4",
             "-ac", "1", str(out_wav)], timeout=60)


@pytest.fixture
def tone_tts() -> ToneTTS:
    return ToneTTS()


SHORT_SCRIPT = """# Test Video Title
Hook: Short test hook.
Claim C1: A test claim. [R1]

First scene says a few words for timing tests here.
> visual: price tag | terms: price tag, shop | overlay: $9.99 vs $10 | claims: C1

Second scene follows with more words to check caption chunking works.
> visual: calculator | terms: calculator | overlay: Round up

Third and final scene ends the short test video now.
> visual: city | terms: city | overlay: The end
"""


@pytest.fixture
def short_script(tmp_path: Path) -> Path:
    p = tmp_path / "script.md"
    p.write_text(SHORT_SCRIPT, encoding="utf-8")
    return p


@pytest.fixture
def research_file(tmp_path: Path) -> Path:
    import json

    p = tmp_path / "research.json"
    p.write_text(json.dumps({
        "topic": "t",
        "sources": [{"id": "S1", "url": "https://example.org/a", "title": "A",
                     "published": "2020-01-01", "accessed": "2026-11-26"}],
        "claims": [{"id": "R1", "text": "A test claim", "source_ids": ["S1"],
                    "verified": True}],
    }))
    return p


@pytest.fixture
def small_limits(monkeypatch):
    """Allow short test scripts (normal minimum is 100 words)."""
    def apply(pl: Pipeline) -> Pipeline:
        pl.s.script.hard_words_min = 5
        return pl
    return apply
