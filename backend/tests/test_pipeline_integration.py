"""End-to-end runs with real FFmpeg on synthetic inputs.

The narration here is a TEST FIXTURE tone (ToneTTS), not speech; captions use the
approximate fallback so no model download is needed. ``test_real_models`` exercises Kokoro
and faster-whisper when they are installed (pytest -m models).
"""

from __future__ import annotations

import pytest
from conftest import needs_ffmpeg

from faceless_studio import media, render
from faceless_studio.quality import validate_video
from faceless_studio.schemas import JobState
from faceless_studio.util import GateError, ToolFailed, read_json

pytestmark = [pytest.mark.integration, needs_ffmpeg]


def _ready_job(pl, short_script, research_file, tone_tts):
    j = pl.studio.create_job("Integration test").id
    pl.draft(j, script_file=short_script, research_file=research_file)
    pl.approve_script(j, "tester")
    pl.voice(j, tts=tone_tts)
    pl.assets(j)
    return j


def test_end_to_end_preview_final_and_gates(pipeline, short_script, research_file, tone_tts,
                                            small_limits):
    pl = small_limits(pipeline)
    j = _ready_job(pl, short_script, research_file, tone_tts)
    vt = pl.timing(j)
    assert vt.demo and len(vt.scenes) == 3
    assert vt.scenes[1].start == pytest.approx(vt.scenes[0].speech_end + 0.35, abs=0.02)
    assert abs(media.duration(pl.paths(j).narration) - vt.duration) < 0.01

    # Preview is allowed before rights review; final render is not.
    pl.render(j, preview=True)
    val = read_json(pl.paths(j).render_dir / "validation.preview.json")
    assert val["ok"], val
    with pytest.raises(GateError, match="not approved"):
        pl.render(j)

    pl.set_rights(j, None, __import__("faceless_studio.schemas").schemas.RightsStatus.CONFIRMED)
    pl.approve_assets(j, "tester")
    assert pl.refresh(j) == JobState.ASSETS_READY
    pl.subtitles(j, approximate=True)
    o = pl.render(j)
    assert o.details["encoder"] == "libx264"
    p = pl.paths(j)
    v = validate_video(p.final, vt.duration, pl.s.render)
    assert v["ok"], [c for c in v["checks"] if not c["ok"]]
    assert (p.render_dir / "cover.png").is_file()
    assert pl.refresh(j) == JobState.RENDERED

    # Final approval refuses demo audio, unreviewed approximate captions and short length
    items = {i["check"]: i for i in pl.final_checklist(j)}
    assert not items["narration is production voice (not demo)"]["ok"]
    assert not items["captions reviewed where required"]["ok"]
    with pytest.raises(GateError, match="final approval blocked"):
        pl.approve_final(j, "tester")
    with pytest.raises(GateError):
        pl.export(j)

    # Re-render reuses cached scene clips
    o2 = pl.render(j)
    assert o2.details["clips_reused"] == 3

    # Caption review is recorded; changing captions makes the render stale
    pl.mark_captions_reviewed(j)
    assert not pl.stage_fresh(j, "render")
    assert pl.refresh(j) == JobState.ASSETS_READY


def test_dry_run_prints_commands_without_files(pipeline, short_script, research_file,
                                               tone_tts, small_limits):
    pl = small_limits(pipeline)
    j = _ready_job(pl, short_script, research_file, tone_tts)
    out = pl.render(j, preview=True, dry_run=True)
    assert out["dry_run"] and any("zoompan" in " ".join(c) for c in out["commands"])
    assert not pl.paths(j).preview.exists()


def test_nvenc_runtime_failure_falls_back_to_libx264(pipeline, short_script, research_file,
                                                     tone_tts, small_limits, monkeypatch):
    pl = small_limits(pipeline)
    j = _ready_job(pl, short_script, research_file, tone_tts)
    monkeypatch.setattr(render, "check_vram", lambda *a, **k: None)
    real = media.video_codec_args

    def broken_nvenc(encoder, cfg, preview=False, intermediate=False):
        if encoder == "h264_nvenc":  # simulate a driver/runtime failure
            return ["-c:v", "h264_nvenc", "-this_option_does_not_exist", "1"]
        return real(encoder, cfg, preview, intermediate)
    monkeypatch.setattr(media, "video_codec_args", broken_nvenc)
    o = pl.render(j, preview=True, encoder="h264_nvenc")
    assert o.details["encoder"] == "libx264"
    assert any("NVENC failure" in f for f in o.fallbacks)
    run = pl.studio.last_run(j, "preview")
    assert run["status"] == "ok" and run["fallbacks"]


