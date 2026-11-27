"""Shorts-style output: short beats, word-by-word captions, punch-in cuts, voices."""

from __future__ import annotations

import pytest

from faceless_studio.config import SubtitleConfig
from faceless_studio.providers.llm import split_beats
from faceless_studio.providers.tts import VOICES
from faceless_studio.render import shot_plan
from faceless_studio.schemas import Caption, SceneAsset, Word
from faceless_studio.subtitles import chunk_words, to_ass, validate_captions
from faceless_studio.util import FacelessError


def test_split_beats_short_scenes():
    paras = ["IKEA stores are built like a maze, and that is no accident. The winding path "
             "leads you past every department before you reach the checkout.",
             "Researchers call this the Gruen effect, named after the architect Victor Gruen, "
             "who designed the first enclosed shopping malls in the United States in the "
             "nineteen fifties, and it makes shoppers forget their plan. It works."]
    beats = split_beats(paras)
    assert len(beats) >= 4  # 6 sentences, one cut at a comma
    assert all(len(b.split()) <= 24 for b in beats)
    # nothing lost, order kept
    assert " ".join(beats).split() == " ".join(paras).split()
    assert beats[-1].endswith("It works.")  # a tiny tail joins the previous beat


def _words(text: str, step: float = 0.4) -> list[Word]:
    return [Word(text=w, start=round(i * step, 3), end=round(i * step + step * 0.9, 3))
            for i, w in enumerate(text.split())]


def test_word_captions_one_line_and_highlighted():
    cfg = SubtitleConfig()
    words = _words("The winding path leads shoppers past every single department today.")
    caps = chunk_words(words, cfg)
    assert all(len(c.text.split()) <= 3 and "\n" not in c.text for c in caps)
    assert not validate_captions(caps, 10.0, cfg)
    ass = to_ass(caps, cfg, 1080, 1920, words=words)
    events = [ln for ln in ass.splitlines() if ln.startswith("Dialogue:")]
    assert len(events) == len(words)  # one event per spoken word
    assert all(cfg.accent_colour in e for e in events)
    assert "\\fscx88" in events[0]  # each caption pops in once


def test_word_captions_survive_edited_text():
    cfg = SubtitleConfig()
    cap = Caption(index=1, start=1.0, end=2.0, text="totally new words")
    ass = to_ass([cap], cfg, 1080, 1920, words=_words("old text"))
    events = [ln for ln in ass.splitlines() if ln.startswith("Dialogue:")]
    assert len(events) == 3 and events[0].split(",")[1] == "0:00:01.00"


def test_classic_style_still_available():
    cfg = SubtitleConfig(style="lines")
    cap = Caption(index=1, start=0.0, end=1.0, text="classic two\nline subtitle")
    events = [ln for ln in to_ass([cap], cfg, 1080, 1920).splitlines()
              if ln.startswith("Dialogue:")]
    assert len(events) == 1


def _asset(kind: str = "image") -> SceneAsset:
    return SceneAsset(scene_id="s01", kind=kind, path="x", sha256="0", license="CC0",
                      commercial_use=True, provider="commons")


def test_punch_in_only_for_long_still_scenes():
    assert shot_plan(_asset(), 90, 30, 4.0) == [(90, None)]
    shots = shot_plan(_asset(), 180, 30, 4.0)
    assert [m for _n, m in shots] == [None, "punch_in"] and sum(n for n, _m in shots) == 180
    long = shot_plan(_asset(), 300, 30, 4.0)
    assert [m for _n, m in long] == [None, "punch_in", "punch_low"]
    assert sum(n for n, _m in long) == 300
    assert len(shot_plan(_asset("video"), 300, 30, 4.0)) == 1
    assert len(shot_plan(_asset(), 300, 30, 0)) == 1


def test_job_voice_option(pipeline):
    j = pipeline.studio.create_job("Voice option test").id
    assert pipeline.job_options(j)["voice"] == pipeline.s.tts.voice
    pipeline.set_job_options(j, voice="bm_george")
    cfg = pipeline.tts_config(j)
    assert cfg.voice == "bm_george" and cfg.lang_code == "b"
    assert pipeline.s.tts.voice != "bm_george"  # global default untouched
    with pytest.raises(FacelessError):
        pipeline.set_job_options(j, voice="nope")
    assert set(VOICES) >= {"af_heart", "am_michael", "bf_emma"}
