"""FFmpeg rendering: per-scene clips timed to narration → concat + captions + mixed audio.

All FFmpeg calls use argument arrays, bounded timeouts, temporary files and atomic
renames. Filter graphs are built only from validated numbers and escaped paths; no
LLM/user text is placed in a filter graph (captions go through an .ass file and graphics
text through ``textfile=``).
"""

from __future__ import annotations

import logging
import shutil
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

from . import media
from .assets import asset_file
from .config import Settings
from .jobs import JobPaths
from .resources import ResourceUnavailable, check_vram, gpu_lock
from .schemas import AssetManifest, SceneAsset, Script, VoiceTiming
from .util import (
    ToolFailed,
    hash_obj,
    run,
    tmp_path_for,
    write_json,
)
from .util import (
    ffmpeg_filter_escape as esc,
)

log = logging.getLogger("faceless")

MOTIONS = ["zoom_in", "pan_right", "zoom_out", "pan_up", "pan_left", "zoom_in_top"]


@dataclass
class RenderPlan:
    width: int
    height: int
    fps: int
    total_frames: int
    scene_frames: list[int]
    duration: float
    preview: bool
    encoder: str
    commands: list[list[str]] = field(default_factory=list)


def frame_plan(timing: VoiceTiming, fps: int) -> tuple[list[int], int]:
    """Frame counts per scene from real narration timing; sums exactly to the total."""
    starts = [round(t.start * fps) for t in timing.scenes]
    starts[0] = 0
    total = round(timing.duration * fps)
    bounds = [*starts, total]
    frames = [b - a for a, b in zip(bounds, bounds[1:], strict=False)]
    if any(f <= 0 for f in frames):
        raise ToolFailed(f"invalid scene timing (frames {frames})")
    return frames, total


def _fit_filters(src_w: int | None, src_h: int | None, W: int, H: int, mode: str,
                 tag: str) -> str:
    """Aspect-preserving fill (crop) or blurred-background pad, output WxH, ends at [tag]."""
    target = W / H
    ratio = (src_w / src_h) if src_w and src_h else target
    use_pad = mode == "pad" or (mode == "auto" and ratio > target * 1.35)
    if not use_pad:
        return (f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,"
                f"crop={W}:{H},setsar=1[{tag}]")
    return (
        f"split=2[{tag}bg][{tag}fg];"
        f"[{tag}bg]scale={W // 8}:{H // 8}:force_original_aspect_ratio=increase,"
        f"crop={W // 8}:{H // 8},boxblur=8:2,eq=brightness=-0.08,scale={W}:{H}[{tag}b];"
        f"[{tag}fg]scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos[{tag}f];"
        f"[{tag}b][{tag}f]overlay=(W-w)/2:(H-h)/2,setsar=1[{tag}]"
    )


def _zoompan(motion: str, frames: int, W: int, H: int, fps: int, zoom: float) -> str:
    n = max(frames - 1, 1)
    z_in = f"1+{zoom}*on/{n}"
    z_out = f"1+{zoom}-{zoom}*on/{n}"
    zc = f"1+{zoom}"
    cx, cy = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    if motion == "zoom_in":
        z, x, y = z_in, cx, cy
    elif motion == "zoom_out":
        z, x, y = z_out, cx, cy
    elif motion == "pan_right":
        z, x, y = zc, f"(iw-iw/zoom)*on/{n}", cy
    elif motion == "pan_left":
        z, x, y = zc, f"(iw-iw/zoom)*(1-on/{n})", cy
    elif motion == "pan_up":
        z, x, y = zc, cx, f"(ih-ih/zoom)*(1-on/{n})"
    else:  # zoom_in_top
        z, x, y = z_in, cx, "0"
    return f"zoompan=z='{z}':x='{x}':y='{y}':d=1:s={W}x{H}:fps={fps}"


