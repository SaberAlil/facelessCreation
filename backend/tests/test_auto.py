"""Automatic flow: calendar research → LLM draft, Italian captions, overlay on media."""

from __future__ import annotations

import json

import httpx
import pytest
from conftest import needs_ffmpeg

from faceless_studio import research as R
from faceless_studio.schemas import JobState, Source, Word
from faceless_studio.subtitles import sentences_from_words, words_for_translation
from faceless_studio.util import run


def test_parse_research_cell():
    cell = ("https://a.example/x | Shoppers walk past more products\n"
            "• https://b.example/y\n"
            "My angle: focus on store layout ; https://c.example/z (paper)")
    links, notes = R.parse_research_cell(cell)
    assert links == [("https://a.example/x", "Shoppers walk past more products"),
                     ("https://b.example/y", ""), ("https://c.example/z", "(paper)")]
    assert notes == ["My angle: focus on store layout"]


def test_relevant_excerpt_prefers_topic_paragraphs():
    paras = ["Cookie banner text about privacy settings and more filler words here.",
             "IKEA stores use a one-way showroom path that guides shoppers past every room.",
             "Unrelated history paragraph about the founder's childhood in a small village."]
    ex = R.relevant_excerpt(paras, "Why IKEA uses winding showroom paths", 120)
    assert ex.startswith("IKEA stores use")


def test_build_research_from_cell_and_wikipedia_fallback():
    def fake_fetch(url, sid, topic="", max_chars=1500):
        if "bad" in url:
            raise R.FacelessError("HTTP 404")
        return Source(id=sid, url=url, title="T", excerpt="ikea showroom path text")

    r, log = R.build_research("Why IKEA uses winding showroom paths",
                              "https://ok.example | One-way path\nhttps://bad.example",
                              fetch=fake_fetch, wiki=lambda *a, **k: pytest.fail("no wiki"))
    assert [s.id for s in r.sources] == ["S1", "S2"]
    assert r.claims[0].verified and r.claims[0].source_ids == ["S1"]
    assert "could not fetch" in log[1] and "Could not fetch" in r.sources[1].notes
    r2, log2 = R.build_research("Why IKEA uses winding showroom paths", "",
                                wiki=lambda t, max_chars: [Source(id="W1", title="IKEA")])
    assert [s.id for s in r2.sources] == ["W1"] and "Wikipedia" in log2[0]


def test_wiki_queries_go_from_specific_to_broad():
    q = R.wiki_queries("Why IKEA uses winding showroom paths")
    assert q[0] == "showroom winding paths ikea" and "IKEA" in q


def test_sentences_and_translated_timing():
    words = [Word(text=t, start=i * 0.5, end=i * 0.5 + 0.4) for i, t in
             enumerate(["Prices", "end", "in", "nine.", "They", "feel", "cheaper!"])]
    sents = sentences_from_words(words)
    assert [s[0] for s in sents] == ["Prices end in nine.", "They feel cheaper!"]
    it = words_for_translation(sents, ["I prezzi finiscono con nove.", "Sembrano più bassi!"])
    assert it[0].start == sents[0][1] and abs(it[4].end - sents[0][2]) < 0.01
    assert it[5].start >= sents[1][1]


# -------------------------------------------------------------- automatic draft

class FakeOllama:
    """Prose call, storyboard call, translation call."""

    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        p = req.url.path
        if p == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen3:4b"}]})
        if p in ("/api/ps", "/api/generate"):
            return httpx.Response(200, json={"models": []})
        body = json.loads(req.content)
        self.calls.append(body)
        fmt = body.get("format") or {}
        if "translations" in json.dumps(fmt):
            n = len(body["messages"][1]["content"].splitlines())
            content = json.dumps({"translations": [f"Frase italiana numero {i}." for i in
                                                   range(1, n + 1)]})
        elif fmt:
            content = json.dumps({
                "title": "Why IKEA Paths Wind", "hook": "Ever got lost in IKEA?",
                "narration": "x", "scenes": [{"narration": "x", "visual_concept":
                                              "showroom", "stock_search_terms": ["store"],
                                              "overlay": "The long way", "claim_ids": []}],
                "claims": [], "youtube_title": "t", "youtube_description": "d",
                "tiktok_caption": "t", "instagram_caption": "i", "hashtags": ["#ikea"]})
        else:
            content = ("IKEA guides you along one long path. You see every room.\n\n"
                       "That path is the point. More rooms mean more ideas to buy.\n\n"
                       "So next time, take the shortcut.")
        return httpx.Response(200, json={"message": {"content": content}})


