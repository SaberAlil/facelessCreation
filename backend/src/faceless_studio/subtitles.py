"""Captions from real narration timing.

Text comes from the approved script (correct spelling, numbers, names); timing comes
from faster-whisper word timestamps on the actual narration. Differences between the
transcript and the script are reported for review. If transcription is unavailable, an
approximate fallback distributes words over each scene's real audio span and must be
reviewed before final approval.
"""

from __future__ import annotations

import difflib
import logging
import re
from pathlib import Path

from .config import SubtitleConfig
from .schemas import Caption, CaptionTrack, Script, VoiceTiming, Word
from .util import ProviderUnavailable, ValidationFailed, atomic_write_text, prefer_offline

log = logging.getLogger("faceless")

_NUM_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
    "seven": "7", "eight": "8", "nine": "9", "ten": "10", "eleven": "11", "twelve": "12",
    "twenty": "20", "thirty": "30", "forty": "40", "fifty": "50", "hundred": "100",
    "percent": "%",
}


def norm_token(w: str) -> str:
    t = w.lower().strip()
    t = t.replace("’", "'")
    t = re.sub(r"^[^\w$%]+|[^\w%]+$", "", t)
    return _NUM_WORDS.get(t, t)


def script_words(text: str) -> list[str]:
    return [w for w in text.split() if w.strip()]


# --------------------------------------------------------------- transcription

_models: dict[tuple, object] = {}


def transcribe(audio: Path, cfg: SubtitleConfig, model_name: str | None = None,
               prompt: str | None = None) -> tuple[list[Word], str]:
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        raise ProviderUnavailable("faster-whisper is not installed",
                                  hint="bash .devcontainer/install-ai-deps.sh") from e
    name = model_name or cfg.model
    key = (name, cfg.device, cfg.compute_type)
    if key not in _models:
        prefer_offline(f"Systran/faster-whisper-{name}")
        log.info("loading faster-whisper %s (%s, %s)", name, cfg.device, cfg.compute_type)
        _models[key] = WhisperModel(name, device=cfg.device, compute_type=cfg.compute_type)
    model = _models[key]
    segments, _info = model.transcribe(
        str(audio), language="en", word_timestamps=True, beam_size=5,
        condition_on_previous_text=False, initial_prompt=(prompt or "")[:400] or None,
    )
    words: list[Word] = []
    for seg in segments:
        for w in seg.words or []:
            if w.word.strip():
                words.append(Word(text=w.word.strip(), start=float(w.start), end=float(w.end),
                                  probability=float(w.probability)))
    return words, name


# ------------------------------------------------------------------ alignment


def align(script_text: str, heard: list[Word]) -> tuple[list[Word], float, list[str]]:
    """Map Whisper timings onto the script's words.

    Returns (timed script words, similarity ratio, human-readable divergences).
    """
    sw = script_words(script_text)
    if not heard:
        raise ValidationFailed("transcription returned no words")
    a = [norm_token(w) for w in sw]
    b = [norm_token(w.text) for w in heard]
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    times: list[tuple[float, float] | None] = [None] * len(sw)
    divergences: list[str] = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            for k in range(i2 - i1):
                times[i1 + k] = (heard[j1 + k].start, heard[j1 + k].end)
        elif op == "replace":
            span_s, span_e = heard[j1].start, heard[j2 - 1].end
            seg = sw[i1:i2]
            weights = [max(1, len(w)) for w in seg]
            tot = sum(weights)
            t = span_s
            for k, wgt in enumerate(weights):
                d = (span_e - span_s) * wgt / tot
                times[i1 + k] = (t, t + d)
                t += d
            said = " ".join(w.text for w in heard[j1:j2])
            if not _equivalent(seg, heard[j1:j2]):
                divergences.append(f"script '{' '.join(seg)}' / heard '{said}'")
        elif op == "delete":
            divergences.append(f"not heard: '{' '.join(sw[i1:i2])}'")
        elif op == "insert":
            divergences.append(f"extra in audio: '{' '.join(w.text for w in heard[j1:j2])}'")
    _interpolate(times, sw)
    out = [Word(text=w, start=round(t[0], 3), end=round(t[1], 3))
           for w, t in zip(sw, times, strict=True)]  # type: ignore[index]
    return out, round(sm.ratio(), 4), divergences


def _equivalent(seg: list[str], heard: list[Word]) -> bool:
    """Numbers often come back as digits ('ninety-nine' → '99'): don't flag those."""
    joined_a = re.sub(r"[^a-z0-9]", "", " ".join(seg).lower())
    joined_b = re.sub(r"[^a-z0-9]", "", " ".join(w.text for w in heard).lower())
    if joined_a == joined_b:
        return True
    digits_b = re.sub(r"\D", "", joined_b)
    return bool(digits_b) and any(x in joined_a for x in ("hundred", "ninety", "thousand",
                                                          "percent", "million", "dollar"))


