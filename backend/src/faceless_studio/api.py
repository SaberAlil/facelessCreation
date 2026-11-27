"""Local dashboard API (FastAPI). Run: ``uvicorn faceless_studio.api:app --host 127.0.0.1``.

Security model (single local user, no accounts):
- Bind to loopback only (the default). Requests whose Host header is not localhost are
  rejected (DNS-rebinding guard).
- Mutating requests need the ``X-Faceless: 1`` header. Browsers cannot add it cross-site
  without a CORS preflight, and CORS only allows the local Angular dev origin.
- Files are served only from a fixed allow-list of job artifacts.
- One background worker executes long stages, so GPU work and renders stay serial.
"""

from __future__ import annotations

import logging
import queue
import threading
import traceback
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, File, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, ValidationError

from . import __version__
from .config import Settings, get_settings
from .jobs import Studio
from .pipeline import Pipeline
from .schemas import Caption, Research, RightsStatus, Script
from .subtitles import to_srt
from .util import FacelessError, GateError, NotFoundError, utcnow, write_json

log = logging.getLogger("faceless")

ALLOWED_HOSTS = {"localhost", "127.0.0.1", "[::1]", "testserver"}
ALLOWED_ORIGINS = ["http://localhost:4200", "http://127.0.0.1:4200"]


# ------------------------------------------------------------------ worker


class Task(BaseModel):
    id: str
    job_id: str | None
    action: str
    status: Literal["queued", "running", "done", "failed"] = "queued"
    created: str
    started: str | None = None
    finished: str | None = None
    result: Any = None
    error: str | None = None
    hint: str | None = None
    progress: dict | None = None  # {"message", "done", "total"} while running


class Worker:
    """Single background thread: one long-running stage at a time."""

    def __init__(self, pipeline: Pipeline | None = None) -> None:
        self.pipeline = pipeline
        self.q: queue.Queue[tuple[Task, Callable[[], Any]]] = queue.Queue()
        self.tasks: dict[str, Task] = {}
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="faceless-worker")
        self._thread.start()

    def submit(self, job_id: str | None, action: str, fn: Callable[[], Any]) -> Task:
        with self._lock:
            busy = [t for t in self.tasks.values() if t.job_id == job_id and job_id and
                    t.status in ("queued", "running")]
            if busy:
                raise GateError(f"job {job_id} already has a {busy[0].action} task "
                                f"({busy[0].status})")
            t = Task(id=uuid.uuid4().hex[:12], job_id=job_id, action=action, created=utcnow())
            self.tasks[t.id] = t
        self.q.put((t, fn))
        return t

    def _loop(self) -> None:
        while True:
            task, fn = self.q.get()
            task.status, task.started = "running", utcnow()
            if self.pipeline is not None:
                def report(message: str, done: int, total: int, _t: Task = task) -> None:
                    _t.progress = {"message": message, "done": done, "total": total}
                self.pipeline.progress = report
            try:
                res = fn()
                task.result = _jsonable(res)
                task.status = "done"
            except FacelessError as e:
                task.status, task.error, task.hint = "failed", str(e), e.hint
            except Exception as e:  # keep the worker alive
                log.error("task %s crashed: %s", task.id, traceback.format_exc())
                task.status, task.error = "failed", f"{type(e).__name__}: {e}"
            task.finished = utcnow()
            task.progress = None
            if self.pipeline is not None:
                self.pipeline.progress = None

    def wait_idle(self, timeout: float = 600) -> None:
        """Test helper: block until the queue is drained."""
        import time
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.q.empty() and all(t.status not in ("queued", "running")
                                      for t in self.tasks.values()):
                return
            time.sleep(0.05)


def _jsonable(obj: Any) -> Any:
    if hasattr(obj, "details") and hasattr(obj, "stage"):
        return {"stage": obj.stage, "details": obj.details, "metrics": obj.metrics,
                "fallbacks": obj.fallbacks}
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if hasattr(obj, "__dataclass_fields__"):
        from dataclasses import asdict
        return {k: (v.value if hasattr(v, "value") else v) for k, v in asdict(obj).items()}
    return obj


# ------------------------------------------------------------ request bodies


class NewJob(BaseModel):
    topic: str | None = None
    day: int | None = None
    date: str | None = None
    subtitles: str | None = None


