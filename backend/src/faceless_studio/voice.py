"""Narration: per-scene synthesis (cached), pauses, loudness normalisation, real timing."""

from __future__ import annotations

import logging
import shutil
from collections.abc import Callable
from pathlib import Path

from . import media
from .config import Settings
from .jobs import JobPaths
from .providers.tts import TTSProvider
from .schemas import SceneTiming, Script, VoiceTiming
from .util import FacelessError, GateError, hash_obj, run, tmp_path_for, write_json

log = logging.getLogger("faceless")


def tts_cache_key(provider: TTSProvider, text: str, s: Settings) -> str:
    return hash_obj({"p": provider.name, "m": provider.model_id(), "t": text,
                     "speed": s.tts.speed, "lang": s.tts.lang_code})


def _concat_with_pauses(files: list[Path], pause_s: float, tail_s: float, rate: int,
                        out: Path, log_file: Path) -> None:
    args: list[str] = ["ffmpeg", "-hide_banner", "-y"]
    for f in files:
        args += ["-i", str(f)]
    args += ["-f", "lavfi", "-t", f"{pause_s:.3f}", "-i", f"anullsrc=r={rate}:cl=mono"]
    args += ["-f", "lavfi", "-t", f"{tail_s:.3f}", "-i", f"anullsrc=r={rate}:cl=mono"]
    n = len(files)
    pause_idx, tail_idx = n, n + 1
    parts = []
    graph = []
    for i in range(n):
        graph.append(f"[{i}:a]aresample={rate},aformat=sample_fmts=s16:channel_layouts=mono"
                     f"[a{i}]")
    # Each pause input can only be consumed once: split it.
    if n > 1:
        graph.append(f"[{pause_idx}:a]asplit={n - 1}" + "".join(f"[p{i}]"
                                                                for i in range(n - 1)))
    for i in range(n):
        parts.append(f"[a{i}]")
        if i < n - 1:
            parts.append(f"[p{i}]")
    parts.append(f"[{tail_idx}:a]")
    graph.append("".join(parts) + f"concat=n={len(parts)}:v=0:a=1[out]")
    args += ["-filter_complex", ";".join(graph), "-map", "[out]", "-ar", str(rate), "-ac", "1",
             "-c:a", "pcm_s16le", str(out)]
    run(args, timeout=300, log_file=log_file)


def _fit_duration(s: Settings, files: list[Path], durations: list[float], pause: float,
                  tail: float, log_file: Path) -> tuple[list[float], float]:
    """Keep the narration within the publishable range.

    Over the limit by at most ``render.max_speedup``: time-stretch every scene with
    FFmpeg ``atempo`` (pitch preserved). Further over, or too short: stop with a clear
    message naming what to change.
    """
    fixed = pause * (len(files) - 1) + tail
    total = sum(durations) + fixed
    limit = s.render.max_duration_s - 0.5
    if total < s.render.min_duration_s:
        raise GateError(f"narration is {total:.1f} s; videos must be at least "
                        f"{s.render.min_duration_s:.0f} s",
                        hint="add a sentence or two to the script, then approve it again")
    if total <= limit:
        return durations, 1.0
    factor = sum(durations) / (limit - fixed)
    if factor > s.render.max_speedup:
        order = sorted(range(len(durations)), key=lambda i: -durations[i])[:2]
        raise GateError(
            f"narration is {total:.1f} s; the limit is {s.render.max_duration_s:.0f} s "
            f"(too long to fix by speeding up)",
            hint="shorten the script by about "
            f"{int((total - limit) * 2.2) + 3} words, e.g. in scene(s) "
            + ", ".join(f"#{i + 1} ({durations[i]:.0f} s)" for i in order))
    out = []
    for f in files:
        tmp = tmp_path_for(f)
        run(["ffmpeg", "-hide_banner", "-y", "-i", str(f), "-af", f"atempo={factor:.4f}",
             "-c:a", "pcm_s16le", str(tmp)], timeout=120, log_file=log_file)
        tmp.replace(f)
        out.append(media.duration(f))
    return out, factor


