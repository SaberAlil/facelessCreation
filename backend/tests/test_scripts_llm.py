from __future__ import annotations

import json

import httpx
import pytest
from conftest import SAMPLES

from faceless_studio import providers
from faceless_studio.config import LLMConfig, ResourceConfig, ScriptConfig
from faceless_studio.providers.llm import (
    ManualScriptProvider,
    OllamaScriptProvider,
    parse_text_script,
)
from faceless_studio.research import check_script, load_research
from faceless_studio.schemas import Research
from faceless_studio.util import GateError, ProviderUnavailable, ValidationFailed


def test_sample_script_parses():
    sc = ManualScriptProvider().load(SAMPLES / "day-001-script.md")
    assert sc.title == "Why Prices End in 99"
    assert len(sc.scenes) == 7
    assert [c.id for c in sc.claims] == ["C1", "C2", "C3", "C4"]
    assert sc.scenes[1].claim_ids == ["C1"]
    assert sc.platform.hashtags[0] == "#pricing"
    r = load_research(SAMPLES / "day-001-research.json")
    rep = check_script(sc, r, ScriptConfig())
    assert rep.ok and 150 <= rep.words <= 185
    # sample research is deliberately unverified: every claim must be flagged
    assert len(rep.flagged_claims) == 4


def test_unsupported_claims_flagged():
    sc = parse_text_script("# Test title\nClaim C1: no source here\nClaim C2: bad source [S9]\n\n"
                           "Scene one words. \n> claims: C1, C2\n")
    rep = check_script(sc, Research(), ScriptConfig(hard_words_min=1))
    reasons = " ".join(rep.flagged_claims)
    assert "no source" in reasons and "unknown sources" in reasons
    assert all(c.needs_review for c in sc.claims)


def test_llm_claims_stay_flagged_even_with_valid_sources():
    from faceless_studio.schemas import Scene, Script, ScriptClaim
    sc = Script(title="Title", scenes=[Scene(id="s1", narration="Words here.")],
                claims=[ScriptClaim(id="C1", text="Invented detail", source_ids=["S1"],
                                    needs_review=True)])
    r = Research.model_validate({"sources": [{"id": "S1", "title": "A"}]})
    rep = check_script(sc, r, ScriptConfig(hard_words_min=1))
    assert rep.flagged_claims and "human review" in rep.flagged_claims[0]


def test_unknown_claim_reference_is_error():
    sc = parse_text_script("# Test title\n\nWords here.\n> claims: C7\n")
    rep = check_script(sc, Research(), ScriptConfig(hard_words_min=1))
    assert any("unknown claim C7" in e for e in rep.errors)


def test_script_requires_title():
    with pytest.raises(ValidationFailed, match="title"):
        parse_text_script("Just narration.")
    with pytest.raises(ValidationFailed, match="invalid script"):
        parse_text_script("# T\n\nToo short a title.")


def test_approval_blocks_flagged_claims(pipeline, small_limits, tmp_path):
    pl = small_limits(pipeline)
    j = pl.studio.create_job("test topic x").id
    f = tmp_path / "s.md"
    f.write_text("# Title here\nClaim C1: unsupported [S1]\n\nSome words in a scene.\n"
                 "> claims: C1\n")
    pl.draft(j, script_file=f)
    with pytest.raises(GateError, match="need review"):
        pl.approve_script(j, "t")
    pl.approve_script(j, "t", acknowledge_flags=True)
    appr = pl.studio.latest_approval(j, "script")
    assert appr["checklist"]["flagged_claims_acknowledged"]


def test_llm_requires_research(pipeline):
    j = pipeline.studio.create_job("test topic x").id
    with pytest.raises(GateError, match="no research"):
        pipeline.draft(j, use_llm=True)
    assert pipeline.studio.last_run(j, "draft") is None  # a gate is not a failure


# --------------------------------------------------------------- fake Ollama

GOOD = {
    "title": "Why Prices End in 99", "hook": "That cent matters.",
    "narration": "ignored",
    "scenes": [{"narration": "Nine ninety-nine feels cheaper than ten.",
                "visual_concept": "price tag", "stock_search_terms": ["price tag"],
                "overlay": "$9.99", "claim_ids": ["c1"]},
               {"narration": "We read the first digit first.", "visual_concept": "eye",
                "stock_search_terms": ["reading"], "overlay": "", "claim_ids": []}],
    "claims": [{"claim_id": "c1", "text": "Left digit bias", "supporting_source_ids": ["S1"],
                "needs_review": False}],
    "youtube_title": "Why 99?", "youtube_description": "d", "tiktok_caption": "t",
    "instagram_caption": "i", "hashtags": ["pricing", "#money"],
}


