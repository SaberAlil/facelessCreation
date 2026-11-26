"""Export package: MP4, cover, SRT, platform drafts, sources/licenses, metadata, review."""

from __future__ import annotations

import csv
import shutil
from pathlib import Path

from .config import Settings
from .jobs import JobPaths
from .schemas import AssetManifest, JobInput, Research, Script, VoiceTiming
from .util import atomic_write_text, sha256_file, utcnow, write_json

DISCLOSURE = (
    "Disclosure reminder (delete before posting once handled):\n"
    "- The narration voice is synthetic (Kokoro text-to-speech). Check each platform's "
    "current rules on labelling AI-generated or altered content and use its disclosure "
    "setting where required (e.g. YouTube 'altered or synthetic content', TikTok "
    "'AI-generated content' label, Instagram 'AI info').\n"
    "- If any visual is AI-generated and realistic, label it.\n"
    "- Add #ad / paid-partnership disclosure if this post contains affiliate links or "
    "sponsorship.\n"
)


def _credits(manifest: AssetManifest) -> list[str]:
    lines = []
    seen = set()
    for a in manifest.scenes + ([manifest.music] if manifest.music else []):
        if a.provider == "procedural":
            continue
        text = a.attribution or (f"{a.creator} ({a.license})" if a.creator else "")
        if text and text not in seen:
            seen.add(text)
            lines.append(text)
    return lines


def _platform_text(title: str, body: str, tags: list[str], credits: list[str],
                   ai_scenes: list[str] | None = None) -> str:
    parts = [title.strip(), "", body.strip()]
    if credits:
        parts += ["", "Credits: " + "; ".join(credits)]
    if tags:
        parts += ["", " ".join(tags)]
    parts += ["", "---", DISCLOSURE]
    if ai_scenes:
        parts.append(f"- THIS VIDEO CONTAINS AI-GENERATED IMAGES (scenes {', '.join(ai_scenes)}). "
                     "Use the platform's AI-content label if they look realistic.")
    return "\n".join(p for p in parts if p is not None).strip() + "\n"


def build_export(s: Settings, paths: JobPaths, job: JobInput, script: Script,
                 research: Research, manifest: AssetManifest, timing: VoiceTiming,
                 validation: dict, approvals: list[dict], stage_runs: list[dict],
                 render_details: dict) -> dict:
    out = paths.export_dir
    tmp = out.with_name(".export.partial")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    base = f"{job.id}"
    shutil.copy2(paths.final, tmp / f"{base}.mp4")
    shutil.copy2(paths.render_dir / "cover.png", tmp / f"{base}-cover.png")
    import json as _json
    lang = _json.loads(paths.captions_json.read_text(encoding="utf-8")).get("language", "en")
    shutil.copy2(paths.srt, tmp / f"{base}.{lang}.srt")  # the burned-in captions
    en_srt = paths.captions_dir / "captions.en.srt"
    if lang != "en" and en_srt.is_file():
        shutil.copy2(en_srt, tmp / f"{base}.en.srt")

    p = script.platform
    tags = list(dict.fromkeys(p.hashtags + s.channel.default_hashtags))[:12]
    credits = _credits(manifest)
    ai = [a.scene_id for a in manifest.scenes if a.ai_generated]
    yt_title = p.youtube_title or script.title
    atomic_write_text(tmp / "youtube.txt", _platform_text(
        f"TITLE: {yt_title[:100]}", p.youtube_description or script.hook, tags[:5], credits, ai))
    atomic_write_text(tmp / "tiktok.txt", _platform_text(
        "", p.tiktok_caption or script.hook, tags, credits, ai))
    atomic_write_text(tmp / "instagram.txt", _platform_text(
        "", p.instagram_caption or script.hook, tags, credits, ai))

    sources = {
        "research_sources": [x.model_dump(mode="json", exclude={"excerpt"})
                             for x in research.sources],
        "claims": [c.model_dump() for c in script.claims],
        "assets": [a.model_dump(mode="json") for a in manifest.scenes],
        "music": manifest.music.model_dump(mode="json") if manifest.music else None,
        "narration": {"provider": timing.provider, "model": timing.model,
                      "voice": timing.voice,
                      "license": "Apache-2.0 (Kokoro-82M weights and voices)"
                      if timing.provider == "kokoro" else "see provider"},
        "fonts": [{"name": "DejaVu Sans Bold", "license": "Bitstream Vera / DejaVu (free, "
                   "commercial use allowed)"}],
    }
    write_json(tmp / "sources.json", sources)
    files = {}
    for f in sorted(tmp.iterdir()):
        files[f.name] = {"sha256": sha256_file(f), "bytes": f.stat().st_size}
    metadata = {
        "job": job.model_dump(), "title": script.title, "exported": utcnow(),
        "channel": s.channel.name, "duration_s": validation.get("duration"),
        "render": {k: render_details.get(k) for k in ("encoder", "encoder_reason",
                                                       "fallbacks", "width", "height", "fps",
                                                       "loudness")},
        "validation": validation, "files": files,
        "stages": [{k: r.get(k) for k in ("stage", "status", "started", "duration_s",
                                          "device", "peak_rss_mib", "peak_children_rss_mib",
                                          "peak_vram_used_mib", "fallbacks")}
                   for r in stage_runs],
        "script_provider": script.provider,
    }
    write_json(tmp / "metadata.json", metadata)
    write_json(tmp / "review.json", {"approvals": approvals, "exported": utcnow(),
                                     "publish": "manual; no automatic upload"})
    shutil.rmtree(out, ignore_errors=True)
    tmp.replace(out)
    return {"dir": str(out), "files": sorted(f.name for f in out.iterdir())}


TRACKING_COLUMNS = [
    "job_id", "day", "date", "topic", "title", "state", "exported", "duration_s",
    "youtube_url", "tiktok_url", "instagram_url", "publish_date", "views_7d",
    "avg_watch_time_s", "completion_rate", "shares", "saves", "subscribers_gained",
    "affiliate_revenue", "notes",
]
MANUAL_COLUMNS = TRACKING_COLUMNS[8:]


def write_tracking_csv(path: Path, rows: list[dict]) -> int:
    """Write the tracking sheet, keeping manually entered columns from an existing file."""
    previous: dict[str, dict] = {}
    if path.is_file():
        with open(path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                previous[r.get("job_id", "")] = r
    out_rows = []
    for r in rows:
        prev = previous.pop(r["job_id"], {})
        merged = {c: r.get(c, "") for c in TRACKING_COLUMNS}
        for c in MANUAL_COLUMNS:
            merged[c] = prev.get(c, "")
        out_rows.append(merged)
    out_rows += [{c: p.get(c, "") for c in TRACKING_COLUMNS} for p in previous.values()]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.partial")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=TRACKING_COLUMNS)
        w.writeheader()
        w.writerows(out_rows)
    tmp.replace(path)
    return len(out_rows)