def _interpolate(times: list[tuple[float, float] | None], sw: list[str]) -> None:
    n = len(times)
    i = 0
    while i < n:
        if times[i] is not None:
            i += 1
            continue
        j = i
        while j < n and times[j] is None:
            j += 1
        left = times[i - 1][1] if i > 0 and times[i - 1] else 0.0
        right = times[j][0] if j < n and times[j] else left + 0.3 * (j - i)
        right = max(right, left + 0.05 * (j - i))
        step = (right - left) / (j - i)
        for k in range(i, j):
            times[k] = (left + step * (k - i), left + step * (k - i + 1))
        i = j


def approximate_words(script: Script, timing: VoiceTiming) -> list[Word]:
    """Fallback: spread words over each scene's measured speech span by character count."""
    out: list[Word] = []
    by_id = {t.scene_id: t for t in timing.scenes}
    for sc in script.scenes:
        t = by_id[sc.id]
        ws = script_words(sc.narration)
        weights = [len(w) + 1 for w in ws]
        tot = sum(weights)
        cur = t.start
        span = t.speech_end - t.start
        for w, wt in zip(ws, weights, strict=True):
            d = span * wt / tot
            out.append(Word(text=w, start=round(cur, 3), end=round(cur + d, 3)))
            cur += d
    return out


def scene_bounds_from_words(script: Script, words: list[Word]) -> list[tuple[float, float]]:
    """(start, speech_end) per scene, from aligned script words."""
    out = []
    k = 0
    for sc in script.scenes:
        n = len(script_words(sc.narration))
        seg = words[k:k + n]
        k += n
        out.append((seg[0].start, seg[-1].end))
    return out


# ------------------------------------------------------------------- chunking

_BREAK_AFTER = re.compile(r"[.!?;:,—–-]$")


def chunk_words(words: list[Word], cfg: SubtitleConfig) -> list[Caption]:
    caps: list[Caption] = []
    cur: list[Word] = []

    def text_len(ws: list[Word]) -> int:
        return len(" ".join(w.text for w in ws))

    def flush() -> None:
        if cur:
            caps.append(Caption(index=len(caps) + 1, start=cur[0].start, end=cur[-1].end,
                                text=layout_lines(" ".join(w.text for w in cur), cfg)))
            cur.clear()

    max_chars = cfg.max_chars_per_line * cfg.max_lines
    for i, w in enumerate(words):
        if cur:
            gap = w.start - cur[-1].end
            too_long = text_len([*cur, w]) > max_chars or len(cur) >= cfg.max_words
            sentence_end = re.search(r"[.!?]$", cur[-1].text) is not None
            soft = _BREAK_AFTER.search(cur[-1].text) is not None and len(cur) >= cfg.min_words
            if too_long or gap > 0.35 or sentence_end or soft:
                flush()
        cur.append(w)
        # avoid a dangling single word at the very end
        if i == len(words) - 1:
            if len(cur) == 1 and caps and text_len(cur) + len(caps[-1].text) < max_chars:
                last = caps.pop()
                cur.insert(0, Word(text=last.text.replace("\n", " "), start=last.start,
                                   end=last.end))
            flush()
    # timing polish: hold captions through short gaps, enforce minimum duration, no overlap
    for i, c in enumerate(caps):
        nxt = caps[i + 1].start if i + 1 < len(caps) else None
        if nxt is not None and 0 < nxt - c.end < 0.3:
            c.end = nxt
        if c.end - c.start < cfg.min_duration_s:
            c.end = c.start + cfg.min_duration_s
            if nxt is not None:
                c.end = min(c.end, nxt)
        c.start, c.end = round(c.start, 3), round(c.end, 3)
    return caps


def layout_lines(text: str, cfg: SubtitleConfig) -> str:
    """Split into at most ``max_lines`` balanced lines."""
    text = " ".join(text.split())
    if len(text) <= cfg.max_chars_per_line or cfg.max_lines < 2:
        return text
    words = text.split()
    best, best_score = text, 1e9
    for k in range(1, len(words)):
        a, b = " ".join(words[:k]), " ".join(words[k:])
        score = abs(len(a) - len(b)) + 100 * (max(len(a), len(b)) > cfg.max_chars_per_line)
        if score < best_score:
            best, best_score = f"{a}\n{b}", score
    return best


