"""Dashboard API: security guards, job flow and a full run through the worker."""

from __future__ import annotations

import pytest
from conftest import SAMPLES, SHORT_SCRIPT, needs_ffmpeg
from fastapi.testclient import TestClient

from faceless_studio.api import create_app

H = {"X-Faceless": "1"}


@pytest.fixture
def client(settings):
    settings.script.hard_words_min = 5
    settings.script.min_estimated_s = 1
    settings.render.min_duration_s = 1
    app = create_app(settings)
    with TestClient(app) as c:
        c.app_ = app
        yield c


def test_guards(client):
    assert client.get("/api/health").json()["ok"]
    assert client.post("/api/jobs", json={"topic": "A topic here"}).status_code == 403
    r = client.get("/api/health", headers={"Host": "evil.example.com"})
    assert r.status_code == 403
    pre = client.options("/api/jobs", headers={
        "Origin": "https://evil.example.com", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "x-faceless"})
    assert "access-control-allow-origin" not in pre.headers
    ok = client.options("/api/jobs", headers={
        "Origin": "http://localhost:4200", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "x-faceless"})
    assert ok.headers["access-control-allow-origin"] == "http://localhost:4200"


def test_calendar_upload_and_job(client):
    with open(SAMPLES / "calendar.csv", "rb") as f:
        r = client.post("/api/calendar/import", files={"file": ("cal.csv", f, "text/csv")},
                        headers=H)
    assert r.json()["rows"] == 7
    r = client.post("/api/jobs", json={"day": 2}, headers=H)
    assert r.status_code == 201
    jid = r.json()["id"]
    assert client.post("/api/jobs", json={"day": 2}, headers=H).status_code == 422
    cal = client.get("/api/calendar?to_day=3").json()
    assert cal[1]["job"]["id"] == jid
    d = client.get(f"/api/jobs/{jid}").json()
    assert d["gate"]["state"] == "DRAFT"
    assert client.get("/api/jobs/nope-job-id").status_code == 404
    assert client.get(f"/api/jobs/{jid}/files/..%2F..%2Fetc%2Fpasswd").status_code == 404


def _wait(client, task):
    client.app_.state.worker.wait_idle()
    return client.get(f"/api/tasks/{task['id']}").json()


@needs_ffmpeg
def test_full_flow_via_api(client, tone_tts, monkeypatch, research_file):
    """One-click approvals; each approval continues automatically to the next one."""
    pl = client.app_.state.pipeline
    from faceless_studio.providers import tts as tts_mod
    monkeypatch.setattr(tts_mod, "get_tts", lambda cfg, name=None: tone_tts)
    r = client.put("/api/settings", json={"reviewer": "Sam", "default_subtitles": "en"},
                   headers=H)
    assert r.json()["reviewer"] == "Sam"
    jid = client.post("/api/jobs", json={"topic": "API flow test"}, headers=H).json()["id"]
    pl.set_research(jid, research_file)
    t = client.post(f"/api/jobs/{jid}/draft", json={"text": SHORT_SCRIPT, "llm": False},
                    headers=H).json()
    assert _wait(client, t)["status"] == "done"
    d = client.get(f"/api/jobs/{jid}").json()
    assert [x["status"] for x in d["steps"]] == ["review", "todo", "todo", "todo"]
    sc = client.get(f"/api/jobs/{jid}/script").json()
    assert sc["report"]["words"] > 10 and not sc["approved"]
    # one click, no typing (acknowledge_flags is the "I checked the facts" button)
    r = client.post(f"/api/jobs/{jid}/approve-script", json={"acknowledge_flags": True},
                    headers=H).json()
    assert r["approved"] and r["task"]["action"] == "continue"
    client.app_.state.worker.wait_idle()
    assert pl.studio.latest_approval(jid, "script")["reviewer"] == "Sam"
    d = client.get(f"/api/jobs/{jid}").json()
    assert d["steps"][1]["status"] == "review"  # narration + pictures done automatically
    a = client.get(f"/api/jobs/{jid}/assets").json()
    assert len(a["manifest"]["scenes"]) == 3
    assert a["problems"] == []  # unreviewed licences don't block: approving confirms them
    assert client.get(f"/api/jobs/{jid}/files/scene-s01").status_code == 200
    # subtitle language per video
    o = client.put(f"/api/jobs/{jid}/options", json={"subtitles": "none"}, headers=H).json()
    assert o["options"]["subtitles"] == "none" and "it" in o["languages"]
    assert client.put(f"/api/jobs/{jid}/options", json={"subtitles": "xx"},
                      headers=H).status_code == 422
    # approving pictures confirms their licences and continues to the rendered video
    monkeypatch.setattr(pl, "subtitles", lambda j, approximate=False, model=None:
                        type(pl).subtitles(pl, j, approximate=True))
    r = client.post(f"/api/jobs/{jid}/approve-assets", json={}, headers=H).json()
    assert r["approved"] and r["task"]
    client.app_.state.worker.wait_idle()
    d = client.get(f"/api/jobs/{jid}").json()
    assert d["gate"]["state"] == "RENDERED", d["gate"]
    assert d["steps"][2]["status"] == "review"
    assert pl.captions(jid).burn is False  # "none" chosen: nothing burned in
    r = client.get(f"/api/jobs/{jid}/files/final.mp4")
    assert r.status_code == 200 and r.headers["content-type"] == "video/mp4"
    r = client.post(f"/api/jobs/{jid}/approve-final", json={}, headers=H)
    assert r.status_code == 409 and "demo" in r.json()["detail"]  # tone fixture refused
    ov = client.get("/api/overview").json()
    assert any(x["id"] == jid for x in ov["review"]) and ov["reviewer_set"]


