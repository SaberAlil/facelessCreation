"""ComfyUI provider against a mocked local API (no ComfyUI or checkpoint needed)."""

from __future__ import annotations

import json

import httpx
import pytest
from conftest import REPO, needs_ffmpeg

from faceless_studio.config import ComfyConfig, ResourceConfig
from faceless_studio.providers import images
from faceless_studio.providers.images import ComfyUIProvider
from faceless_studio.util import ProviderUnavailable, run

WORKFLOW = REPO / "config" / "comfyui" / "sd15_portrait.json"


class FakeComfy:
    def __init__(self, png: bytes, ooms: int = 0):
        self.png = png
        self.ooms = ooms
        self.prompts: list[dict] = []
        self.frees = 0

    def __call__(self, req: httpx.Request) -> httpx.Response:
        p = req.url.path
        if p == "/object_info/CheckpointLoaderSimple":
            return httpx.Response(200, json={"CheckpointLoaderSimple": {"input": {"required": {
                "ckpt_name": [["test-sd15.safetensors"], {}]}}}})
        if p == "/prompt":
            self.prompts.append(json.loads(req.content)["prompt"])
            return httpx.Response(200, json={"prompt_id": f"id{len(self.prompts)}"})
        if p.startswith("/history/"):
            pid = p.rsplit("/", 1)[1]
            if len(self.prompts) <= self.ooms:
                return httpx.Response(200, json={pid: {"status": {
                    "status_str": "error",
                    "messages": [["execution_error", {"exception_message":
                                                      "CUDA out of memory"}]]}}})
            return httpx.Response(200, json={pid: {"status": {"status_str": "success"},
                                                   "outputs": {"9": {"images": [
                                                       {"filename": "a.png", "subfolder": "",
                                                        "type": "output"}]}}}})
        if p == "/view":
            return httpx.Response(200, content=self.png)
        if p == "/free":
            self.frees += 1
            return httpx.Response(200)
        return httpx.Response(404)


@pytest.fixture
def png(tmp_path):
    p = tmp_path / "x.png"
    run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=512x768",
         "-frames:v", "1", str(p)], timeout=30)
    return p.read_bytes()


@pytest.fixture(autouse=True)
def vram_ok(monkeypatch):
    monkeypatch.setattr(images, "check_vram", lambda *a, **k: None)


def _cfg(**kw) -> ComfyConfig:
    base = {"enabled": True, "checkpoint": "test-sd15.safetensors",
            "checkpoint_license": "TEST license", "checkpoint_commercial_use": True}
    return ComfyConfig(**{**base, **kw})


@pytest.mark.parametrize("kw,msg", [({"enabled": False}, "disabled"),
                                    ({"checkpoint": ""}, "checkpoint is not set"),
                                    ({"checkpoint_license": ""}, "license"),
                                    ({"checkpoint_commercial_use": False}, "commercial")])
def test_refuses_without_license_info(tmp_path, kw, msg):
    with pytest.raises(ProviderUnavailable, match=msg):
        ComfyUIProvider(_cfg(**kw), ResourceConfig(), tmp_path, WORKFLOW)


@needs_ffmpeg
def test_generate_sets_workflow_and_frees(tmp_path, png):
    fake = FakeComfy(png)
    p = ComfyUIProvider(_cfg(), ResourceConfig(), tmp_path, WORKFLOW,
                        transport=httpx.MockTransport(fake))
    res = p.generate("a calculator", tmp_path / "out.png", seed=42)
    wf = fake.prompts[0]
    assert wf["4"]["inputs"]["ckpt_name"] == "test-sd15.safetensors"
    assert wf["5"]["inputs"] == {"width": 512, "height": 768, "batch_size": 1}
    assert wf["3"]["inputs"]["seed"] == 42 and wf["6"]["inputs"]["text"] == "a calculator"
    assert "watermark" in wf["7"]["inputs"]["text"]
    assert res.path.read_bytes() == png and fake.frees == 1