def overlay_filters(text: str, workdir: Path, tag: str, s: Settings, W: int, H: int) -> str:
    """Headline text in a translucent box near the top (above the caption zone).

    Text goes through ``textfile=`` so it is never parsed as filter syntax.
    """
    lines = textwrap.wrap(" ".join(text.split()), 16, break_on_hyphens=False)[:3]
    if not lines:
        return ""
    size = int(W * 0.075)
    y0 = int(H * 0.17)
    out = []
    for i, line in enumerate(lines):
        f = workdir / f".ov-{tag}-{i}.txt"
        f.write_text(line, encoding="utf-8")
        out.append(f"drawtext=fontfile={esc(s.font_file())}:textfile={esc(f)}:expansion=none:"
                   f"fontsize={size}:fontcolor=white:x=(w-text_w)/2:"
                   f"y={int(y0 + i * size * 1.3)}:box=1:boxcolor=black@0.5:"
                   f"boxborderw={max(6, size // 4)}")
    return ",".join(out)


def scene_clip_cmd(asset: SceneAsset, src: Path, frames: int, idx: int, W: int, H: int,
                   fps: int, s: Settings, encoder: str, preview: bool, out: Path,
                   overlay: str = "") -> list[str]:
    dims: tuple[int | None, int | None] = (None, None)
    if asset.kind == "video":
        w, h, _ = media.media_dims(src)
        dims = (w, h)
    dur = frames / fps
    args = ["ffmpeg", "-hide_banner", "-y", "-nostdin"]
    if asset.kind == "video":
        args += ["-stream_loop", "-1", "-i", str(src), "-t", f"{dur + 0.5:.3f}"]
        graph = f"[0:v]fps={fps},{_fit_filters(dims[0], dims[1], W, H, s.render.fit_mode, 'f')}"
        # A gentle push-in keeps short loops from looking static.
        graph += (f";[f]scale={int(W * 1.06) // 2 * 2}:{int(H * 1.06) // 2 * 2},"
                  f"crop={W}:{H}:x='(iw-ow)/2*(1-t/{dur:.3f})':y='(ih-oh)/2'"
                  f"{',' + overlay if overlay else ''},format=yuv420p[v]")
    else:
        # Stills: ``src`` is already fitted at 2x (see prepare_still_cmd); add smooth
        # Ken Burns motion back down to WxH.
        args += ["-loop", "1", "-framerate", str(fps), "-i", str(src)]
        motion = MOTIONS[idx % len(MOTIONS)]
        graph = (f"[0:v]{_zoompan(motion, frames, W, H, fps, s.render.motion_zoom)},"
                 f"{overlay + ',' if overlay else ''}format=yuv420p[v]")
    args += ["-filter_complex", graph, "-map", "[v]", "-frames:v", str(frames), "-r", str(fps),
             "-an", *media.video_codec_args(encoder, s.render, preview, intermediate=True),
             "-pix_fmt", "yuv420p", str(out)]
    return args


def prepare_still_cmd(src: Path, W: int, H: int, fit_mode: str, out: Path) -> list[str]:
    """Fit a still once at 2x the output size (crop or blurred pad) as a PNG."""
    w, h, _ = media.media_dims(src)
    graph = f"[0:v]{_fit_filters(w, h, W * 2, H * 2, fit_mode, 'f')}"
    return ["ffmpeg", "-hide_banner", "-y", "-nostdin", "-i", str(src), "-filter_complex",
            graph, "-map", "[f]", "-frames:v", "1", "-update", "1", str(out)]


