"""Original procedural graphics (no third-party media) rendered with FFmpeg.

Every graphic is a 1080x1920 PNG; motion is added at render time. Text is always passed
through ``textfile=`` so script content is never interpreted as filter syntax.
"""

from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass
from pathlib import Path

from .util import ffmpeg_filter_escape as esc
from .util import run

# Calm, high-contrast palettes (background A, background B, accent, text)
PALETTES = [
    ("0x0B1D3A", "0x1F4E79", "0xFFC83D", "white"),
    ("0x1B1B2F", "0x3A2E5C", "0x4FD1C5", "white"),
    ("0x0F3D3E", "0x1E6F5C", "0xF6E05E", "white"),
    ("0x2D1E2F", "0x6B2D5C", "0xF4A261", "white"),
    ("0x10141F", "0x283655", "0x7FDBFF", "white"),
    ("0x22313F", "0x3C5A73", "0xFF7A59", "white"),
]

TEMPLATES = ["headline", "number", "keyword", "grid", "orb"]

# A stand-alone number token ("$9.99", "40%", "3x"), not part of a word like "9-something".
_NUMBER = re.compile(r"(?<!\S)(?:[$€£]?\d[\d,.]*%?|\d+(?:\.\d+)?x)(?=[\s,.;:!?]|$)")


def _wrap(text: str, width: int) -> list[str]:
    return textwrap.wrap(text, width, break_on_hyphens=False, break_long_words=False)


@dataclass
class Graphic:
    template: str
    palette: int
    text: str


def plan_graphic(index: int, overlay: str, title: str | None = None) -> Graphic:
    text = overlay.strip() or (title or "")
    if _NUMBER.match(text) and len(text) <= 24:
        template = "number"
    else:
        # Rotate through the non-number templates so neighbours never repeat.
        others = [t for t in TEMPLATES if t != "number"]
        template = others[index % len(others)]
    return Graphic(template=template, palette=index % len(PALETTES), text=text)


def _text_filters(lines: list[str], workdir: Path, font: Path, size: int, color: str,
                  y0: int, line_gap: float = 1.18, tag: str = "t") -> list[str]:
    out = []
    for i, line in enumerate(lines):
        f = workdir / f".{tag}{i}.txt"
        f.write_text(line, encoding="utf-8")
        y = int(y0 + i * size * line_gap)
        out.append(
            f"drawtext=fontfile={esc(font)}:textfile={esc(f)}:expansion=none:fontsize={size}:"
            f"fontcolor={color}:x=(w-text_w)/2:y={y}:borderw=3:bordercolor=black@0.35:"
            "shadowx=0:shadowy=4:shadowcolor=black@0.35"
        )
    return out


def render_graphic(g: Graphic, out_png: Path, font: Path, width: int = 1080,
                   height: int = 1920, timeout: float = 60) -> None:
    a, b, accent, fg = PALETTES[g.palette]
    out_png.parent.mkdir(parents=True, exist_ok=True)
    work = out_png.parent
    tag = out_png.stem
    base = (f"gradients=s={width}x{height}:c0={a}:c1={b}:x0=0:y0=0:x1={width}:y1={height}:"
            "nb_colors=2:seed=1")
    chain: list[str] = []
    text = " ".join(g.text.split())
    if g.template == "number":
        m = _NUMBER.match(text)
        big = m.group(0).strip() if m else text
        rest = (text[: m.start()] + text[m.end():]).strip(" -–:") if m else ""
        chain.append(f"drawbox=x={width // 2 - 160}:y=900:w=320:h=14:color={accent}@0.95:t=fill")
        chain += _text_filters([big], work, font, 210, accent, 600, tag=tag + "n")
        if rest:
            chain += _text_filters(_wrap(rest, 16)[:2], work, font, 72, fg, 960,
                                   tag=tag + "r")
    elif g.template == "keyword":
        chain.append(f"drawbox=x=110:y=430:w=18:h=460:color={accent}@0.95:t=fill")
        chain.append("drawbox=x=0:y=0:w=iw:h=ih:color=black@0.15:t=fill")
        if text:
            chain += _text_filters(_wrap(text.upper(), 12)[:4], work, font, 100, fg,
                                   470, tag=tag + "k")
    elif g.template == "grid":
        chain.append("drawgrid=w=135:h=135:t=2:c=white@0.07")
        chain.append(f"drawbox=x=135:y=405:w=810:h=540:color={accent}@0.12:t=fill")
        chain.append(f"drawbox=x=135:y=405:w=810:h=540:color={accent}@0.9:t=6")
        if text:
            chain += _text_filters(_wrap(text, 12)[:3], work, font, 88, fg, 520,
                                   tag=tag + "g")
    elif g.template == "orb":
        # Soft glowing disc drawn per-pixel (single frame, cheap enough)
        chain.append(
            "geq=r='r(X,Y)+90*exp(-((X-W*0.68)^2+(Y-H*0.30)^2)/(2*170^2))':"
            "g='g(X,Y)+70*exp(-((X-W*0.68)^2+(Y-H*0.30)^2)/(2*170^2))':"
            "b='b(X,Y)+40*exp(-((X-W*0.68)^2+(Y-H*0.30)^2)/(2*170^2))'")
        if text:
            chain += _text_filters(_wrap(text, 14)[:3], work, font, 96, fg, 700,
                                   tag=tag + "o")
    else:  # headline
        chain.append(f"drawbox=x=0:y=360:w=iw:h=8:color={accent}@0.9:t=fill")
        chain.append("drawgrid=w=iw:h=96:t=1:c=white@0.04")
        if text:
            chain += _text_filters(_wrap(text, 14)[:4], work, font, 104, fg, 440,
                                   tag=tag + "h")
    vf = ",".join(["format=rgb24", *chain]) if chain else "format=rgb24"
    tmp = out_png.with_name(f".{out_png.stem}.partial.png")
    try:
        run(["ffmpeg", "-hide_banner", "-y", "-f", "lavfi", "-i", base, "-vf", vf,
             "-frames:v", "1", "-update", "1", str(tmp)], timeout=timeout)
        tmp.replace(out_png)
    finally:
        for f in work.glob(f".{tag}*.txt"):
            f.unlink(missing_ok=True)
