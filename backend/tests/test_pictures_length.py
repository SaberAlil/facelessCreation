"""Real photos (Wikimedia Commons, mocked), uploads, missing pictures, 45-90 s limits."""

from __future__ import annotations

import pytest
from conftest import needs_ffmpeg

from faceless_studio import assets as assets_mod
from faceless_studio.config import ScriptConfig
from faceless_studio.providers import commons
from faceless_studio.research import check_script
from faceless_studio.schemas import Research, Scene, Script
from faceless_studio.util import GateError, ToolFailed, run


@pytest.fixture
def photo_file(tmp_path):
    """TEST FIXTURE: an FFmpeg test pattern standing in for a downloaded photo."""
    def make(name: str) -> bytes:
        p = tmp_path / f"{name}.jpg"
        run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
             f"testsrc2=size=1200x1600:duration=1,drawtext=text={name}:fontsize=80",
             "-frames:v", "1", str(p)], timeout=30)
        return p.read_bytes()
    return make


@pytest.fixture
def fake_commons(monkeypatch, photo_file):
    calls: list[str] = []

    def fake_search(query, allowed, min_width=900, limit=12, exclude=None):
        calls.append(query)
        out = []
        for i in range(3):
            title = f"File:{query} {i}.jpg"
            if exclude and title in exclude:
                continue
            out.append(commons.CommonsPhoto(
                title=title, page_url=f"https://commons.wikimedia.org/wiki/{i}",
                image_url=f"https://upload.wikimedia.org/x/{i}.jpg", width=1200, height=1600,
                license="CC BY 4.0", license_url="https://creativecommons.org/licenses/by/4.0",
                artist="Jane Photographer", attribution_required=True,
                description=f"{query} photo"))
        return out

    def fake_download(photo, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(photo_file(photo.title.split(":")[1].split(".")[0].replace(" ", "")))
        return dest

    monkeypatch.setattr(commons, "search", fake_search)
    monkeypatch.setattr(commons, "download", fake_download)
    return calls


def test_license_filter():
    allowed = ScriptConfig  # noqa: F841 (just to show we use config defaults below)
    from faceless_studio.config import VisualsConfig
    al = VisualsConfig().commons_licenses
    ok = ["CC BY 4.0", "CC BY 2.0", "Public domain", "CC0", "PD-US", "CC BY 3.0 de"]
    bad = ["CC BY-SA 4.0", "CC BY-NC 2.0", "CC BY-ND 4.0", "GFDL", "CC BY-SA 3.0 de"]
    assert all(commons.license_ok(x, al) for x in ok)
    assert not any(commons.license_ok(x, al) for x in bad)


def test_commons_queries_name_the_subject():
    sc = Scene(id="s1", narration="x", search_terms=["store interior", "furniture showroom"])
    qs = assets_mod.commons_queries(sc, "Why IKEA uses winding showroom paths")
    assert qs[0] == "IKEA store interior" and "furniture showroom" in qs


@needs_ffmpeg
def test_commons_photos_with_credits_and_another_photo(pipeline, short_script, research_file,
                                                        small_limits, fake_commons):
    pl = small_limits(pipeline)
    pl.s.visuals.order = ["library", "commons"]
    j = pl.studio.create_job("Why IKEA uses winding showroom paths").id
    pl.draft(j, script_file=short_script, research_file=research_file)
    pl.approve_script(j, "t")
    o = pl.assets(j)
    assert o.details["by_source"] == {"commons": 3} and o.details["missing"] == 0
    m = pl.manifest(j)
    first = m.scenes[0]
    assert first.provider == "commons" and first.attribution_required
    assert "Jane Photographer" in first.attribution and first.license == "CC BY 4.0"
    assert fake_commons[0].startswith("IKEA ")  # subject named in the first search
    titles = {a.candidates[0] for a in m.scenes}
    assert len(titles) == 3  # never the same photo twice in one video
    # "Try another photo" gives a different one and withdraws the approval
    pl.set_rights(j, None, __import__("faceless_studio.schemas").schemas.RightsStatus.CONFIRMED)
    pl.approve_assets(j, "t")
    pl.scene_picture(j, "s01", "photo")
    m2 = pl.manifest(j)
    assert m2.scenes[0].sha256 != first.sha256 and len(m2.scenes[0].candidates) == 2
    assert not pl.approval_valid(j, "assets")


@needs_ffmpeg
def test_missing_picture_blocks_approval_and_render(pipeline, short_script, research_file,
                                                    small_limits, tone_tts, monkeypatch,
                                                    tmp_path, photo_file):
    pl = small_limits(pipeline)
    pl.s.visuals.order = ["commons"]  # no graphics fallback

    def boom(*a, **k):
        raise commons.FacelessError("Wikimedia Commons request failed: offline")
    monkeypatch.setattr(commons, "search", boom)
    j = pl.studio.create_job("No pictures test").id
    pl.draft(j, script_file=short_script, research_file=research_file)
    pl.approve_script(j, "t")
    pl.voice(j, tts=tone_tts)
    o = pl.assets(j)
    assert o.details["missing"] == 3 and o.details["errors"]
    rv = pl.asset_review(j)
    assert any("needs a picture" in p for p in rv["problems"])
    with pytest.raises(GateError):
        pl.approve_assets(j, "t")
    with pytest.raises(ToolFailed, match="no picture"):
        pl.render(j, preview=True)
    # uploading your own photo fixes one scene
    up = tmp_path / "mine.jpg"
    up.write_bytes(photo_file("mine"))
    pl.scene_picture(j, "s02", "upload", upload=up)
    m = pl.manifest(j)
    assert m.scenes[1].provider == "upload" and m.scenes[1].rights.value == "confirmed"
    assert m.scenes[0].kind == "missing"


def _script(words_per_scene: int, scenes: int) -> Script:
    return Script(title="Length test", scenes=[
        Scene(id=f"s{i}", narration=" ".join(["word"] * words_per_scene) + ".")
        for i in range(scenes)])


def test_script_length_limits():
    cfg = ScriptConfig()
    long_report = check_script(_script(40, 6), Research(), cfg)  # 240 words
    assert any("too long" in e and "Remove about" in e for e in long_report.errors)
    short_report = check_script(_script(10, 4), Research(), cfg)
    assert any("too short" in e for e in short_report.errors)
    ok = check_script(_script(25, 6), Research(), cfg)  # 150 words ≈ 71 s
    assert not ok.errors and 60 < ok.est_seconds < 80


@needs_ffmpeg
def test_narration_sped_up_to_fit_or_refused(pipeline, short_script, research_file,
                                             small_limits, tone_tts):
    pl = small_limits(pipeline)
    j = pl.studio.create_job("Speed fit test").id
    pl.draft(j, script_file=short_script, research_file=research_file)
    pl.approve_script(j, "t")
    # tone fixture: ~10.7 s total; pretend the limit is 11 s (target 10.5) → small speed-up
    pl.s.tts.pause_between_scenes_ms = 350  # the timings above assume this pause
    pl.s.render.max_duration_s = 11.0
    o = pl.voice(j, tts=tone_tts)
    assert 1.0 < o.details["speedup"] <= 1.08 and o.fallbacks
    assert pl.timing(j).duration <= 10.6
    pl.s.render.max_duration_s = 6.0  # far too long to fix by speeding up
    pl.s.tts.speed = 0.99  # new input → re-synthesis
    with pytest.raises(GateError, match="too long"):
        pl.voice(j, tts=tone_tts)