def audio_mix_cmd(narration: Path, music: Path | None, duration: float, s: Settings,
                  out: Path) -> list[str]:
    r = s.render
    args = ["ffmpeg", "-hide_banner", "-y", "-nostdin", "-i", str(narration)]
    fade_st = max(0.0, duration - 0.5)
    if music is None:
        graph = (f"[0:a]aresample={r.audio_rate},aformat=channel_layouts=stereo,"
                 f"apad,atrim=0:{duration:.3f}[a]")
    else:
        args += ["-stream_loop", "-1", "-i", str(music)]
        graph = (
            f"[0:a]aresample={r.audio_rate},aformat=channel_layouts=stereo,apad,"
            f"atrim=0:{duration:.3f},asplit=2[n][sc];"
            f"[1:a]aresample={r.audio_rate},aformat=channel_layouts=stereo,"
            f"atrim=0:{duration:.3f},volume={r.music_volume_db}dB,"
            f"afade=t=in:d=1.0,afade=t=out:st={fade_st:.3f}:d=0.5[m];"
            "[m][sc]sidechaincompress=threshold=0.03:ratio=6:attack=30:release=350[duck];"
            "[n][duck]amix=inputs=2:duration=first:normalize=0[a]"
        )
    args += ["-filter_complex", graph, "-map", "[a]", "-c:a", "pcm_s16le", "-ar",
             str(r.audio_rate), "-ac", "2", str(out)]
    return args


def final_cmd(concat_list: Path, audio: Path, ass: Path | None, plan: RenderPlan, s: Settings,
              fonts_dir: Path, out: Path) -> list[str]:
    vf = []
    if ass is not None:
        vf.append(f"ass=filename={esc(ass)}:fontsdir={esc(fonts_dir)}")
    fade_out = max(0.0, plan.duration - 0.35)
    vf += ["fade=t=in:st=0:d=0.25", f"fade=t=out:st={fade_out:.3f}:d=0.35", "format=yuv420p"]
    return [
        "ffmpeg", "-hide_banner", "-y", "-nostdin", "-f", "concat", "-safe", "0",
        "-i", str(concat_list), "-i", str(audio), "-map", "0:v:0", "-map", "1:a:0",
        # No -frames:v here: it ends the mux before trailing audio packets are written.
        # The concatenated clips already have exactly ``total_frames`` frames.
        "-vf", ",".join(vf), "-r", str(plan.fps),
        *media.video_codec_args(plan.encoder, s.render, plan.preview), "-pix_fmt", "yuv420p",
        "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
        "-c:a", "aac", "-b:a", s.render.audio_bitrate, "-ar", str(s.render.audio_rate),
        "-ac", "2", "-t", f"{plan.total_frames / plan.fps:.3f}",
        "-movflags", "+faststart", str(out),
    ]


def cover_cmd(src: Path, is_video: bool, title: str, workdir: Path, s: Settings, out: Path,
              width: int, height: int, accent: str = "0xFFC83D") -> list[str]:
    """Cover: first scene's visual, blurred and darkened, with the title on top.

    Built from the source visual (not the final video) so burned-in captions and scene
    text never collide with the title.
    """
    # DejaVu Sans Bold averages ~0.65 em per character: 12 chars at 0.092*W fit 84% of W.
    lines = textwrap.wrap(title, 12)[:5]
    size = int(width * 0.092)
    block = len(lines) * size * 1.2
    y0 = int(height * 0.42 - block / 2)
    filters = [f"scale={width}:{height}:force_original_aspect_ratio=increase",
               f"crop={width}:{height}", "gblur=sigma=40", "eq=brightness=-0.18:saturation=0.9",
               f"drawbox=x={int(width * 0.08)}:y={int(y0 - size * 0.7)}:w={int(width * 0.18)}:"
               f"h={max(4, width // 120)}:color={accent}:t=fill"]
    for i, line in enumerate(lines):
        f = workdir / f".cover{i}.txt"
        f.write_text(line, encoding="utf-8")
        filters.append(
            f"drawtext=fontfile={esc(s.font_file())}:textfile={esc(f)}:expansion=none:"
            f"fontsize={size}:fontcolor=white:x={int(width * 0.08)}:"
            f"y={int(y0 + i * size * 1.2)}:borderw=3:bordercolor=black@0.5")
    pre = ["-ss", "1"] if is_video else []
    return ["ffmpeg", "-hide_banner", "-y", "-nostdin", *pre, "-i", str(src),
            "-frames:v", "1", "-vf", ",".join(filters), "-update", "1", str(out)]