def test_one_task_per_job(client):
    import threading
    pl = client.app_.state.pipeline
    jid = client.post("/api/jobs", json={"topic": "Busy job test"}, headers=H).json()["id"]
    gate = threading.Event()
    w = client.app_.state.worker
    w.submit(jid, "slow", lambda: gate.wait(5))
    r = client.post(f"/api/jobs/{jid}/run/voice", headers=H)
    assert r.status_code == 409
    gate.set()
    w.wait_idle()
    assert pl.studio.get_job(jid)


def test_make_endpoint_creates_and_runs(client):
    with open(SAMPLES / "calendar.csv", "rb") as f:
        client.post("/api/calendar/import", files={"file": ("cal.csv", f, "text/csv")},
                    headers=H)
    t = client.post("/api/make", json={"day": 3}, headers=H).json()
    done = _wait(client, t)
    assert done["status"] == "done", done
    d = client.get(f"/api/jobs/{t['job_id']}").json()
    # no research in the sample calendar and Wikipedia disabled in tests: stops with a hint
    assert d["gate"]["state"] == "DRAFT" and "research" in d["gate"]["reason"]
    again = client.post("/api/make", json={"day": 3}, headers=H).json()
    assert again["job_id"] == t["job_id"]  # never a second job for the same day


@needs_ffmpeg
def test_fit_stage_rerecords_and_continues(client, tone_tts, monkeypatch, research_file):
    pl = client.app_.state.pipeline
    from faceless_studio.providers import tts as tts_mod
    monkeypatch.setattr(tts_mod, "get_tts", lambda cfg, name=None: tone_tts)
    jid = client.post("/api/jobs", json={"topic": "Fit stage test"}, headers=H).json()["id"]
    pl.set_research(jid, research_file)
    pl.draft(jid, pasted_text=SHORT_SCRIPT)
    pl.approve_script(jid, "t")
    pl.voice(jid)
    before = pl.timing(jid).duration
    pl.s.render.max_duration_s = before + 0.3  # target is limit - 0.5 s: slightly too long
    t = client.post(f"/api/jobs/{jid}/run/fit", headers=H).json()
    done = _wait(client, t)
    assert done["status"] == "done", (done["error"], done["hint"])
    assert pl.timing(jid).duration < before
    assert pl.studio.last_run(jid, "voice", "ok")["details"]["speedup"] > 1.0