@pytest.fixture
def fake_ollama(monkeypatch):
    from faceless_studio.providers import llm
    fake = FakeOllama()
    real = llm.OllamaScriptProvider.__init__

    def init(self, cfg, res, lock_dir, transport=None):
        real(self, cfg, res, lock_dir, transport=httpx.MockTransport(fake))
    monkeypatch.setattr(llm.OllamaScriptProvider, "__init__", init)
    monkeypatch.setattr(llm, "query_gpu", lambda: None)
    return fake


def test_resume_builds_research_and_drafts(pipeline, small_limits, fake_ollama, monkeypatch):
    pl = small_limits(pipeline)
    pl.s.script.target_words_min, pl.s.script.target_words_max = 5, 60
    monkeypatch.setattr(R, "fetch_source", lambda url, sid, **k: Source(
        id=sid, url=url, title="Store layout article", excerpt="ikea showroom path"))
    from faceless_studio.calendar import parse_calendar, store_calendar
    csv = pl.s.data_dir / "cal.csv"
    csv.write_text("Day,Video topic / working hook,Research / sources\n"
                   "1,Why IKEA uses winding showroom paths,"
                   "https://example.org/ikea | The showroom is a one-way path\n")
    store_calendar(pl.studio.db, parse_calendar(csv))
    from faceless_studio.calendar import calendar_entry
    j = pl.studio.create_from_calendar(calendar_entry(pl.studio.db, day=1)).id
    gate = pl.advance(j)
    assert gate.state == JobState.RESEARCH_REVIEW
    r = pl.research(j)
    assert r.sources[0].url and str(r.sources[0].url).startswith("https://example.org/ikea")
    assert r.claims[0].text == "The showroom is a one-way path"
    sc = pl.script(j)
    assert sc.provider == "ollama:qwen3:4b" and len(sc.scenes) == 3
    assert [x["stage"] for x in pl.studio.stage_runs(j)] == ["research", "draft"]


def test_resume_without_any_research_stops_with_hint(pipeline):
    j = pipeline.studio.create_job("A topic with no research").id
    gate = pipeline.advance(j)
    assert gate.state == JobState.DRAFT and "research" in gate.reason


@needs_ffmpeg
def test_italian_captions(pipeline, short_script, research_file, tone_tts, small_limits,
                          fake_ollama):
    pl = small_limits(pipeline)
    pl.s.subtitles.burn_language = "it"
    j = pl.studio.create_job("Italian captions test").id
    pl.draft(j, script_file=short_script, research_file=research_file)
    pl.approve_script(j, "t")
    pl.voice(j, tts=tone_tts)
    o = pl.subtitles(j, approximate=True)
    assert o.details["language"] == "it" and not o.details["translation_cached"]
    tr = pl.captions(j)
    assert tr.language == "it" and tr.translated_by == "qwen3:4b"
    burned = pl.paths(j).srt.read_text()
    assert "Frase italiana" in burned and "First scene" not in burned
    english = (pl.paths(j).captions_dir / "captions.en.srt").read_text()
    assert "First scene" in english
    n_calls = len(fake_ollama.calls)
    o2 = pl.subtitles(j, approximate=True)  # same sentences → cache, no LLM call
    assert o2.details["translation_cached"] and len(fake_ollama.calls) == n_calls
    items = {i["check"]: i for i in pl.final_checklist(j)}
    assert any("machine-translated" in k for k in items)


@needs_ffmpeg
def test_overlay_drawn_on_media_not_on_graphics(pipeline, short_script, research_file,
                                                tone_tts, small_limits, tmp_path):
    from faceless_studio import library
    pl = small_limits(pipeline)
    img = tmp_path / "photo.png"
    run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=720x1280",
         "-frames:v", "1", str(img)], timeout=30)
    library.add_asset(pl.studio.db, pl.s.library_dir, img, license="own work",
                      commercial_use=True, tags=["calculator"])
    j = pl.studio.create_job("Overlay test").id
    pl.draft(j, script_file=short_script, research_file=research_file)
    pl.approve_script(j, "t")
    pl.voice(j, tts=tone_tts)
    pl.assets(j)
    out = pl.render(j, preview=True, dry_run=True)
    clip_cmds = [" ".join(c) for c in out["commands"] if "zoompan" in " ".join(c)]
    assert "overlay: Round up" in clip_cmds[1]          # library photo (scene 2)
    assert "overlay:" not in clip_cmds[0]                # original graphic (scene 1)
    o = pl.render(j, preview=True)
    assert o.details["validation_ok"]
