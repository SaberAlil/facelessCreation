from __future__ import annotations

import csv
import os

import httpx
import pytest
from conftest import needs_ffmpeg

from faceless_studio import library
from faceless_studio.cachectl import clean
from faceless_studio.config import StockConfig
from faceless_studio.export import write_tracking_csv
from faceless_studio.providers.stock import PexelsProvider
from faceless_studio.util import FacelessError, ProviderUnavailable, ValidationFailed, run


@pytest.fixture
def test_image(tmp_path):
    """TEST FIXTURE: an FFmpeg test pattern, not stock footage."""
    p = tmp_path / "pattern.png"
    run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=1280x720",
         "-frames:v", "1", str(p)], timeout=30)
    return p


@needs_ffmpeg
def test_library_requires_license_and_records_provenance(studio, test_image):
    with pytest.raises(ValidationFailed, match="license"):
        library.add_asset(studio.db, studio.settings.library_dir, test_image, license=" ",
                          commercial_use=True)
    a = library.add_asset(studio.db, studio.settings.library_dir, test_image,
                          license="own work", commercial_use=True, creator="me",
                          tags=["price", "tag"], source_url="local")
    assert (a.width, a.height) == (1280, 720)
    assert (studio.settings.library_dir / a.path).is_file()
    again = library.add_asset(studio.db, studio.settings.library_dir, test_image,
                              license="own work", commercial_use=True)
    assert again.id == a.id  # deduplicated by content hash


@needs_ffmpeg
def test_library_rejects_unsupported(studio, tmp_path):
    p = tmp_path / "x.exe"
    p.write_bytes(b"MZ")
    with pytest.raises(FacelessError, match="unsupported"):
        library.add_asset(studio.db, studio.settings.library_dir, p, license="x",
                          commercial_use=True)


@needs_ffmpeg
def test_asset_selection_prefers_matching_library_then_graphics(
        pipeline, short_script, research_file, small_limits, test_image):
    pl = small_limits(pipeline)
    a = library.add_asset(pl.studio.db, pl.s.library_dir, test_image, license="own work",
                          commercial_use=True, tags=["calculator", "budget"])
    j = pl.studio.create_job("test topic t").id
    pl.draft(j, script_file=short_script, research_file=research_file)
    pl.approve_script(j, "t")
    o = pl.assets(j)
    m = pl.manifest(j)
    assert o.details["by_source"] == {"local": 1, "procedural": 2}
    assert m.scenes[1].library_id == a.id
    assert m.scenes[0].provider == "procedural" and m.scenes[0].commercial_use
    assert len({x.sha256 for x in m.scenes}) == 3  # no repeated background
    rv = pl.asset_review(j)
    assert any("rights unreviewed" in p for p in rv["problems"])
    from faceless_studio.util import GateError
    with pytest.raises(GateError):
        pl.approve_assets(j, "t")
    from faceless_studio.schemas import RightsStatus
    pl.set_rights(j, ["s02"], RightsStatus.CONFIRMED)
    pl.approve_assets(j, "t")
    assert pl.approval_valid(j, "assets")
    # manual replacement invalidates the approval
    pl.replace_asset(j, "s02", procedural=True, overlay="Replaced")
    assert not pl.approval_valid(j, "assets")


def _pexels_transport(link_host="videos.pexels.com"):
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/videos/search":
            assert req.headers["Authorization"] == "k"
            assert req.url.params["orientation"] == "portrait"
            return httpx.Response(200, json={"videos": [{
                "id": 42, "url": "https://www.pexels.com/video/42/", "duration": 8,
                "user": {"name": "Ana", "url": "https://www.pexels.com/@ana"},
                "video_files": [
                    {"file_type": "video/mp4", "width": 1920, "height": 1080,
                     "link": f"https://{link_host}/a.mp4"},
                    {"file_type": "video/mp4", "width": 1080, "height": 1920,
                     "link": f"https://{link_host}/b.mp4"}]}]})
        if req.url.path == "/b.mp4":
            return httpx.Response(200, content=b"x" * 2048)
        return httpx.Response(429 if req.url.path == "/limited" else 404)
    return httpx.MockTransport(handler)


def test_pexels_search_prefers_portrait_and_bounds_download(tmp_path):
    p = PexelsProvider(StockConfig(max_download_mb=1), api_key="k",
                       transport=_pexels_transport())
    c = p.search("price tag")[0]
    assert (c["width"], c["height"]) == (1080, 1920) and c["creator"] == "Ana"
    out = p.download(c, tmp_path / "v.mp4")
    assert out.stat().st_size == 2048


def test_pexels_refuses_foreign_download_host(tmp_path):
    p = PexelsProvider(StockConfig(), api_key="k",
                       transport=_pexels_transport("evil.example.com"))
    c = p.search("x")[0]
    with pytest.raises(FacelessError, match="unexpected host"):
        p.download(c, tmp_path / "v.mp4")


def test_pexels_needs_key(monkeypatch):
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    with pytest.raises(ProviderUnavailable):
        PexelsProvider(StockConfig())


def test_tracking_csv_keeps_manual_columns(tmp_path):
    out = tmp_path / "t.csv"
    write_tracking_csv(out, [{"job_id": "a", "topic": "A", "state": "EXPORTED"}])
    with open(out, newline="") as f:
        rows = list(csv.DictReader(f))
    rows[0]["views_7d"] = "1234"
    rows[0]["youtube_url"] = "https://youtu.be/x"
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    write_tracking_csv(out, [{"job_id": "a", "topic": "A", "state": "EXPORTED"},
                             {"job_id": "b", "topic": "B", "state": "DRAFT"}])
    with open(out, newline="") as f:
        rows = {r["job_id"]: r for r in csv.DictReader(f)}
    assert rows["a"]["views_7d"] == "1234" and rows["a"]["youtube_url"]
    assert rows["b"]["views_7d"] == ""


def test_cache_clean_lru(settings):
    d = settings.cache_dir / "tts"
    d.mkdir(parents=True)
    for i in range(5):
        f = d / f"{i}.wav"
        f.write_bytes(b"x" * 1000)
        os.utime(f, (1000 + i, 1000 + i))
    removed, freed = clean(settings, max_gb=2500 / 1e9)
    assert removed == 3 and freed == 3000
    assert sorted(f.name for f in d.iterdir()) == ["3.wav", "4.wav"]