def synthesize_narration(
    s: Settings, paths: JobPaths, script: Script, provider: TTSProvider,
    progress: Callable[[str, int, int], None] | None = None,
) -> tuple[VoiceTiming, dict]:
    vd = paths.voice_dir
    vd.mkdir(parents=True, exist_ok=True)
    cache = s.cache_dir / "tts"
    cache.mkdir(parents=True, exist_ok=True)
    scene_files: list[Path] = []
    durations: list[float] = []
    cache_hits = 0
    for n, scene in enumerate(script.scenes, 1):
        if progress:
            progress("Recording the narration", n - 1, len(script.scenes))
        text = scene.narration.strip()
        if not text:
            raise FacelessError(f"scene {scene.id} has empty narration")
        key = tts_cache_key(provider, text, s)
        cached = cache / f"{key}.wav"
        if cached.is_file() and cached.stat().st_size > 1000:
            cache_hits += 1
        else:
            tmp = tmp_path_for(cached)
            provider.synthesize(text, tmp)
            tmp.replace(cached)
        dest = vd / f"scene_{scene.id}.wav"
        shutil.copyfile(cached, dest)
        d = media.duration(dest)
        if d < 0.3:
            raise FacelessError(f"scene {scene.id}: synthesized audio is only {d:.2f}s")
        scene_files.append(dest)
        durations.append(d)

    pause = s.tts.pause_between_scenes_ms / 1000
    tail = s.tts.tail_silence_ms / 1000
    durations, speedup = _fit_duration(s, scene_files, durations, pause, tail,
                                       paths.logs / "voice.log")
    raw = vd / "narration.raw.wav"
    _concat_with_pauses(scene_files, pause, tail, s.tts.sample_rate, raw, paths.logs / "voice.log")
    tmp = tmp_path_for(paths.narration)
    loud = media.normalize_audio(raw, tmp, s.tts.narration_lufs, -1.5, s.tts.sample_rate,
                                 channels=1, log_file=paths.logs / "voice.log")
    tmp.replace(paths.narration)
    raw.unlink(missing_ok=True)
    total = media.duration(paths.narration)

    timings: list[SceneTiming] = []
    t = 0.0
    for i, (scene, d) in enumerate(zip(script.scenes, durations, strict=True)):
        gap = pause if i < len(durations) - 1 else tail
        end = t + d + gap if i < len(durations) - 1 else total
        timings.append(SceneTiming(scene_id=scene.id, start=round(t, 4),
                                   speech_end=round(t + d, 4), end=round(end, 4),
                                   audio_file=f"voice/scene_{scene.id}.wav",
                                   text=scene.narration))
        t = t + d + gap
    expected = sum(durations) + pause * (len(durations) - 1) + tail
    if abs(expected - total) > 0.1:
        log.warning("narration duration %.3fs differs from scene sum %.3fs", total, expected)
    used_voice = getattr(provider, "cfg", s.tts).voice
    vt = VoiceTiming(provider=provider.name, voice=used_voice if provider.name == "kokoro"
                     else provider.model_id(), model=provider.model_id(),
                     sample_rate=s.tts.sample_rate, duration=round(total, 4), scenes=timings,
                     loudness_lufs=loud.get("input_i"), demo=provider.demo)
    write_json(paths.timing, vt.model_dump())
    return vt, {"cache_hits": cache_hits, "scenes": len(scene_files),
                "loudness": loud, "duration_s": total, "speedup": round(speedup, 4)}


def import_user_narration(s: Settings, paths: JobPaths, script: Script, src: Path
                          ) -> tuple[VoiceTiming, dict]:
    """Use a user-recorded narration file. Scene boundaries are estimated by character
    share and flagged; captions are aligned to the real audio with Whisper later."""
    vd = paths.voice_dir
    vd.mkdir(parents=True, exist_ok=True)
    tmp = tmp_path_for(paths.narration)
    loud = media.normalize_audio(src, tmp, s.tts.narration_lufs, -1.5, s.tts.sample_rate,
                                 channels=1, log_file=paths.logs / "voice.log")
    tmp.replace(paths.narration)
    total = media.duration(paths.narration)
    lens = [max(1, len(sc.narration)) for sc in script.scenes]
    tot = sum(lens)
    t = 0.0
    timings = []
    for sc, n in zip(script.scenes, lens, strict=True):
        d = total * n / tot
        timings.append(SceneTiming(scene_id=sc.id, start=round(t, 4), speech_end=round(t + d, 4),
                                   end=round(t + d, 4), audio_file="voice/narration.wav",
                                   text=sc.narration))
        t += d
    timings[-1].end = round(total, 4)
    vt = VoiceTiming(provider="user", voice="user-recording", model=f"user:{src.name}",
                     sample_rate=s.tts.sample_rate, duration=round(total, 4), scenes=timings,
                     loudness_lufs=loud.get("input_i"))
    write_json(paths.timing, vt.model_dump())
    return vt, {"approximate_scene_boundaries": True, "loudness": loud, "duration_s": total}