def _write_concat(files: list[Path], out: Path) -> None:
    lines = []
    for f in files:
        p = str(f.resolve()).replace("'", "'\\''")
        lines.append(f"file '{p}'")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def render_job(s: Settings, paths: JobPaths, script: Script, timing: VoiceTiming,
               manifest: AssetManifest, ass: Path | None, preview: bool = False,
               dry_run: bool = False, force_encoder: str | None = None) -> dict:
    """Render the job. Returns details (encoder, fallbacks, outputs, commands)."""
    media.require_ffmpeg()
    if [a.scene_id for a in manifest.scenes] != [t.scene_id for t in timing.scenes]:
        raise ToolFailed("asset manifest and narration timing list different scenes; "
                         "re-run 'faceless assets' and 'faceless voice'")
    fallbacks: list[str] = []
    if force_encoder:
        encoder = force_encoder
        reason = "forced"
    else:
        choice = media.select_encoder(s.render)
        encoder, reason = choice.name, choice.reason
    if encoder == "h264_nvenc" and not dry_run:
        try:
            check_vram(s.resources.nvenc_vram_mib, s.resources.vram_headroom_mib, "nvenc")
        except ResourceUnavailable as e:
            fallbacks.append(f"libx264: {e}")
            encoder = "libx264"
    try:
        return _render(s, paths, timing, manifest, ass, preview, dry_run, encoder, reason,
                       fallbacks, script)
    except ToolFailed as e:
        if encoder != "h264_nvenc":
            raise
        log.warning("NVENC render failed (%s); retrying with libx264", str(e)[:200])
        fallbacks.append(f"libx264 after NVENC failure: {str(e)[:300]}")
        return _render(s, paths, timing, manifest, ass, preview, dry_run, "libx264",
                       "fallback after NVENC runtime failure", fallbacks, script)