# ----------------------------------------------------------- formats & checks


def _ts_srt(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _ts_ass(t: float) -> str:
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360_000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def to_srt(caps: list[Caption]) -> str:
    out = []
    for i, c in enumerate(caps, 1):
        out.append(f"{i}\n{_ts_srt(c.start)} --> {_ts_srt(c.end)}\n{c.text}\n")
    return "\n".join(out)


_SRT_TIME = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})")


def parse_srt(text: str) -> list[Caption]:
    caps: list[Caption] = []
    blocks = re.split(r"\n\s*\n", text.replace("\r\n", "\n").strip())
    for b in blocks:
        lines = [ln for ln in b.split("\n") if ln.strip()]
        if not lines:
            continue
        ti = next((i for i, ln in enumerate(lines) if _SRT_TIME.search(ln)), None)
        if ti is None:
            raise ValidationFailed(f"SRT block without a timestamp: {b[:60]!r}")
        m = _SRT_TIME.search(lines[ti])
        g = [int(x) for x in m.groups()]  # type: ignore[union-attr]
        start = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
        end = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
        body = "\n".join(re.sub(r"<[^>]+>", "", ln).strip() for ln in lines[ti + 1:])
        caps.append(Caption(index=len(caps) + 1, start=start, end=end, text=body))
    return caps


def validate_captions(caps: list[Caption], audio_duration: float, cfg: SubtitleConfig
                      ) -> list[str]:
    problems = []
    if not caps:
        return ["no captions"]
    prev_end = 0.0
    for c in caps:
        tag = f"caption {c.index} ({_ts_srt(c.start)})"
        if not c.text.strip():
            problems.append(f"{tag}: empty text")
        if c.end <= c.start:
            problems.append(f"{tag}: ends before it starts")
        elif c.end - c.start < 0.2:
            problems.append(f"{tag}: shorter than 0.2s")
        if c.start < prev_end - 0.001:
            problems.append(f"{tag}: overlaps the previous caption")
        if c.end > audio_duration + 0.25:
            problems.append(f"{tag}: ends after the narration ({audio_duration:.2f}s)")
        lines = c.text.split("\n")
        if len(lines) > cfg.max_lines:
            problems.append(f"{tag}: {len(lines)} lines (max {cfg.max_lines})")
        if any(len(ln) > cfg.max_chars_per_line + 8 for ln in lines):
            problems.append(f"{tag}: line too long for the frame")
        prev_end = c.end
    return problems


def _ass_escape(text: str) -> str:
    # Neutralise override blocks and line-break escapes from user text.
    t = text.replace("\\", "⧵").replace("{", "(").replace("}", ")")
    return t.replace("\n", "\\N")


def to_ass(caps: list[Caption], cfg: SubtitleConfig, width: int, height: int,
           keywords: set[str] | None = None) -> str:
    style = (f"Style: Default,{cfg.font},{cfg.font_size},{cfg.primary_colour},"
             f"{cfg.accent_colour},{cfg.outline_colour},&H64000000,-1,0,0,0,100,100,0,0,1,"
             f"{cfg.outline},{cfg.shadow},2,{cfg.margin_l},{cfg.margin_r},{cfg.margin_v},1")
    head = (
        "[Script Info]\nScriptType: v4.00+\nWrapStyle: 2\nScaledBorderAndShadow: yes\n"
        f"PlayResX: {width}\nPlayResY: {height}\nYCbCr Matrix: TV.709\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, "
        "Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, "
        f"Encoding\n{style}\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, "
        "MarginR, MarginV, Effect, Text\n"
    )
    lines = []
    for c in caps:
        text = _ass_escape(c.text)
        if cfg.highlight_keywords and keywords:
            def hl(m: re.Match) -> str:
                w = m.group(0)
                if norm_token(w) in keywords:
                    return "{\\c" + cfg.accent_colour + "&}" + w + "{\\c" + \
                        cfg.primary_colour + "&}"
                return w
            text = re.sub(r"[\w$%'.-]+", hl, text)
        # gentle 120 ms fade-in, no flashing
        lines.append(f"Dialogue: 0,{_ts_ass(c.start)},{_ts_ass(c.end)},Default,,0,0,0,,"
                     f"{{\\fad(120,0)}}{text}")
    return head + "\n".join(lines) + "\n"


def write_tracks(track: CaptionTrack, srt: Path, ass: Path, cfg: SubtitleConfig,
                 width: int, height: int, keywords: set[str] | None = None) -> None:
    atomic_write_text(srt, to_srt(track.captions))
    atomic_write_text(ass, to_ass(track.captions, cfg, width, height, keywords))
