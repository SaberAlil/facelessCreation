from __future__ import annotations

from faceless_studio.config import SubtitleConfig
from faceless_studio.schemas import Caption, Word
from faceless_studio.subtitles import (
    align,
    chunk_words,
    parse_srt,
    to_ass,
    to_srt,
    validate_captions,
)


def W(t, s, e):
    return Word(text=t, start=s, end=e)


def test_align_uses_script_text_and_whisper_timing():
    heard = [W("Prices", 0.0, 0.6), W("that", 0.6, 0.86), W("end", 0.86, 1.1),
             W("in", 1.1, 1.3), W("99", 1.3, 1.74), W("feel", 1.74, 2.1),
             W("cheaper.", 2.1, 2.48)]
    words, ratio, div = align("Prices that end in ninety-nine feel cheaper.", heard)
    assert [w.text for w in words][4] == "ninety-nine"
    assert words[4].start == 1.3 and words[4].end == 1.74
    assert div == []  # digits vs words are not reported as divergences
    assert ratio > 0.8


def test_align_reports_real_divergence_and_interpolates():
    heard = [W("Manage", 0.0, 0.4), W("Thomas", 0.4, 0.9), W("found", 1.2, 1.5)]
    words, ratio, div = align("Manoj Thomas found it", heard)
    assert words[0].text == "Manoj"
    assert any("Manoj" in d for d in div)
    assert words[-1].text == "it" and words[-1].start >= 1.5  # interpolated after last
    assert all(w.end >= w.start for w in words)


def test_chunking_limits():
    cfg = SubtitleConfig()
    text = ("This is a fairly long sentence that should be split into readable chunks, "
            "with a pause. Then another sentence follows here.")
    words = [W(t, i * 0.3, i * 0.3 + 0.28) for i, t in enumerate(text.split())]
    caps = chunk_words(words, cfg)
    for c in caps:
        lines = c.text.split("\n")
        assert len(lines) <= cfg.max_lines
        assert len(c.text.replace("\n", " ").split()) <= cfg.max_words + 1
    assert validate_captions(caps, 100, cfg) == []
    # sentence boundaries end a caption
    assert any(c.text.endswith("pause.") for c in caps)


def test_srt_roundtrip_and_validation():
    caps = [Caption(index=1, start=0, end=1.2, text="Hello\nthere"),
            Caption(index=2, start=1.2, end=2.5, text="world")]
    back = parse_srt(to_srt(caps))
    assert [(c.start, c.end, c.text) for c in back] == [(0, 1.2, "Hello\nthere"),
                                                        (1.2, 2.5, "world")]
    bad = [Caption(index=1, start=0, end=1, text="a"),
           Caption(index=2, start=0.5, end=0.6, text=""),
           Caption(index=3, start=5, end=9, text="x\ny\nz")]
    probs = " ".join(validate_captions(bad, 6.0, SubtitleConfig()))
    for expected in ("overlaps", "empty", "shorter than", "after the narration", "3 lines"):
        assert expected in probs


def test_ass_neutralises_override_tags():
    caps = [Caption(index=1, start=0, end=1, text="{\\b1}evil\\N text")]
    out = to_ass(caps, SubtitleConfig(), 1080, 1920)
    line = [ln for ln in out.splitlines() if ln.startswith("Dialogue")][0]
    assert "{\\b1}" not in line and "(\\b1)" not in line
    assert "PlayResX: 1080" in out and "PlayResY: 1920" in out