@needs_ffmpeg
def test_oom_retries_once_with_reduced_settings(tmp_path, png):
    fake = FakeComfy(png, ooms=1)
    p = ComfyUIProvider(_cfg(), ResourceConfig(), tmp_path, WORKFLOW,
                        transport=httpx.MockTransport(fake))
    res = p.generate("x", tmp_path / "o.png", seed=1)
    assert res.retried_after_oom
    assert (res.width, res.height, res.steps) == (448, 672, _cfg().reduced_steps)
    assert fake.frees == 2  # after the OOM and at the end


@needs_ffmpeg
def test_double_oom_falls_back_to_graphics(pipeline, short_script, research_file, small_limits,
                                           png):
    pl = small_limits(pipeline)
    pl.s.comfyui = _cfg()
    pl.s.visuals.order = ["comfyui", "graphics"]
    fake = FakeComfy(png, ooms=99)
    j = pl.studio.create_job("ai test").id
    pl.draft(j, script_file=short_script, research_file=research_file)
    pl.approve_script(j, "t")
    o = pl.assets(j, ai_images=True, comfy_transport=httpx.MockTransport(fake))
    assert o.details["by_source"] == {"procedural": 3}
    assert len(o.details["errors"]) == 3 and o.fallbacks
    assert len(fake.prompts) == 6  # one retry per scene, never more


@needs_ffmpeg
def test_ai_images_recorded_with_provenance(pipeline, short_script, research_file,
                                            small_limits, png):
    pl = small_limits(pipeline)
    pl.s.comfyui = _cfg()
    pl.s.visuals.order = ["comfyui", "graphics"]
    fake = FakeComfy(png)
    j = pl.studio.create_job("ai test").id
    pl.draft(j, script_file=short_script, research_file=research_file)
    pl.approve_script(j, "t")
    o = pl.assets(j, ai_images=True, comfy_transport=httpx.MockTransport(fake))
    assert o.details["by_source"] == {"comfyui": 3}
    m = pl.manifest(j)
    a = m.scenes[0]
    assert a.ai_generated and a.provider == "comfyui" and "TEST license" in a.license
    from faceless_studio.library import get_asset
    notes = json.loads(get_asset(pl.studio.db, a.library_id).notes)
    assert notes["model"] == "test-sd15.safetensors" and "seed" in notes


def test_unavailable_or_missing_checkpoint_skips_ai_once(pipeline, short_script, research_file,
                                                         small_limits, png):
    pl = small_limits(pipeline)
    pl.s.comfyui = _cfg(checkpoint="other.safetensors")
    pl.s.visuals.order = ["comfyui", "graphics"]
    fake = FakeComfy(png)
    j = pl.studio.create_job("ai test").id
    pl.draft(j, script_file=short_script, research_file=research_file)
    pl.approve_script(j, "t")
    o = pl.assets(j, ai_images=True, comfy_transport=httpx.MockTransport(fake))
    assert o.details["by_source"] == {"procedural": 3} and fake.prompts == []
    assert len(o.details["errors"]) == 1 and "not found" in o.details["errors"][0]
    down = httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("refused")))
    o = pl.assets(j, ai_images=True, rebuild=True, comfy_transport=down)
    assert "not reachable" in o.details["errors"][0]


def test_ai_images_default_from_config(pipeline, short_script, research_file, small_limits,
                                       png):
    pl = small_limits(pipeline)
    pl.s.comfyui = _cfg()
    pl.s.visuals.order = ["comfyui", "graphics"]
    fake = FakeComfy(png)
    j = pl.studio.create_job("ai default test").id
    pl.draft(j, script_file=short_script, research_file=research_file)
    pl.approve_script(j, "t")
    o = pl.assets(j, comfy_transport=httpx.MockTransport(fake))  # ai_images not given
    assert o.details["by_source"] == {"comfyui": 3}
    assert "documentary photograph" in fake.prompts[0]["6"]["inputs"]["text"]
