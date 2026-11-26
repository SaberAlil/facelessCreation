"""Optional ComfyUI image provider (local HTTP API only, behind ``comfyui.enabled``).

- Refuses to run without a configured checkpoint *and* its license note.
- Takes the GPU lock, checks VRAM headroom, generates one image (batch size 1) at a modest
  portrait size (default 512x768); the renderer upscales during composition.
- Always asks ComfyUI to unload models and free memory afterwards (``POST /free``).
- On out-of-memory: frees memory and retries once with fewer steps and a smaller size;
  callers fall back to original graphics if that fails too.
- Records model, seed and settings for provenance. Downloads nothing.
"""

from __future__ import annotations

import contextlib
import copy
import json
import logging
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import httpx

from ..config import ComfyConfig, ResourceConfig
from ..resources import GpuOutOfMemory, check_vram, gpu_lock, looks_like_oom
from ..util import FacelessError, ProviderUnavailable, atomic_write_bytes

log = logging.getLogger("faceless")

NEGATIVE = ("text, watermark, logo, signature, brand name, celebrity, real person, "
            "deformed, blurry, lowres, nsfw")


@dataclass
class ImageResult:
    path: Path
    seed: int
    width: int
    height: int
    steps: int
    model: str
    prompt: str
    prompt_id: str
    retried_after_oom: bool = False


class ComfyUIProvider:
    name = "comfyui"

    def __init__(self, cfg: ComfyConfig, res: ResourceConfig, lock_dir: Path, workflow: Path,
                 transport: httpx.BaseTransport | None = None):
        if not cfg.enabled:
            raise ProviderUnavailable("ComfyUI provider is disabled (comfyui.enabled: false)")
        if not cfg.checkpoint:
            raise ProviderUnavailable("comfyui.checkpoint is not set",
                                      hint="choose a commercially usable SD 1.5 checkpoint, "
                                      "place it in the comfyui-models volume, set its file name")
        if not cfg.checkpoint_license:
            raise ProviderUnavailable("comfyui.checkpoint_license is empty",
                                      hint="record the checkpoint's license (and that it allows "
                                      "commercial use of outputs) before generating")
        if not cfg.checkpoint_commercial_use:
            raise ProviderUnavailable("comfyui.checkpoint_commercial_use is false",
                                      hint="set it to true only if the checkpoint license "
                                      "allows commercial use of generated images")
        self.cfg = cfg
        self.res = res
        self.lock_dir = lock_dir
        self.template = json.loads(workflow.read_text(encoding="utf-8"))
        self.client = httpx.Client(base_url=cfg.host, timeout=30, transport=transport)

    def _workflow(self, prompt: str, seed: int, width: int, height: int, steps: int) -> dict:
        wf = copy.deepcopy(self.template)
        # Values are set on known node fields, never by string substitution.
        wf["4"]["inputs"]["ckpt_name"] = self.cfg.checkpoint
        wf["6"]["inputs"]["text"] = prompt
        wf["7"]["inputs"]["text"] = NEGATIVE
        wf["5"]["inputs"].update({"width": width, "height": height, "batch_size": 1})
        wf["3"]["inputs"].update({"seed": seed, "steps": steps, "cfg": self.cfg.cfg})
        return wf

    def free(self) -> None:
        with contextlib.suppress(httpx.HTTPError):
            self.client.post("/free", json={"unload_models": True, "free_memory": True})

    def _run(self, wf: dict) -> tuple[str, dict]:
        try:
            r = self.client.post("/prompt", json={"prompt": wf, "client_id": uuid.uuid4().hex})
        except httpx.HTTPError as e:
            raise ProviderUnavailable(f"ComfyUI not reachable at {self.cfg.host}: {e}") from e
        if r.status_code >= 400:
            raise FacelessError(f"ComfyUI rejected the workflow: {r.text[:400]}")
        pid = r.json()["prompt_id"]
        deadline = time.monotonic() + self.cfg.timeout_s
        while time.monotonic() < deadline:
            h = self.client.get(f"/history/{pid}").json().get(pid)
            if h:
                status = h.get("status", {})
                if status.get("status_str") == "error":
                    msg = json.dumps(status.get("messages", []))[:1500]
                    if looks_like_oom(msg):
                        raise GpuOutOfMemory(f"ComfyUI out of memory: {msg[:300]}")
                    raise FacelessError(f"ComfyUI generation failed: {msg[:400]}")
                if h.get("outputs"):
                    return pid, h["outputs"]
            time.sleep(1.0)
        raise FacelessError(f"ComfyUI timed out after {self.cfg.timeout_s:.0f}s")

    def _download(self, outputs: dict, out_png: Path) -> None:
        for node in outputs.values():
            for img in node.get("images", []):
                r = self.client.get("/view", params={"filename": img["filename"],
                                                     "subfolder": img.get("subfolder", ""),
                                                     "type": img.get("type", "output")})
                r.raise_for_status()
                if len(r.content) > 50 * 1024 * 1024:
                    raise FacelessError("ComfyUI image unexpectedly large")
                atomic_write_bytes(out_png, r.content)
                return
        raise FacelessError("ComfyUI returned no image")

    def generate(self, prompt: str, out_png: Path, seed: int) -> ImageResult:
        w, h, steps = self.cfg.width, self.cfg.height, self.cfg.steps
        retried = False
        with gpu_lock(self.lock_dir, "comfyui-image", self.res.gpu_lock_timeout_s):
            check_vram(self.cfg.estimated_vram_mib, self.res.vram_headroom_mib, "comfyui")
            try:
                try:
                    pid, outputs = self._run(self._workflow(prompt, seed, w, h, steps))
                except GpuOutOfMemory as e:
                    log.warning("%s; retrying once with reduced settings", e)
                    self.free()
                    retried = True
                    w, h, steps = 448, 672, self.cfg.reduced_steps
                    pid, outputs = self._run(self._workflow(prompt, seed, w, h, steps))
                self._download(outputs, out_png)
            finally:
                self.free()  # unload models so the next GPU stage has the VRAM
        return ImageResult(out_png, seed, w, h, steps, self.cfg.checkpoint, prompt, pid,
                           retried)