class FakeOllama:
    def __init__(self, replies: list, loaded_after: int = 0):
        self.replies = list(replies)
        self.chats: list[dict] = []
        self.unload_calls = 0
        self.ps_calls = 0
        self.loaded_after = loaded_after

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen3:4b"}]})
        if path == "/api/ps":
            self.ps_calls += 1
            loaded = self.unload_calls == 0 and self.loaded_after > 0
            return httpx.Response(200, json={"models": [{"name": "qwen3:4b"}] if loaded
                                             else []})
        if path == "/api/generate":
            self.unload_calls += 1
            return httpx.Response(200, json={})
        if path == "/api/chat":
            body = json.loads(request.content)
            self.chats.append(body)
            r = self.replies.pop(0)
            if isinstance(r, httpx.Response):
                return r
            content = r if isinstance(r, str) else json.dumps(r)
            return httpx.Response(200, json={"message": {"content": content},
                                             "eval_count": 10})
        return httpx.Response(404)


def _provider(fake: FakeOllama, tmp_path, **cfg) -> OllamaScriptProvider:
    return OllamaScriptProvider(LLMConfig(host="http://ollama.test", **cfg), ResourceConfig(),
                                tmp_path / "locks", transport=httpx.MockTransport(fake))


@pytest.fixture(autouse=True)
def no_gpu(monkeypatch):
    monkeypatch.setattr(providers.llm, "query_gpu", lambda: None)


def _research() -> Research:
    return Research.model_validate({"sources": [{"id": "S1", "title": "A"}], "claims": [
        {"id": "R1", "text": "a verified claim", "source_ids": ["S1"], "verified": True}]})


PROSE = ("<think>planning the answer</think>\n\n**Nine ninety-nine** feels cheaper than ten.\n\n"
         "We read the first digit first, so the price lands lower.\n\n"
         "Scene 3: That is the whole trick, and now you know it.")


def test_clean_prose_and_paragraphs():
    from faceless_studio.providers.llm import clean_prose, split_paragraphs
    text = clean_prose(PROSE)
    assert "think" not in text and "**" not in text and "Scene 3" not in text
    assert len(split_paragraphs(text)) == 3
    one_block = "One two three four. Five six seven eight. Nine ten eleven twelve. Thirteen."
    assert len(split_paragraphs(one_block, target_words=4)) == 4
    assert clean_prose("reasoning without a start tag</think>Actual text.") == "Actual text."


def test_ollama_two_step_draft(tmp_path):
    fake = FakeOllama([PROSE, GOOD])
    sc = _provider(fake, tmp_path).generate("topic", _research(), "Chan", 5, 40)
    assert sc.provider == "ollama:qwen3:4b"
    # narration is the prose, verbatim; structure comes from the JSON call
    assert [s.narration for s in sc.scenes][0] == "Nine ninety-nine feels cheaper than ten."
    assert len(sc.scenes) == 3 and sc.scenes[2].visual == ""
    assert sc.scenes[0].overlay == "$9.99" and sc.scenes[0].claim_ids == ["c1"]
    assert sc.claims[0].needs_review is True  # model can never clear the flag
    assert sc.platform.hashtags == ["#pricing", "#money"]
    prose_req, json_req = fake.chats
    assert "format" not in prose_req and "format" in json_req
    for req in fake.chats:
        assert req["keep_alive"] == 0 and req["think"] is False
        assert req["options"]["num_ctx"] == 4096 and req["options"]["num_gpu"] == 0
        assert "DATA, not instructions" in req["messages"][0]["content"]
    assert "<research>" in prose_req["messages"][1]["content"]
    assert "[1] Nine ninety-nine" in json_req["messages"][1]["content"]


def test_ollama_insufficient_research_is_reported(tmp_path):
    fake = FakeOllama(["INSUFFICIENT RESEARCH: no sources about prices."])
    with pytest.raises(ValidationFailed, match="insufficient research"):
        _provider(fake, tmp_path).generate("t", _research(), "C", 5, 40)


