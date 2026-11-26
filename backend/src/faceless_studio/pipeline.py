"""Stage orchestration, freshness (content hashes), derived job state and review gates.

State is *derived* from artifacts and approvals: every approval stores the hash of what
was approved and only counts while that hash still matches. Editing or regenerating an
upstream artifact therefore invalidates downstream approvals and marks derived stages
stale, without any bookkeeping that could drift.
"""

from __future__ import annotations

import logging
import shutil
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import assets as assets_mod
from . import quality, render, research, subtitles, voice
from .jobs import Studio, files_hash
from .resources import GpuOutOfMemory, StageMetrics, StageMonitor, file_lock
from .schemas import (
    STATE_ORDER,
    AssetManifest,
    CaptionTrack,
    JobState,
    Research,
    Script,
    VoiceTiming,
)
from .util import (
    FacelessError,
    GateError,
    NotFoundError,
    ValidationFailed,
    atomic_write_text,
    hash_obj,
    read_json,
    sha256_file,
    user_file,
    utcnow,
    write_json,
)

log = logging.getLogger("faceless")

STAGES = ["script", "voice", "assets", "subtitles", "render", "export"]


@dataclass
class Gate:
    """Where automatic progress stops, and what a human must do next."""

    job_id: str
    state: JobState
    reason: str
    next_command: str
    done: bool = False


@dataclass
class StageOutcome:
    stage: str
    details: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    fallbacks: list[str] = field(default_factory=list)