def _render(s: Settings, paths: JobPaths, timing: VoiceTiming, manifest: AssetManifest,
            ass: Path | None, preview: bool, dry_run: bool, encoder: str, reason: str,
            fallbacks: list[str], script: Script) -> dict:
    r = s.render
    W, H = (r.preview_width, r.preview_height) if preview else (r.width, r.height)
    frames, total = frame_plan(timing, r.fps)
    plan = RenderPlan(W, H, r.fps, total, frames, total / r.fps, preview, encoder)
    kind = "preview" if preview else "final"
    work = paths.render_dir / f"work-{kind}"
    clip_dir = paths.render_dir / "scenes" / f"{kind}-{W}x{H}"
    out = paths.preview if preview else paths.final
    if not dry_run:
        work.mkdir(parents=True, exist_ok=True)
        clip_dir.mkdir(parents=True, exist_ok=True)

    lock = gpu_lock(s.locks_dir, f"render:{paths.root.name}", s.resources.gpu_lock_timeout_s) \
        if encoder == "h264_nvenc" and not dry_run else _nullctx()
    clips: list[Path] = []
    reused = 0
    # Original graphics already contain their text; photos, AI images and clips get the
    # scene's overlay drawn on top (the "main" on-screen text).
    overlays = {sc.id: sc.overlay for sc in script.scenes if sc.overlay and r.overlay_on_media}
    overlays = {k: v for k, v in overlays.items()
                if next((a.kind for a in manifest.scenes if a.scene_id == k), "") != "procedural"}
    with lock:
        for i, (asset, n) in enumerate(zip(manifest.scenes, frames, strict=True)):
            src = asset_file(paths, asset)
            key = hash_obj({"sha": asset.sha256, "n": n, "i": i, "W": W, "H": H, "fps": r.fps,
                            "enc": encoder, "zoom": r.motion_zoom, "fit": r.fit_mode,
                            "q": media.video_codec_args(encoder, r, preview, True), "v": 3,
                            "ov": overlays.get(asset.scene_id, "")})[:16]
            clip = clip_dir / f"{asset.scene_id}-{key}.mp4"
            clips.append(clip)
            if asset.kind != "video":
                still = clip_dir / f"{asset.scene_id}-{asset.sha256[:12]}-{r.fit_mode}.png"
                pcmd = prepare_still_cmd(src, W, H, r.fit_mode, tmp_path_for(still))
                plan.commands.append(pcmd)
                if not dry_run and not still.is_file() and not clip.is_file():
                    run(pcmd, timeout=120, log_file=paths.render_log)
                    tmp_path_for(still).replace(still)
                src = still
            ov = overlays.get(asset.scene_id, "")
            ov_filter = overlay_filters(ov, clip_dir, asset.scene_id, s, W, H) \
                if ov and not dry_run else (f"drawtext=<overlay: {ov}>" if ov else "")
            cmd = scene_clip_cmd(asset, src, n, i, W, H, r.fps, s, encoder, preview,
                                 tmp_path_for(clip), ov_filter)
            plan.commands.append(cmd)
            if dry_run:
                continue
            if clip.is_file():
                reused += 1
                continue
            run(cmd, timeout=r.scene_timeout_s, log_file=paths.render_log)
            tmp_path_for(clip).replace(clip)

        concat = work / "concat.txt"
        mix = work / "mix.wav"
        mix_norm = work / "mix.norm.wav"
        music = asset_file(paths, manifest.music) if manifest.music else None
        acmd = audio_mix_cmd(paths.narration, music, plan.duration, s, mix)
        fcmd = final_cmd(concat, mix_norm, ass, plan, s, s.resolve(s.paths.fonts_dir),
                         tmp_path_for(out))
        plan.commands += [acmd, fcmd]
        if dry_run:
            return {"dry_run": True, "encoder": encoder, "commands": plan.commands,
                    "frames": frames, "total_frames": total}
        _write_concat(clips, concat)
        run(acmd, timeout=600, log_file=paths.render_log)
        loud = media.normalize_audio(mix, mix_norm, r.loudness_lufs, r.true_peak_db,
                                     r.audio_rate, channels=2, log_file=paths.render_log)
        run(fcmd, timeout=r.final_timeout_s, log_file=paths.render_log)
        tmp_path_for(out).replace(out)

    cover = paths.render_dir / ("cover.preview.png" if preview else "cover.png")
    first = manifest.scenes[0]
    try:
        run(cover_cmd(asset_file(paths, first), first.kind == "video", script.title, work, s,
                      tmp_path_for(cover), W, H), timeout=60, log_file=paths.render_log)
        tmp_path_for(cover).replace(cover)
    finally:
        for f in work.glob(".cover*.txt"):
            f.unlink(missing_ok=True)
    # Clean stale clips from previous plans for this resolution.
    keep = {c.name for c in clips}
    keep |= {f"{a.scene_id}-{a.sha256[:12]}-{r.fit_mode}.png" for a in manifest.scenes}
    for f in clip_dir.glob(".ov-*.txt"):
        f.unlink(missing_ok=True)
    for old in [*clip_dir.glob("*.mp4"), *clip_dir.glob("*.png")]:
        if old.name not in keep:
            old.unlink(missing_ok=True)
    shutil.rmtree(work, ignore_errors=True)
    return {"encoder": encoder, "encoder_reason": reason, "fallbacks": fallbacks,
            "output": str(out), "cover": str(cover), "width": W, "height": H, "fps": r.fps,
            "frames": frames, "total_frames": total, "duration_s": plan.duration,
            "clips_reused": reused, "loudness": loud, "preview": preview}


class _nullctx:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *a: object) -> None:
        return None


def save_render_details(paths: JobPaths, details: dict) -> None:
    write_json(paths.render_dir / ("render.preview.json" if details.get("preview")
                                   else "render.json"), details)