class DraftBody(BaseModel):
    llm: bool = True
    text: str | None = Field(default=None, max_length=20000)
    title: str | None = None


class Approval(BaseModel):
    """One click in the dashboard. The reviewer defaults to the name set in Settings."""
    reviewer: str = Field(default="", max_length=80)
    notes: str = Field(default="", max_length=2000)
    acknowledge_flags: bool = False
    continue_after: bool = True  # run the following automatic steps straight away


class OptionsBody(BaseModel):
    subtitles: str | None = None
    voice: str | None = None


class PictureAction(BaseModel):
    action: Literal["photo", "ai"]


class SettingsBody(BaseModel):
    reviewer: str | None = Field(default=None, max_length=80)
    default_subtitles: str | None = None
    default_voice: str | None = None


class RightsBody(BaseModel):
    scene_ids: list[str] | None = None
    status: RightsStatus


class ReplaceBody(BaseModel):
    scene_id: str
    library_id: str | None = None
    procedural: bool = False
    overlay: str | None = Field(default=None, max_length=80)


class CaptionEdit(BaseModel):
    captions: list[Caption] = Field(max_length=400)


# --------------------------------------------------------------------- app


def create_app(settings: Settings | None = None) -> FastAPI:
    s = settings or get_settings()
    pl = Pipeline(Studio(s))
    worker = Worker(pl)
    app = FastAPI(title="Faceless Studio", version=__version__, docs_url="/api/docs",
                  openapi_url="/api/openapi.json")
    app.state.pipeline = pl
    app.state.worker = worker
    app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS, allow_credentials=False,
                       allow_methods=["GET", "POST", "PUT", "DELETE"],
                       allow_headers=["Content-Type", "X-Faceless"])

    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = (request.headers.get("host") or "").rsplit(":", 1)[0]
        if host not in ALLOWED_HOSTS:
            return JSONResponse({"detail": "host not allowed"}, status_code=403)
        if request.method in ("POST", "PUT", "DELETE") and \
                request.headers.get("x-faceless") != "1":
            return JSONResponse({"detail": "missing X-Faceless header"}, status_code=403)
        return await call_next(request)

    @app.exception_handler(FacelessError)
    async def faceless_error(_req: Request, e: FacelessError):
        code = 404 if isinstance(e, NotFoundError) else 409 if isinstance(e, GateError) else 422
        return JSONResponse({"detail": str(e), "hint": e.hint}, status_code=code)

    @app.exception_handler(ValidationError)
    async def validation_error(_req: Request, e: ValidationError):
        return JSONResponse({"detail": str(e)}, status_code=422)

    def job(job_id: str) -> str:
        return pl.studio.get_job(job_id)["id"]

    # ------------------------------------------------------------ system
    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True, "version": __version__}

    @app.get("/api/system")
    def system() -> dict:
        from .resources import meminfo_mib, query_gpu
        g = query_gpu()
        lock = s.locks_dir / "gpu.lock"
        return {"gpu": g.__dict__ if g else None, "ram": meminfo_mib(),
                "gpu_lock": lock.read_text().strip() if lock.is_file() else "",
                "encoder_setting": s.render.encoder, "tts": s.tts.provider,
                "llm": {"host": s.llm.host, "model": s.llm.model},
                "queue": [t.model_dump() for t in worker.tasks.values()
                          if t.status in ("queued", "running")]}

    @app.get("/api/doctor")
    def doctor(network: bool = False) -> list[dict]:
        from .doctor import run_checks
        return [c.__dict__ for c in run_checks(s, probe_encoders=True, network=network)]

    @app.get("/api/tasks")
    def tasks(job_id: str | None = None) -> list[Task]:
        items = sorted(worker.tasks.values(), key=lambda t: t.created, reverse=True)
        return [t for t in items if job_id is None or t.job_id == job_id][:50]

    @app.get("/api/tasks/{task_id}")
    def task(task_id: str) -> Task:
        if task_id not in worker.tasks:
            raise NotFoundError("no such task")
        return worker.tasks[task_id]

    # ---------------------------------------------------------- calendar
    @app.get("/api/calendar")
    def calendar(from_day: int = 1, to_day: int = 400) -> list[dict]:
        from .calendar import calendar_range
        jobs = {j["day"]: j for j in pl.studio.list_jobs() if j["day"] is not None}
        rows = calendar_range(pl.studio.db, day_from=from_day, day_to=to_day)
        for r in rows:
            j = jobs.get(r["day"])
            r["job"] = {"id": j["id"], "state": j["state"]} if j else None
        return rows

    @app.post("/api/calendar/import")
    async def calendar_import(file: UploadFile = File(...), replace: bool = False,
                              sheet: str | None = None) -> dict:
        from .calendar import parse_calendar, store_calendar
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in (".csv", ".xlsx", ".xlsm", ".tsv"):
            raise FacelessError("upload a .csv or .xlsx file")
        data = await file.read(20 * 1024 * 1024 + 1)
        if len(data) > 20 * 1024 * 1024:
            raise FacelessError("file larger than 20 MB")
        dest = s.content_dir / "uploads" / f"calendar-upload{suffix}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        imp = parse_calendar(dest, sheet)
        n = store_calendar(pl.studio.db, imp, replace)
        return {"rows": n, "mapping": imp.mapping, "sheet": imp.sheet}

    # -------------------------------------------------------------- jobs

    @app.get("/api/jobs")
    def jobs() -> list[dict]:
        return pl.studio.list_jobs()

    @app.post("/api/jobs", status_code=201)
    def new_job(body: NewJob) -> dict:
        from .calendar import calendar_entry
        if body.day is not None or body.date:
            row = calendar_entry(pl.studio.db, body.day, body.date)
            j = pl.studio.create_from_calendar(row)
        elif body.topic:
            j = pl.studio.create_job(body.topic)
        else:
            raise FacelessError("give topic, day or date")
        return j.model_dump()

    @app.post("/api/make", status_code=202)
    def make(body: NewJob) -> Task:
        """Create the job if needed, then run every automatic stage until an approval."""
        from .calendar import calendar_entry
        if body.day is not None or body.date:
            row = calendar_entry(pl.studio.db, body.day, body.date)
            existing = pl.studio.db.one("SELECT id FROM jobs WHERE day = ?", (row["day"],))
            jid = existing["id"] if existing else pl.studio.create_from_calendar(row).id
        elif body.topic:
            jid = pl.studio.create_job(body.topic).id
        else:
            raise FacelessError("give topic, day or date")
        if body.subtitles:
            pl.set_job_options(jid, body.subtitles)
        return worker.submit(jid, "make", lambda: pl.advance(jid))

    @app.get("/api/jobs/{job_id}")
    def job_detail(job_id: str = Depends(job)) -> dict:
        pl.refresh(job_id)
        g = pl.next_gate(job_id)
        sc = pl.script(job_id)
        p = pl.paths(job_id)
        return {
            "job": pl.studio.get_job(job_id), "title": sc.title if sc else None,
            "gate": {"state": g.state.value, "reason": g.reason, "next": g.next_command,
                     "done": g.done},
            "fresh": {k: pl.stage_fresh(job_id, k) for k in ("voice", "assets", "subtitles",
                                                              "render", "export")},
            "approvals": {k: pl.approval_valid(job_id, k) for k in ("script", "assets",
                                                                      "final")},
            "runs": pl.studio.stage_runs(job_id)[-30:],
            "transitions": pl.studio.transitions(job_id)[-30:],
            "files": {k: (p.root / v).is_file() for k, v in FILES.items()},
            "timing": pl.timing(job_id).model_dump() if pl.timing(job_id) else None,
            "options": pl.job_options(job_id),
            "steps": job_steps(job_id),
            "task": next((t.model_dump() for t in sorted(worker.tasks.values(),
                                                          key=lambda t: t.created,
                                                          reverse=True)
                          if t.job_id == job_id), None),
        }

    def job_steps(job_id: str) -> list[dict]:
        """The four steps shown in the dashboard, in plain language."""
        st = pl.effective_state(job_id)
        order = ["DRAFT", "RESEARCH_REVIEW", "SCRIPT_APPROVED", "ASSETS_READY", "RENDERED",
                 "FINAL_APPROVED", "EXPORTED"]
        k = order.index(st.value)
        assets_ready = pl.stage_fresh(job_id, "voice") and pl.stage_fresh(job_id, "assets")

        def step(key: str, title: str, status: str, hint: str) -> dict:
            return {"key": key, "title": title, "status": status, "hint": hint}

        script = step("script", "Script",
                      "done" if k >= 2 else "review" if k == 1 else "todo",
                      "Check the facts and the length, then approve." if k == 1 else
                      "The script is written from your sources." if k < 1 else "Approved")
        pics = step("pictures", "Pictures",
                    "done" if k >= 3 else "review" if k == 2 and assets_ready else
                    "todo" if k < 2 else "working",
                    "Replace any picture that does not fit, then approve." if k == 2 and
                    assets_ready else "Approved" if k >= 3 else "Comes after the script")
        video = step("video", "Video",
                     "done" if k >= 5 else "review" if k == 4 else
                     "working" if k == 3 else "todo",
                     "Watch it and fix the subtitles if needed, then approve." if k == 4
                     else "Approved" if k >= 5 else "Comes after the pictures")
        publish = step("publish", "Publish", "done" if k >= 6 else
                       "working" if k == 5 else "todo",
                       "Download the video and copy the post texts." if k >= 6 else
                       "Comes after the video")
        return [script, pics, video, publish]

    @app.get("/api/jobs/{job_id}/options")
    def get_options(job_id: str = Depends(job)) -> dict:
        from .pipeline import LANGUAGES
        from .providers.tts import VOICES
        return {"options": pl.job_options(job_id), "languages": LANGUAGES, "voices": VOICES}

    @app.put("/api/jobs/{job_id}/options")
    def put_options(body: OptionsBody, job_id: str = Depends(job)) -> dict:
        from .pipeline import LANGUAGES
        from .providers.tts import VOICES
        return {"options": pl.set_job_options(job_id, body.subtitles, body.voice),
                "languages": LANGUAGES, "voices": VOICES}

    # ---------------------------------------------------------- settings
    @app.get("/api/settings")
    def get_app_settings() -> dict:
        from .pipeline import LANGUAGES
        from .providers.tts import VOICES
        return {"reviewer": s.reviewer, "default_subtitles": s.subtitles.burn_language,
                "default_voice": s.tts.voice, "voices": VOICES,
                "languages": LANGUAGES, "max_duration_s": s.render.max_duration_s,
                "min_duration_s": s.render.min_duration_s,
                "picture_sources": s.visuals.order}

    @app.put("/api/settings")
    def put_app_settings(body: SettingsBody) -> dict:
        import yaml

        from .pipeline import LANGUAGES
        local = s.project_root / "config" / "local.yaml"
        data = yaml.safe_load(local.read_text()) if local.is_file() else {}
        data = data or {}
        if body.reviewer is not None:
            s.reviewer = data["reviewer"] = body.reviewer.strip()
        if body.default_subtitles is not None:
            if body.default_subtitles not in LANGUAGES:
                raise FacelessError(f"unknown language {body.default_subtitles}")
            s.subtitles.burn_language = body.default_subtitles
            data.setdefault("subtitles", {})["burn_language"] = body.default_subtitles
        if body.default_voice is not None:
            from .providers.tts import VOICES
            if body.default_voice not in VOICES:
                raise FacelessError(f"unknown voice {body.default_voice}")
            # Only new videos use it: each video stores its own voice.
            s.tts.voice = data.setdefault("tts", {})["voice"] = body.default_voice
            s.tts.lang_code = data["tts"]["lang_code"] = body.default_voice[0]
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_text(yaml.safe_dump(data, sort_keys=False))
        return get_app_settings()

    @app.get("/api/voices/{voice}/sample")
    def voice_sample(voice: str) -> FileResponse:
        """A short spoken sample of a voice (made once, then cached)."""
        from .providers.tts import VOICES, KokoroTTS
        if voice not in VOICES:
            raise NotFoundError(f"unknown voice {voice}")
        out = s.cache_dir / "voice-samples" / f"{voice}.wav"
        if not out.is_file():
            tts = KokoroTTS(s.tts.model_copy(update={"voice": voice, "lang_code": voice[0]}))
            tts.synthesize("Here is a quick fact you probably did not know. This is how I "
                           "would narrate your next video.", out)
        return FileResponse(out, media_type="audio/wav")

    # ---------------------------------------------------------- overview
    @app.get("/api/overview")
    def overview() -> dict:
        from datetime import date

        import httpx
        today = date.today().isoformat()
        upcoming = pl.studio.db.all(
            "SELECT day, date, topic FROM calendar WHERE date >= ? ORDER BY date LIMIT 7",
            (today,))
        jobs_by_day = {j["day"]: j for j in pl.studio.list_jobs() if j["day"] is not None}
        for r in upcoming:
            j = jobs_by_day.get(r["day"])
            r["job"] = {"id": j["id"], "state": j["state"]} if j else None
        running = {t.job_id: t.model_dump() for t in worker.tasks.values()
                   if t.status in ("queued", "running")}
        review, progress = [], []
        for j in pl.studio.list_jobs():
            steps = job_steps(j["id"])
            cur = next((x for x in steps if x["status"] != "done"), None)
            item = {"id": j["id"], "topic": j["topic"], "day": j["day"], "date": j["date"],
                    "step": cur["title"] if cur else "Published",
                    "task": running.get(j["id"])}
            if j["id"] in running:
                progress.append(item)
            elif cur and cur["status"] == "review":
                review.append(item)
        try:
            images_ok = httpx.get(f"{s.comfyui.host}/system_stats", timeout=2).is_success
        except httpx.HTTPError:
            images_ok = False
        return {"today": today, "upcoming": upcoming, "review": review,
                "in_progress": progress, "ai_images": images_ok,
                "reviewer_set": bool(s.reviewer)}

    def _queue(job_id: str, action: str, fn: Callable[[], Any]) -> Task:
        return worker.submit(job_id, action, fn)


    @app.post("/api/jobs/{job_id}/draft", status_code=202)
    def draft(body: DraftBody, job_id: str = Depends(job)) -> Task:
        if body.text:
            return _queue(job_id, "draft", lambda: pl.draft(job_id, pasted_text=body.text,
                                                            title=body.title))
        return _queue(job_id, "draft", lambda: pl.draft(job_id, use_llm=True))

    STAGES: dict[str, Callable[[str], Any]] = {
        "voice": lambda j: pl.voice(j),
        "assets": lambda j: pl.assets(j),
        "subtitles": lambda j: pl.subtitles(j),
        "subtitles-approximate": lambda j: pl.subtitles(j, approximate=True),
        "preview": lambda j: pl.render(j, preview=True),
        "render": lambda j: pl.render(j),
        "export": lambda j: pl.export(j),
        "resume": lambda j: pl.advance(j),
        # Re-record the narration (speeds it up by at most 8% if it is slightly over the
        # limit), then redo subtitles and video. Pictures and approvals are kept.
        "fit": lambda j: (pl.voice(j), pl.advance(j))[-1],
    }

    @app.post("/api/jobs/{job_id}/run/{stage}", status_code=202)
    def run_stage(stage: str, job_id: str = Depends(job)) -> Task:
        if stage not in STAGES:
            raise NotFoundError(f"unknown stage {stage}; one of {sorted(STAGES)}")
        return _queue(job_id, stage, lambda: STAGES[stage](job_id))

    # ------------------------------------------------- script & research
    @app.get("/api/jobs/{job_id}/script")
    def get_script(job_id: str = Depends(job)) -> dict:
        rv = pl.review_script(job_id)
        rep = rv["report"]
        return {"script": rv["script"].model_dump(mode="json"), "approved": rv["approved"],
                "report": {"words": rep.words, "est_seconds": rep.est_seconds,
                           "errors": rep.errors, "warnings": rep.warnings,
                           "flagged_claims": rep.flagged_claims},
                "research_problems": rv["research_problems"]}

    @app.put("/api/jobs/{job_id}/script")
    def put_script(script: Script, job_id: str = Depends(job)) -> dict:
        p = pl.paths(job_id)
        script.provider = script.provider if script.provider.startswith("manual") else \
            f"{script.provider}+edited"
        write_json(p.script, script.model_dump(mode="json"))
        pl.studio.record_stage(job_id, "draft", "ok", utcnow(), 0.0, None,
                               pl.script_hash(job_id), {"device": "none"}, [],
                               {"manual": "edited in dashboard"})
        pl.refresh(job_id, "script edited")
        return get_script(job_id)

    @app.get("/api/jobs/{job_id}/research")
    def get_research(job_id: str = Depends(job)) -> dict:
        from .research import research_problems
        r = pl.research(job_id)
        return {"research": r.model_dump(mode="json"), "problems": research_problems(r)}

    @app.put("/api/jobs/{job_id}/research")
    def put_research(research: Research, job_id: str = Depends(job)) -> dict:
        from .research import save_research
        save_research(pl.paths(job_id).research, research)
        pl.refresh(job_id, "research edited")
        return get_research(job_id)


    def _reviewer(body: Approval) -> str:
        return (body.reviewer or s.reviewer or "reviewer").strip()

    def _continue(job_id: str, body: Approval) -> dict | None:
        """After an approval, run the following automatic steps right away."""
        if not body.continue_after:
            return None
        try:
            return worker.submit(job_id, "continue", lambda: pl.advance(job_id)).model_dump()
        except GateError:
            return None  # something is already running for this job

    @app.post("/api/jobs/{job_id}/approve-script")
    def approve_script(body: Approval, job_id: str = Depends(job)) -> dict:
        h = pl.approve_script(job_id, _reviewer(body), body.acknowledge_flags, body.notes)
        return {"approved": True, "hash": h, "task": _continue(job_id, body)}

    # ------------------------------------------------------------ assets
    @app.get("/api/jobs/{job_id}/assets")
    def assets(job_id: str = Depends(job)) -> dict:
        rv = pl.asset_review(job_id)
        # Approving in the dashboard confirms the licences shown, so "unreviewed" is not a
        # blocking problem here; missing pictures and unusable licences are.
        problems = [x for x in rv["problems"] if not x.endswith("rights unreviewed")]
        return {"manifest": rv["manifest"].model_dump(mode="json"),
                "problems": problems, "fresh": rv["fresh"], "approved": rv["approved"]}


    @app.post("/api/jobs/{job_id}/assets/rights")
    def rights(body: RightsBody, job_id: str = Depends(job)) -> dict:
        pl.set_rights(job_id, body.scene_ids, body.status)
        return assets(job_id)


    @app.post("/api/jobs/{job_id}/assets/replace")
    def replace(body: ReplaceBody, job_id: str = Depends(job)) -> dict:
        pl.replace_asset(job_id, body.scene_id, body.library_id, body.procedural, body.overlay)
        return assets(job_id)

    @app.post("/api/jobs/{job_id}/approve-assets")
    def approve_assets(body: Approval, job_id: str = Depends(job)) -> dict:
        """Approving the pictures confirms their licences (shown next to each one)."""
        m = pl.manifest(job_id)
        unreviewed = [a.scene_id for a in (m.scenes if m else [])
                      if a.kind != "missing" and a.rights != RightsStatus.CONFIRMED]
        if unreviewed:
            pl.set_rights(job_id, unreviewed, RightsStatus.CONFIRMED)
        h = pl.approve_assets(job_id, _reviewer(body), body.notes)
        return {"approved": True, "hash": h, "task": _continue(job_id, body)}

    @app.post("/api/jobs/{job_id}/scenes/{scene_id}/picture", status_code=202)
    def scene_picture(scene_id: str, body: PictureAction, job_id: str = Depends(job)) -> Task:
        return _queue(job_id, f"picture-{body.action}",
                      lambda: pl.scene_picture(job_id, scene_id, body.action))

    @app.post("/api/jobs/{job_id}/scenes/{scene_id}/upload")
    async def scene_upload(scene_id: str, file: UploadFile = File(...),
                           job_id: str = Depends(job)) -> dict:
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in (".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mov", ".webm"):
            raise FacelessError("upload a photo (.jpg, .png, .webp) or a video clip (.mp4)")
        data = await file.read(300 * 1024 * 1024 + 1)
        if len(data) > 300 * 1024 * 1024:
            raise FacelessError("file larger than 300 MB")
        from .util import safe_filename
        dest = s.cache_dir / "uploads" / safe_filename(f"{job_id}-{scene_id}-{file.filename}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        pl.scene_picture(job_id, scene_id, "upload", upload=dest)
        return assets(job_id)

    @app.get("/api/library")
    def library_list(kind: str | None = None) -> list[dict]:
        from .library import list_assets
        return [a.model_dump(mode="json") for a in list_assets(pl.studio.db, kind)]

    # ---------------------------------------------------------- captions
    @app.get("/api/jobs/{job_id}/captions")
    def captions(job_id: str = Depends(job)) -> dict:
        tr = pl.captions(job_id)
        return {"track": tr.model_dump() if tr else None,
                "fresh": pl.stage_fresh(job_id, "subtitles")}


    @app.put("/api/jobs/{job_id}/captions")
    def put_captions(body: CaptionEdit, job_id: str = Depends(job)) -> dict:
        tmp = pl.paths(job_id).captions_dir / ".dashboard-edit.srt"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(to_srt(body.captions), encoding="utf-8")
        try:
            pl.import_captions(job_id, tmp)
        finally:
            tmp.unlink(missing_ok=True)
        return captions(job_id)

    @app.post("/api/jobs/{job_id}/captions/mark-reviewed")
    def captions_reviewed(job_id: str = Depends(job)) -> dict:
        pl.mark_captions_reviewed(job_id)
        return captions(job_id)

    # ------------------------------------------------------ review/final
    @app.get("/api/jobs/{job_id}/review")
    def review(job_id: str = Depends(job)) -> dict:
        rv = pl.review(job_id)
        return {"title": rv["title"], "checklist": rv["checklist"], "approved": rv["approved"]}

    @app.post("/api/jobs/{job_id}/approve-final")
    def approve_final(body: Approval, job_id: str = Depends(job)) -> dict:
        h = pl.approve_final(job_id, _reviewer(body), body.notes)
        return {"approved": True, "hash": h, "task": _continue(job_id, body)}

    @app.get("/api/jobs/{job_id}/export")
    def export_files(job_id: str = Depends(job)) -> dict:
        d = pl.paths(job_id).export_dir
        files = sorted(f for f in d.iterdir() if f.is_file()) if d.is_dir() else []
        texts = {f.stem: f.read_text(encoding="utf-8") for f in files
                 if f.name in ("youtube.txt", "tiktok.txt", "instagram.txt")}
        return {"files": [{"name": f.name, "bytes": f.stat().st_size} for f in files],
                "texts": texts, "folder": str(d)}

    @app.get("/api/jobs/{job_id}/export/{name}")
    def export_file(name: str, job_id: str = Depends(job)) -> FileResponse:
        d = pl.paths(job_id).export_dir
        allowed = {f.name: f for f in d.iterdir() if f.is_file()} if d.is_dir() else {}
        if name not in allowed:
            raise NotFoundError("no such export file")
        return FileResponse(allowed[name], filename=name)

    @app.get("/api/jobs/{job_id}/files/{name}")
    def file(name: str, job_id: str = Depends(job)) -> FileResponse:
        p = pl.paths(job_id)
        if name in FILES:
            path = p.root / FILES[name]
        elif name.startswith("scene-"):
            # preview image of a scene's visual (procedural PNG or library still)
            m = pl.manifest(job_id)
            sid = name[len("scene-"):]
            a = next((x for x in (m.scenes if m else []) if x.scene_id == sid), None)
            if a is None or a.kind == "video":
                raise NotFoundError("no still for that scene")
            from .assets import asset_file
            path = asset_file(p, a)
        else:
            raise NotFoundError("unknown file")
        if not path.is_file():
            raise NotFoundError(f"{name} does not exist yet")
        return FileResponse(path)

    return app


FILES = {
    "final.mp4": "render/final.mp4",
    "preview.mp4": "render/preview.mp4",
    "cover.png": "render/cover.png",
    "cover.preview.png": "render/cover.preview.png",
    "captions.srt": "captions/captions.srt",
    "narration.wav": "voice/narration.wav",
    "script_review.md": "review/script_review.md",
    "validation.json": "render/validation.json",
}


def _lazy_app() -> FastAPI:
    return create_app()


def __getattr__(name: str) -> Any:  # ``uvicorn faceless_studio.api:app``
    if name == "app":
        global app
        app = _lazy_app()
        return app
    raise AttributeError(name)