class Pipeline:
    def __init__(self, studio: Studio | None = None):
        self.studio = studio or Studio()
        self.s = self.studio.settings

    # ------------------------------------------------------------ artifacts
    def paths(self, job_id: str):
        return self.studio.paths(job_id)

    def script(self, job_id: str) -> Script | None:
        p = self.paths(job_id).script
        if not p.is_file():
            return None
        try:
            return Script.model_validate(read_json(p))
        except Exception as e:
            raise ValidationFailed(f"{p} is invalid: {e}",
                                   hint="fix the file or re-run 'faceless draft'") from e

    def require_script(self, job_id: str) -> Script:
        sc = self.script(job_id)
        if sc is None:
            raise GateError("no script yet", hint=f"faceless draft {job_id} --script FILE")
        return sc

    def research(self, job_id: str) -> Research:
        return research.load_research(self.paths(job_id).research)

    def timing(self, job_id: str) -> VoiceTiming | None:
        p = self.paths(job_id).timing
        return VoiceTiming.model_validate(read_json(p)) if p.is_file() else None

    def manifest(self, job_id: str) -> AssetManifest | None:
        return assets_mod.load_manifest(self.paths(job_id))

    def captions(self, job_id: str) -> CaptionTrack | None:
        p = self.paths(job_id).captions_json
        return CaptionTrack.model_validate(read_json(p)) if p.is_file() else None

    # --------------------------------------------------------------- hashes
    def _tts_settings(self) -> dict:
        t = self.s.tts
        return {"voice": t.voice, "speed": t.speed, "lang": t.lang_code,
                "pause": t.pause_between_scenes_ms, "tail": t.tail_silence_ms,
                "lufs": t.narration_lufs, "repo": t.kokoro_repo}

    def script_hash(self, job_id: str) -> str | None:
        return files_hash(self.paths(job_id).script)

    def script_approval_hash(self, job_id: str) -> str | None:
        p = self.paths(job_id)
        h = files_hash(p.script)
        if h is None:
            return None
        return hash_obj({"script": h, "research": files_hash(p.research)})

    def voice_input_hash(self, job_id: str, provider_key: str) -> str | None:
        sh = self.script_hash(job_id)
        return None if sh is None else hash_obj({"script": sh, "provider": provider_key,
                                                 "tts": self._tts_settings()})

    def voice_output_hash(self, job_id: str) -> str | None:
        p = self.paths(job_id)
        return files_hash(p.narration, p.timing)

    def assets_input_hash(self, job_id: str) -> str | None:
        sc = self.script(job_id)
        if sc is None:
            return None
        return hash_obj([[x.id, x.visual, x.search_terms, x.overlay] for x in sc.scenes])

    def assets_output_hash(self, job_id: str) -> str | None:
        return files_hash(self.paths(job_id).manifest)

    def subtitles_input_hash(self, job_id: str) -> str | None:
        vo, sh = self.voice_output_hash(job_id), self.script_hash(job_id)
        if vo is None or sh is None:
            return None
        sub = self.s.subtitles.model_dump(exclude={"fallback_model"})
        return hash_obj({"voice": vo, "script": sh, "subs": sub,
                         "W": self.s.render.width, "H": self.s.render.height})

    def subtitles_output_hash(self, job_id: str) -> str | None:
        p = self.paths(job_id)
        return files_hash(p.captions_json, p.ass)

    def _render_settings(self) -> dict:
        return self.s.render.model_dump(exclude={"encoder", "scene_timeout_s", "final_timeout_s",
                                                 "min_duration_s", "max_duration_s"})

    def render_input_hash(self, job_id: str, preview: bool = False) -> str | None:
        voice_h, assets_h = self.voice_output_hash(job_id), self.assets_output_hash(job_id)
        sc = self.script(job_id)
        if voice_h is None or assets_h is None or sc is None:
            return None
        return hash_obj({"voice": voice_h, "assets": assets_h,
                         "subs": self.subtitles_output_hash(job_id), "preview": preview,
                         "render": self._render_settings(), "font": str(self.s.font_file()),
                         "title": sc.title})

    def render_output_hash(self, job_id: str, preview: bool = False) -> str | None:
        p = self.paths(job_id)
        return files_hash(p.preview if preview else p.final)

    def final_approval_hash(self, job_id: str) -> str | None:
        p = self.paths(job_id)
        h = files_hash(p.final, p.srt, p.script, p.manifest, p.render_dir / "cover.png")
        return h

    def export_output_hash(self, job_id: str) -> str | None:
        d = self.paths(job_id).export_dir
        if not d.is_dir():
            return None
        files = sorted(f for f in d.iterdir() if f.is_file())
        return files_hash(*files) if files else None

    # ------------------------------------------------------------ freshness
    def stage_fresh(self, job_id: str, stage: str) -> bool:
        run = self.studio.last_run(job_id, stage, "ok")
        if not run:
            return False
        if stage == "voice":
            expected_in = self.voice_input_hash(job_id, run["details"].get("provider_key", ""))
            out = self.voice_output_hash(job_id)
        elif stage == "assets":
            expected_in, out = self.assets_input_hash(job_id), self.assets_output_hash(job_id)
        elif stage == "subtitles":
            expected_in = self.subtitles_input_hash(job_id)
            out = self.subtitles_output_hash(job_id)
        elif stage == "render":
            expected_in = self.render_input_hash(job_id)
            out = self.render_output_hash(job_id)
        elif stage == "export":
            expected_in = self.final_approval_hash(job_id)
            out = self.export_output_hash(job_id)
        else:
            raise ValueError(stage)
        return (expected_in is not None and out is not None and run["input_hash"] == expected_in
                and run["output_hash"] == out)

    def approval_valid(self, job_id: str, kind: str) -> bool:
        appr = self.studio.latest_approval(job_id, kind)
        if not appr:
            return False
        current = {"script": self.script_approval_hash, "assets": self.assets_output_hash,
                   "final": self.final_approval_hash}[kind](job_id)
        return current is not None and appr["artifact_hash"] == current

    # ---------------------------------------------------------------- state
    def _achieved_state(self, job_id: str) -> JobState:
        if not self.paths(job_id).script.is_file():
            return JobState.DRAFT
        if not self.approval_valid(job_id, "script"):
            return JobState.RESEARCH_REVIEW
        if not (self.stage_fresh(job_id, "voice") and self.stage_fresh(job_id, "assets")
                and self.approval_valid(job_id, "assets")):
            return JobState.SCRIPT_APPROVED
        if not (self.stage_fresh(job_id, "subtitles") and self.stage_fresh(job_id, "render")):
            return JobState.ASSETS_READY
        if not self.approval_valid(job_id, "final"):
            return JobState.RENDERED
        if not self.stage_fresh(job_id, "export"):
            return JobState.FINAL_APPROVED
        return JobState.EXPORTED

    def derive_state(self, job_id: str) -> tuple[JobState, str | None, str | None]:
        """Achieved state, overlaid with FAILED if a stage's latest run failed."""
        for st in ["draft", *STAGES]:
            last = self.studio.last_run(job_id, st)
            if last and last["status"] == "failed":
                return JobState.FAILED, st, last["error"]
        return self._achieved_state(job_id), None, None

    def refresh(self, job_id: str, note: str = "") -> JobState:
        state, fstage, err = self.derive_state(job_id)
        self.studio.set_state(job_id, state, note=note, failed_stage=fstage, error=err)
        return state

    def effective_state(self, job_id: str) -> JobState:
        """What has actually been achieved, ignoring a FAILED overlay."""
        return self._achieved_state(job_id)

    def at_least(self, job_id: str, state: JobState) -> bool:
        return STATE_ORDER.index(self.effective_state(job_id)) >= STATE_ORDER.index(state)

    # ---------------------------------------------------------- stage runner
    def _run_stage(self, job_id: str, stage: str, input_hash: str | None,
                   fn: Callable[[StageMetrics], dict], output_hash: Callable[[], str | None],
                   device: str = "cpu") -> StageOutcome:
        self.studio.get_job(job_id)
        started = utcnow()
        mon = StageMonitor(device=device, sample_interval_s=self.s.resources.sample_interval_s)
        err: BaseException | None = None
        details: dict = {}
        with mon:
            try:
                details = fn(mon.metrics) or {}
            except BaseException as e:  # record, then re-raise
                err = e
        m = mon.metrics
        if err is not None:
            msg = f"{type(err).__name__}: {err}"
            self.studio.record_stage(job_id, stage, "failed", started, m.duration_s,
                                     input_hash, None, m.as_dict(), m.fallbacks,
                                     {"traceback": traceback.format_exc(limit=6)[-4000:]},
                                     msg[:2000])
            self.refresh(job_id, note=f"{stage} failed")
            raise err
        out_hash = output_hash()
        self.studio.record_stage(job_id, stage, "ok", started, m.duration_s, input_hash,
                                 out_hash, m.as_dict(), m.fallbacks, details)
        self.refresh(job_id, note=f"{stage} ok")
        return StageOutcome(stage, details, m.as_dict(), m.fallbacks)

    # ======================================================== M2: scripts
    def set_research(self, job_id: str, research_file: Path) -> Research:
        r = research.load_research(user_file(research_file))
        research.save_research(self.paths(job_id).research, r)
        self.refresh(job_id, "research updated")
        return r

    def draft(self, job_id: str, script_file: Path | None = None,
              research_file: Path | None = None, title: str | None = None,
              use_llm: bool = False, pasted_text: str | None = None) -> StageOutcome:
        job = self.studio.job_input(job_id)
        p = self.paths(job_id)
        if research_file:
            self.set_research(job_id, research_file)
        res = self.research(job_id)
        if script_file is None and pasted_text is None and not use_llm:
            raise FacelessError("give --script FILE, --text, or --llm")
        if use_llm and script_file is None and pasted_text is None and not res.sources:
            raise GateError(
                "no research recorded: the LLM may only write from supplied research",
                hint=f"faceless research init {job_id}; add sources/claims; or pass "
                "--research FILE")

        def fn(metrics: StageMetrics) -> dict:
            from .providers.llm import ManualScriptProvider, OllamaScriptProvider, parse_text_script
            if script_file is not None:
                sc = ManualScriptProvider().load(user_file(script_file), title)
                metrics.device = "none"
            elif pasted_text is not None:
                sc = parse_text_script(pasted_text, title)
                metrics.device = "none"
            elif use_llm:
                prov = OllamaScriptProvider(self.s.llm, self.s.resources, self.s.locks_dir)
                sc = prov.generate(job.topic, res, self.s.channel.name,
                                   self.s.script.target_words_min,
                                   self.s.script.target_words_max, metrics)
                sc.provider_meta["unload_verified"] = getattr(prov, "last_unload_verified",
                                                              None)
            else:
                raise FacelessError("give --script FILE, --text, or --llm")
            report = research.check_script(sc, res, self.s.script)
            if p.script.is_file():
                p.drafts.mkdir(parents=True, exist_ok=True)
                stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
                shutil.copy2(p.script, p.drafts / f"script-{stamp}.json")
            write_json(p.script, sc.model_dump(mode="json"))
            self._write_script_review(job_id, sc, res, report)
            return {"provider": sc.provider, "words": report.words, "errors": report.errors,
                    "warnings": report.warnings, "flagged": report.flagged_claims,
                    "provider_meta": sc.provider_meta}

        return self._run_stage(job_id, "draft", None, fn, lambda: self.script_hash(job_id))

    def _write_script_review(self, job_id: str, sc: Script, res: Research,
                             report: research.ScriptReport) -> Path:
        lines = [f"# Script review: {sc.title}", "", f"Provider: {sc.provider}",
                 f"Words: {report.words} (≈{report.est_seconds}s spoken)", ""]
        for label, items in (("Errors", report.errors), ("Warnings", report.warnings),
                             ("Claims needing review", report.flagged_claims),
                             ("Research problems", research.research_problems(res))):
            lines.append(f"## {label}")
            lines += [f"- {x}" for x in items] or ["- none"]
            lines.append("")
        lines.append("## Scenes")
        for s in sc.scenes:
            lines += [f"### {s.id}", s.narration, "",
                      f"*visual:* {s.visual or '-'} · *terms:* {', '.join(s.search_terms) or '-'}"
                      f" · *overlay:* {s.overlay or '-'} · *claims:* "
                      f"{', '.join(s.claim_ids) or '-'}", ""]
        lines.append("## Sources")
        for src in res.sources:
            lines.append(f"- [{src.id}] {src.title} — {src.publisher} ({src.published or '?'}) "
                         f"{src.url or ''}")
        out = self.paths(job_id).review_dir / "script_review.md"
        atomic_write_text(out, "\n".join(lines) + "\n")
        return out

    def review_script(self, job_id: str) -> dict:
        sc = self.require_script(job_id)
        res = self.research(job_id)
        report = research.check_script(sc, res, self.s.script)
        path = self._write_script_review(job_id, sc, res, report)
        return {"script": sc, "report": report, "research_problems":
                research.research_problems(res), "review_file": str(path),
                "approved": self.approval_valid(job_id, "script")}

    def approve_script(self, job_id: str, reviewer: str | None = None,
                       acknowledge_flags: bool = False, notes: str = "") -> str:
        sc = self.require_script(job_id)
        res = self.research(job_id)
        report = research.check_script(sc, res, self.s.script)
        if report.errors:
            raise GateError("script has errors: " + "; ".join(report.errors))
        if report.flagged_claims and not acknowledge_flags:
            raise GateError(
                f"{len(report.flagged_claims)} claim(s) need review:\n  - "
                + "\n  - ".join(report.flagged_claims),
                hint="fix the claims/research, or pass --acknowledge-flags after you have "
                "fact-checked them yourself")
        h = self.script_approval_hash(job_id)
        assert h
        self.studio.approve(job_id, "script", h, reviewer, notes, {
            "words": report.words, "flagged_claims_acknowledged": report.flagged_claims,
            "research_problems": research.research_problems(res), "warnings": report.warnings})
        self.refresh(job_id, "script approved")
        return h

    # ========================================================= M3: voice
    def voice(self, job_id: str, provider: str | None = None,
              narration_file: Path | None = None, tts: Any = None) -> StageOutcome:
        """``tts`` lets tests inject a provider object; normal use selects by name."""
        if not self.approval_valid(job_id, "script"):
            raise GateError("script is not approved (or changed since approval)",
                            hint=f"faceless review-script {job_id}; faceless approve-script "
                            f"{job_id}")
        sc = self.require_script(job_id)
        p = self.paths(job_id)
        if narration_file is not None:
            src = user_file(narration_file)
            key = f"user:{sha256_file(src)}"

            def fn(metrics: StageMetrics) -> dict:
                metrics.device = "none"
                vt, d = voice.import_user_narration(self.s, p, sc, src)
                return {**d, "provider_key": key, "provider": "user"}
        else:
            from .providers.tts import get_tts
            tts = tts or get_tts(self.s.tts, provider)
            key = f"{tts.name}:{tts.model_id()}"

            def fn(metrics: StageMetrics) -> dict:
                metrics.device = self.s.tts.device if tts.name == "kokoro" else "cpu"
                vt, d = voice.synthesize_narration(self.s, p, sc, tts)
                if tts.demo:
                    metrics.fallbacks.append(f"demo voice ({tts.name}); not publishable")
                return {**d, "provider_key": key, "provider": tts.name, "demo": tts.demo}

        return self._run_stage(job_id, "voice", self.voice_input_hash(job_id, key), fn,
                               lambda: self.voice_output_hash(job_id))

    # ======================================================== M4: assets
    def assets(self, job_id: str, pexels: bool | None = None, rebuild: bool = False,
               ai_images: bool = False, comfy_transport: Any = None) -> StageOutcome:
        if not self.approval_valid(job_id, "script"):
            raise GateError("script is not approved", hint=f"faceless approve-script {job_id}")
        sc = self.require_script(job_id)
        p = self.paths(job_id)
        use_pexels = self.s.stock.pexels_enabled if pexels is None else pexels

        def fn(metrics: StageMetrics) -> dict:
            metrics.device = "cuda" if ai_images else "cpu"
            m, stats = assets_mod.build_manifest(self.s, self.studio.db, p, sc, use_pexels,
                                                 keep_existing=not rebuild,
                                                 ai_images=ai_images,
                                                 comfy_transport=comfy_transport)
            metrics.fallbacks += [f"graphics after AI error: {e}" for e in stats["ai_errors"]]
            return {**stats, "scenes": [(a.scene_id, a.provider, a.template or a.library_id)
                                        for a in m.scenes]}

        return self._run_stage(job_id, "assets", self.assets_input_hash(job_id), fn,
                               lambda: self.assets_output_hash(job_id))

    def _record_manual_assets(self, job_id: str, action: str) -> None:
        now = utcnow()
        self.studio.record_stage(job_id, "assets", "ok", now, 0.0,
                                 self.assets_input_hash(job_id), self.assets_output_hash(job_id),
                                 {"device": "none"}, [], {"manual": action})
        self.refresh(job_id, f"assets: {action}")

    def replace_asset(self, job_id: str, scene_id: str, library_id: str | None = None,
                      procedural: bool = False, overlay: str | None = None) -> AssetManifest:
        if not self.stage_fresh(job_id, "assets"):
            raise GateError("assets are missing or stale", hint=f"faceless assets {job_id}")
        sc = self.require_script(job_id)
        m = assets_mod.replace_scene_asset(self.s, self.studio.db, self.paths(job_id), sc,
                                           scene_id, library_id, procedural, overlay)
        self._record_manual_assets(job_id, f"replace {scene_id}")
        return m

    def set_music(self, job_id: str, library_id: str | None) -> AssetManifest:
        if not self.stage_fresh(job_id, "assets"):
            raise GateError("assets are missing or stale", hint=f"faceless assets {job_id}")
        m = assets_mod.set_music(self.s, self.studio.db, self.paths(job_id), library_id)
        self._record_manual_assets(job_id, f"music {library_id}")
        return m

    def set_rights(self, job_id: str, scene_ids: list[str] | None, status) -> AssetManifest:
        if not self.stage_fresh(job_id, "assets"):
            raise GateError("assets are missing or stale", hint=f"faceless assets {job_id}")
        m = assets_mod.set_rights(self.paths(job_id), scene_ids, status)
        self._record_manual_assets(job_id, f"rights {status.value} {scene_ids or 'all'}")
        return m

    def asset_review(self, job_id: str) -> dict:
        m = self.manifest(job_id)
        if m is None:
            raise GateError("no assets yet", hint=f"faceless assets {job_id}")
        sc = self.require_script(job_id)
        return {"manifest": m, "problems": assets_mod.asset_problems(self.paths(job_id), m, sc),
                "fresh": self.stage_fresh(job_id, "assets"),
                "approved": self.approval_valid(job_id, "assets")}

    def approve_assets(self, job_id: str, reviewer: str | None = None, notes: str = "") -> str:
        rv = self.asset_review(job_id)
        if not rv["fresh"]:
            raise GateError("assets are stale (script changed)", hint=f"faceless assets {job_id}")
        if rv["problems"]:
            raise GateError("asset review has open problems:\n  - " +
                            "\n  - ".join(rv["problems"]),
                            hint=f"faceless review-assets {job_id} --confirm SCENE_IDS")
        h = self.assets_output_hash(job_id)
        assert h
        self.studio.approve(job_id, "assets", h, reviewer, notes,
                            {"scenes": [(a.scene_id, a.license, a.provider)
                                        for a in rv["manifest"].scenes]})
        self.refresh(job_id, "assets approved")
        return h

    # ===================================================== M5: subtitles
    def subtitles(self, job_id: str, approximate: bool = False, model: str | None = None
                  ) -> StageOutcome:
        if not self.stage_fresh(job_id, "voice"):
            raise GateError("narration is missing or stale", hint=f"faceless voice {job_id}")
        sc = self.require_script(job_id)
        vt = self.timing(job_id)
        assert vt
        p = self.paths(job_id)
        cfg = self.s.subtitles

        def fn(metrics: StageMetrics) -> dict:
            metrics.device = cfg.device
            details: dict[str, Any] = {}
            if approximate:
                words = subtitles.approximate_words(sc, vt)
                track_kw: dict[str, Any] = {"source": "approximate", "approximate": True}
                metrics.fallbacks.append("approximate timing (needs review)")
            else:
                name = model or cfg.model
                try:
                    heard, used = subtitles.transcribe(p.narration, cfg, name, sc.title)
                except (GpuOutOfMemory, MemoryError, RuntimeError) as e:
                    if name == cfg.fallback_model:
                        raise
                    log.warning("Whisper %s failed (%s); retrying with %s", name, e,
                                cfg.fallback_model)
                    metrics.fallbacks.append(f"whisper {cfg.fallback_model} after: {e}")
                    heard, used = subtitles.transcribe(p.narration, cfg, cfg.fallback_model,
                                                       sc.title)
                write_json(p.captions_dir / "transcript.json",
                           [w.model_dump() for w in heard])
                words, ratio, div = subtitles.align(sc.narration, heard)
                track_kw = {"source": "whisper", "model": used, "divergence_ratio": ratio,
                            "divergences": div[:50]}
                details.update({"model": used, "divergence_ratio": ratio,
                                "divergences": len(div)})
            write_json(p.words, [w.model_dump() for w in words])
            caps = subtitles.chunk_words(words, cfg)
            problems = subtitles.validate_captions(caps, vt.duration, cfg)
            if problems:
                raise ValidationFailed("caption timing problems: " + "; ".join(problems[:5]))
            track = CaptionTrack(captions=caps, **track_kw)
            self._save_track(job_id, track, sc)
            details.update({"captions": len(caps), "source": track.source})
            return details

        return self._run_stage(job_id, "subtitles", self.subtitles_input_hash(job_id), fn,
                               lambda: self.subtitles_output_hash(job_id))

    def _save_track(self, job_id: str, track: CaptionTrack, sc: Script) -> None:
        p = self.paths(job_id)
        keywords = {subtitles.norm_token(w) for s in sc.scenes for w in s.overlay.split()
                    if len(w) > 3}
        write_json(p.captions_json, track.model_dump())
        subtitles.write_tracks(track, p.srt, p.ass, self.s.subtitles, self.s.render.width,
                               self.s.render.height, keywords)

    def import_captions(self, job_id: str, srt_file: Path) -> CaptionTrack:
        if not self.stage_fresh(job_id, "voice"):
            raise GateError("narration is missing or stale", hint=f"faceless voice {job_id}")
        vt = self.timing(job_id)
        sc = self.require_script(job_id)
        assert vt
        caps = subtitles.parse_srt(user_file(srt_file).read_text(encoding="utf-8"))
        problems = subtitles.validate_captions(caps, vt.duration, self.s.subtitles)
        if problems:
            raise ValidationFailed("imported captions invalid:\n  - " + "\n  - ".join(problems))
        prev = self.captions(job_id)
        track = CaptionTrack(source="imported", model=prev.model if prev else "",
                             reviewed=True, captions=caps)
        self._save_track(job_id, track, sc)
        self._record_manual_subtitles(job_id, "import")
        return track

    def mark_captions_reviewed(self, job_id: str) -> CaptionTrack:
        if not self.stage_fresh(job_id, "subtitles"):
            raise GateError("captions are missing or stale", hint=f"faceless subtitles {job_id}")
        track = self.captions(job_id)
        assert track
        track.reviewed = True
        self._save_track(job_id, track, self.require_script(job_id))
        self._record_manual_subtitles(job_id, "mark-reviewed")
        return track

    def _record_manual_subtitles(self, job_id: str, action: str) -> None:
        self.studio.record_stage(job_id, "subtitles", "ok", utcnow(), 0.0,
                                 self.subtitles_input_hash(job_id),
                                 self.subtitles_output_hash(job_id), {"device": "none"}, [],
                                 {"manual": action})
        self.refresh(job_id, f"captions {action}")

    # ======================================================== M5: render
    def render(self, job_id: str, preview: bool = False, dry_run: bool = False,
               encoder: str | None = None) -> StageOutcome | dict:
        p = self.paths(job_id)
        sc = self.require_script(job_id)
        if not self.stage_fresh(job_id, "voice"):
            raise GateError("narration is missing or stale", hint=f"faceless voice {job_id}")
        if not self.stage_fresh(job_id, "assets"):
            raise GateError("assets are missing or stale", hint=f"faceless assets {job_id}")
        subs_ok = self.stage_fresh(job_id, "subtitles")
        if not preview:
            if not self.approval_valid(job_id, "assets"):
                raise GateError("assets are not approved",
                                hint=f"faceless review-assets {job_id} (preview with "
                                f"'faceless render {job_id} --preview')")
            if not subs_ok:
                raise GateError("captions are missing or stale",
                                hint=f"faceless subtitles {job_id}")
        vt = self.timing(job_id)
        m = self.manifest(job_id)
        assert vt and m
        ass = p.ass if subs_ok else None
        if dry_run:
            return render.render_job(self.s, p, sc, vt, m, ass, preview, dry_run=True,
                                     force_encoder=encoder)
        stage = "preview" if preview else "render"
        in_hash = self.render_input_hash(job_id, preview)

        def fn(metrics: StageMetrics) -> dict:
            # One render at a time across processes and batch workers.
            with file_lock(self.s.locks_dir, "render", f"render:{job_id}",
                           self.s.resources.gpu_lock_timeout_s):
                details = render.render_job(self.s, p, sc, vt, m, ass, preview,
                                            force_encoder=encoder)
            metrics.device = "nvenc" if details["encoder"] == "h264_nvenc" else "cpu"
            metrics.fallbacks += details.get("fallbacks", [])
            out = p.preview if preview else p.final
            val = quality.validate_video(out, vt.duration, self.s.render, preview=preview)
            write_json(p.render_dir / ("validation.preview.json" if preview
                                       else "validation.json"), val)
            details["validation_ok"] = val["ok"]
            details.pop("commands", None)
            render.save_render_details(p, details)
            if not val["ok"]:
                bad = [f"{c['name']}: {c['detail']}" for c in val["checks"]
                       if c["required"] and not c["ok"]]
                raise ValidationFailed("rendered file failed validation: " + "; ".join(bad))
            return details

        return self._run_stage(job_id, stage, in_hash, fn,
                               lambda: self.render_output_hash(job_id, preview))

    # ==================================================== M6: review/export
    def final_checklist(self, job_id: str) -> list[dict]:
        p = self.paths(job_id)
        items: list[dict] = []

        def add(name: str, ok: bool, detail: str = "", required: bool = True) -> None:
            items.append({"check": name, "ok": bool(ok), "detail": detail,
                          "required": required})

        add("script approved (unchanged since)", self.approval_valid(job_id, "script"))
        appr = self.studio.latest_approval(job_id, "script")
        flagged = (appr or {}).get("checklist", {}).get("flagged_claims_acknowledged", [])
        add("flagged claims were fact-checked by a human", True,
            f"{len(flagged)} acknowledged at script approval" if flagged else "none flagged",
            required=False)
        rp = research.research_problems(self.research(job_id))
        add("research complete", not rp, "; ".join(rp[:4]) or "ok", required=False)
        add("narration current", self.stage_fresh(job_id, "voice"))
        vt = self.timing(job_id)
        add("narration is production voice (not demo)", bool(vt and not vt.demo),
            vt.provider if vt else "none")
        add("assets approved (rights confirmed)", self.approval_valid(job_id, "assets"))
        add("captions current", self.stage_fresh(job_id, "subtitles"))
        tr = self.captions(job_id)
        needs_review = bool(tr and (tr.approximate or (tr.divergence_ratio is not None and
                                                        tr.divergence_ratio <
                                                        self.s.subtitles.divergence_ratio_warn)))
        add("captions reviewed where required", bool(tr) and (not needs_review or tr.reviewed),
            "approximate or divergent timing requires 'faceless subtitles JOB --mark-reviewed'"
            if needs_review and not (tr and tr.reviewed) else
            (f"divergence ratio {tr.divergence_ratio}" if tr and tr.divergence_ratio else "ok"))
        add("final render current", self.stage_fresh(job_id, "render"))
        val = read_json(p.validation) if p.validation.is_file() else None
        add("technical validation passed", bool(val and val["ok"]))
        if val:
            dur = val.get("duration", 0)
            add("duration within publishable range",
                self.s.render.min_duration_s <= dur <= self.s.render.max_duration_s,
                f"{dur:.1f}s")
        add("cover exists", (p.render_dir / "cover.png").is_file())
        return items

    def review(self, job_id: str) -> dict:
        p = self.paths(job_id)
        sc = self.script(job_id)
        m = self.manifest(job_id)
        return {
            "job": self.studio.get_job(job_id), "title": sc.title if sc else None,
            "checklist": self.final_checklist(job_id),
            "assets": [a.model_dump(mode="json") for a in m.scenes] if m else [],
            "files": {"final": str(p.final), "preview": str(p.preview),
                      "cover": str(p.render_dir / "cover.png"), "srt": str(p.srt),
                      "script_review": str(p.review_dir / "script_review.md")},
            "approved": self.approval_valid(job_id, "final"),
        }

    def approve_final(self, job_id: str, reviewer: str | None = None, notes: str = "") -> str:
        items = self.final_checklist(job_id)
        failed = [f"{i['check']} ({i['detail']})" if i["detail"] else i["check"]
                  for i in items if i["required"] and not i["ok"]]
        if failed:
            raise GateError("final approval blocked:\n  - " + "\n  - ".join(failed))
        h = self.final_approval_hash(job_id)
        assert h
        p = self.paths(job_id)
        self.studio.approve(job_id, "final", h, reviewer, notes, {
            "checklist": items, "final_sha256": sha256_file(p.final),
            "srt_sha256": sha256_file(p.srt)})
        self.refresh(job_id, "final approved")
        return h

    def export(self, job_id: str) -> StageOutcome:
        from .export import build_export
        if not self.approval_valid(job_id, "final"):
            raise GateError("final video is not approved (or changed since approval)",
                            hint=f"faceless review {job_id}; faceless approve-final {job_id}")
        p = self.paths(job_id)
        job = self.studio.job_input(job_id)
        sc, vt, m = self.require_script(job_id), self.timing(job_id), self.manifest(job_id)
        assert vt and m
        val = read_json(p.validation)
        details = read_json(p.render_dir / "render.json")

        def fn(metrics: StageMetrics) -> dict:
            metrics.device = "none"
            return build_export(self.s, p, job, sc, self.research(job_id), m, vt, val,
                                self.studio.approvals(job_id), self.studio.stage_runs(job_id),
                                details)

        return self._run_stage(job_id, "export", self.final_approval_hash(job_id), fn,
                               lambda: self.export_output_hash(job_id))

    # ================================================== resume / advance
    def next_gate(self, job_id: str) -> Gate:
        self.refresh(job_id)
        st = self.effective_state(job_id)
        j = job_id
        if st == JobState.DRAFT:
            return Gate(j, st, "needs a script", f"faceless draft {j} --script FILE  (or --llm "
                        "with research)")
        if st == JobState.RESEARCH_REVIEW:
            return Gate(j, st, "script awaits human review",
                        f"faceless review-script {j}; faceless approve-script {j}")
        if st == JobState.SCRIPT_APPROVED:
            if not self.stage_fresh(j, "voice"):
                return Gate(j, st, "narration needed", f"faceless voice {j}")
            if not self.stage_fresh(j, "assets"):
                return Gate(j, st, "assets needed", f"faceless assets {j}")
            return Gate(j, st, "assets await rights review",
                        f"faceless review-assets {j} --confirm-all --approve")
        if st == JobState.ASSETS_READY:
            if not self.stage_fresh(j, "subtitles"):
                return Gate(j, st, "captions needed", f"faceless subtitles {j}")
            return Gate(j, st, "render needed", f"faceless render {j}")
        if st == JobState.RENDERED:
            return Gate(j, st, "final video awaits human approval",
                        f"faceless review {j}; faceless approve-final {j}")
        if st == JobState.FINAL_APPROVED:
            return Gate(j, st, "export needed", f"faceless export {j}")
        return Gate(j, st, "exported; publish manually", "", done=True)

    def advance(self, job_id: str, on_step: Callable[[str], None] | None = None,
                stop_before_render: bool = False) -> Gate:
        """Run automatic (non-review) stages until a human gate. Never approves anything."""
        def say(msg: str) -> None:
            if on_step:
                on_step(msg)
        for _ in range(10):
            gate = self.next_gate(job_id)
            cmd = gate.next_command
            if gate.done:
                return gate
            if cmd.startswith(f"faceless voice {job_id}"):
                say("voice")
                self.voice(job_id)
            elif cmd.startswith(f"faceless assets {job_id}"):
                say("assets")
                self.assets(job_id)
            elif cmd.startswith(f"faceless subtitles {job_id}"):
                say("subtitles")
                self.subtitles(job_id)
            elif cmd.startswith(f"faceless render {job_id}"):
                if stop_before_render:
                    return gate
                say("render")
                self.render(job_id)
            elif cmd.startswith(f"faceless export {job_id}"):
                say("export")
                self.export(job_id)
            else:
                return gate
        return self.next_gate(job_id)


def find_job(studio: Studio, job_id: str) -> dict:
    try:
        return studio.get_job(job_id)
    except NotFoundError:
        rows = [r for r in studio.list_jobs() if r["id"].startswith(job_id)]
        if len(rows) == 1:
            return rows[0]
        raise


def copy_into_job(src: Path, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(user_file(src), dest)
    return dest