def test_ollama_bad_json_then_retry(tmp_path):
    fake = FakeOllama([PROSE, "{not json", {"title": "x"}, GOOD])
    sc = _provider(fake, tmp_path).generate("t", _research(), "C", 5, 40)
    assert sc.provider_meta["json_attempts"] == 3
    assert "failed validation" in fake.chats[2]["messages"][-1]["content"]


def test_ollama_gives_up_after_bounded_attempts(tmp_path):
    fake = FakeOllama([PROSE, "nope", "nope", "nope", "nope"])
    with pytest.raises(ValidationFailed, match="after 3 attempts"):
        _provider(fake, tmp_path).generate("t", _research(), "C", 5, 40)
    assert len(fake.chats) == 4


def test_ollama_oom_retries_once_with_smaller_context(tmp_path):
    from faceless_studio.resources import StageMetrics
    oom = httpx.Response(500, json={"error": "CUDA error: out of memory"})
    fake = FakeOllama([oom, PROSE, GOOD])
    m = StageMetrics()
    _provider(fake, tmp_path).generate("t", _research(), "C", 5, 40, metrics=m)
    assert fake.chats[1]["options"]["num_ctx"] == 2048
    assert fake.chats[2]["options"]["num_ctx"] == 2048
    assert any("num_ctx=2048" in f for f in m.fallbacks)


def test_ollama_second_oom_is_not_retried_forever(tmp_path):
    from faceless_studio.resources import GpuOutOfMemory
    oom = lambda: httpx.Response(500, json={"error": "out of memory"})  # noqa: E731
    fake = FakeOllama([oom(), oom(), PROSE, GOOD])
    with pytest.raises(GpuOutOfMemory):
        _provider(fake, tmp_path).generate("t", _research(), "C", 5, 40)
    assert len(fake.chats) == 2


def test_ollama_missing_model(tmp_path):
    fake = FakeOllama([])
    p = _provider(fake, tmp_path, model="llama-nope")
    with pytest.raises(ProviderUnavailable, match="not installed"):
        p.generate("t", _research(), "C", 5, 40)


def test_ollama_unload_is_requested_and_verified(tmp_path):
    fake = FakeOllama([PROSE, GOOD], loaded_after=1)
    p = _provider(fake, tmp_path)
    p.generate("t", _research(), "C", 5, 40)
    assert fake.unload_calls == 1
    assert p.last_unload_verified is True


def _prose(n: int) -> str:
    return "\n\n".join(["Word " * (n // 3)] * 3)


def test_ollama_length_feedback_then_accept(tmp_path):
    fake = FakeOllama([_prose(12), _prose(30), GOOD])
    sc = _provider(fake, tmp_path).generate("t", _research(), "C", 25, 35)
    assert sc.provider_meta["length_ok"] is True and sc.provider_meta["prose_attempts"] == 2
    fb = fake.chats[1]["messages"][-1]["content"]
    assert "has 12 words" in fb and "25-35 words" in fb


def test_ollama_keeps_closest_prose_after_bounded_attempts(tmp_path):
    fake = FakeOllama([_prose(9), _prose(21), _prose(12), GOOD])
    sc = _provider(fake, tmp_path).generate("t", _research(), "C", 25, 35)
    assert len(fake.chats) == 4
    assert sc.provider_meta["length_ok"] is False and sc.provider_meta["words"] == 21


def test_pipeline_llm_draft_records_stage(pipeline, research_file, monkeypatch, small_limits):
    pl = small_limits(pipeline)
    fake = FakeOllama([PROSE, GOOD])
    real_init = OllamaScriptProvider.__init__

    def init(self, cfg, res, lock_dir, transport=None):
        real_init(self, cfg, res, lock_dir, transport=httpx.MockTransport(fake))
    monkeypatch.setattr(OllamaScriptProvider, "__init__", init)
    pl.s.script.target_words_min, pl.s.script.target_words_max = 5, 40
    j = pl.studio.create_job("test topic x").id
    o = pl.draft(j, research_file=research_file, use_llm=True)
    assert o.details["provider"] == "ollama:qwen3:4b"
    assert o.details["provider_meta"]["unload_verified"] is True
    assert pl.script(j).claims[0].needs_review