def test_failed_stage_is_recorded_and_resumable(pipeline, short_script, research_file,
                                                tone_tts, small_limits, monkeypatch):
    pl = small_limits(pipeline)
    j = _ready_job(pl, short_script, research_file, tone_tts)
    from faceless_studio.schemas import RightsStatus
    pl.set_rights(j, None, RightsStatus.CONFIRMED)
    pl.approve_assets(j, "tester")
    pl.subtitles(j, approximate=True)
    real = render.render_job

    def boom(*a, **k):
        raise ToolFailed("simulated crash during render")
    monkeypatch.setattr(render, "render_job", boom)
    with pytest.raises(ToolFailed):
        pl.render(j)
    job = pl.studio.get_job(j)
    assert job["state"] == "FAILED" and job["failed_stage"] == "render"
    assert pl.effective_state(j) == JobState.ASSETS_READY  # earlier work preserved
    monkeypatch.setattr(render, "render_job", real)
    gate = pl.advance(j)
    assert gate.state == JobState.RENDERED
    assert pl.studio.get_job(j)["state"] == "RENDERED"


def test_simulated_whisper_oom_uses_fallback_model(pipeline, short_script, research_file,
                                                   tone_tts, small_limits, monkeypatch):
    pl = small_limits(pipeline)
    j = _ready_job(pl, short_script, research_file, tone_tts)
    from faceless_studio import subtitles as subs
    from faceless_studio.schemas import Word
    calls = []

    def fake_transcribe(audio, cfg, model_name=None, prompt=None):
        calls.append(model_name)
        if model_name == "small.en":
            raise RuntimeError("CUDA error: out of memory")
        words = pl.script(j).narration.split()
        return [Word(text=w, start=i * 0.3, end=i * 0.3 + 0.25)
                for i, w in enumerate(words)], model_name
    monkeypatch.setattr(subs, "transcribe", fake_transcribe)
    o = pl.subtitles(j)
    assert calls == ["small.en", "base.en"]
    assert o.details["model"] == "base.en" and o.fallbacks


@pytest.mark.models
def test_real_models(pipeline, tmp_path, small_limits):
    """Real Kokoro narration + faster-whisper alignment (needs installed models)."""
    pytest.importorskip("kokoro")
    pytest.importorskip("faster_whisper")
    pl = small_limits(pipeline)
    s = tmp_path / "s.md"
    s.write_text("# Real Model Test\n\nPrices that end in ninety-nine feel cheaper.\n\n"
                 "That is the left digit effect at work.\n")
    j = pl.studio.create_job("real models").id
    pl.draft(j, script_file=s)
    pl.approve_script(j, "tester")
    pl.voice(j)
    o = pl.subtitles(j)
    assert o.details["source"] == "whisper"
    assert o.details["divergence_ratio"] > 0.7


def test_export_package_contents(pipeline, short_script, research_file, tone_tts,
                                 small_limits, monkeypatch):
    import json

    from faceless_studio.schemas import RightsStatus
    pl = small_limits(pipeline)
    j = _ready_job(pl, short_script, research_file, tone_tts)
    pl.set_rights(j, None, RightsStatus.CONFIRMED)
    pl.approve_assets(j, "tester")
    pl.subtitles(j, approximate=True)
    pl.render(j)
    # The tone fixture is (correctly) refused as demo audio; bypass only the checklist.
    monkeypatch.setattr(pl, "final_checklist", lambda job_id: [])
    pl.approve_final(j, "tester")
    pl.export(j)
    assert pl.refresh(j) == JobState.EXPORTED
    out = pl.paths(j).export_dir
    names = sorted(f.name for f in out.iterdir())
    assert names == sorted([f"{j}.mp4", f"{j}-cover.png", f"{j}.srt", "youtube.txt",
                            "tiktok.txt", "instagram.txt", "sources.json", "metadata.json",
                            "review.json"])
    yt = (out / "youtube.txt").read_text()
    assert "TITLE: Test Video Title" in yt and "synthetic" in yt
    meta = json.loads((out / "metadata.json").read_text())
    assert meta["validation"]["ok"] and meta["render"]["encoder"] == "libx264"
    assert {s["stage"] for s in meta["stages"]} >= {"voice", "assets", "subtitles", "render"}
    review = json.loads((out / "review.json").read_text())
    assert [a["kind"] for a in review["approvals"]] == ["script", "assets", "final"]
    sources = json.loads((out / "sources.json").read_text())
    assert sources["research_sources"][0]["id"] == "S1"
    # Changing the approved video afterwards invalidates approval and export
    pl.paths(j).final.write_bytes(b"tampered")
    assert pl.refresh(j) != JobState.EXPORTED
    assert not pl.approval_valid(j, "final")
