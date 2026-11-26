"""Job identity, directories, stage-run records and approvals."""

from __future__ import annotations

import getpass
import json
import logging
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cached_property
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .config import Settings, get_settings
from .db import Database, loads
from .schemas import JobInput, JobState
from .util import (
    FacelessError,
    NotFoundError,
    ValidationFailed,
    hash_files,
    read_json,
    utcnow,
    write_json,
)

log = logging.getLogger("faceless")

JOB_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")


def _slug(text: str, n: int = 24) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:n].rstrip("-") or "job"


def new_job_id(topic: str, day: int | None = None) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    prefix = f"d{day:03d}-" if day is not None else ""
    return f"{prefix}{stamp}-{_slug(topic)}-{secrets.token_hex(2)}"


def validate_job_id(job_id: str) -> str:
    if not JOB_ID_RE.match(job_id):
        raise FacelessError(f"invalid job id: {job_id!r}")
    return job_id


@dataclass(frozen=True)
class JobPaths:
    root: Path

    @property
    def job_json(self) -> Path:
        return self.root / "job.json"

    @property
    def research(self) -> Path:
        return self.root / "research.json"

    @property
    def script(self) -> Path:
        return self.root / "script.json"

    @property
    def drafts(self) -> Path:
        return self.root / "drafts"

    @property
    def review_dir(self) -> Path:
        return self.root / "review"

    @property
    def voice_dir(self) -> Path:
        return self.root / "voice"

    @property
    def narration(self) -> Path:
        return self.voice_dir / "narration.wav"

    @property
    def timing(self) -> Path:
        return self.voice_dir / "timing.json"

    @property
    def assets_dir(self) -> Path:
        return self.root / "assets"

    @property
    def manifest(self) -> Path:
        return self.assets_dir / "manifest.json"

    @property
    def captions_dir(self) -> Path:
        return self.root / "captions"

    @property
    def captions_json(self) -> Path:
        return self.captions_dir / "captions.json"

    @property
    def srt(self) -> Path:
        return self.captions_dir / "captions.srt"

    @property
    def ass(self) -> Path:
        return self.captions_dir / "captions.ass"

    @property
    def words(self) -> Path:
        return self.captions_dir / "words.json"

    @property
    def render_dir(self) -> Path:
        return self.root / "render"

    @property
    def final(self) -> Path:
        return self.render_dir / "final.mp4"

    @property
    def preview(self) -> Path:
        return self.render_dir / "preview.mp4"

    @property
    def validation(self) -> Path:
        return self.render_dir / "validation.json"

    @property
    def render_log(self) -> Path:
        return self.render_dir / "ffmpeg.log"

    @property
    def export_dir(self) -> Path:
        return self.root / "export"

    @property
    def logs(self) -> Path:
        return self.root / "logs"


class Studio:
    """Entry point holding settings and the database."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        for d in (
            self.settings.jobs_dir,
            self.settings.library_dir,
            self.settings.cache_dir,
            self.settings.locks_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)

    @cached_property
    def db(self) -> Database:
        return Database(self.settings.db_path)

    # ------------------------------------------------------------------ jobs
    def paths(self, job_id: str) -> JobPaths:
        validate_job_id(job_id)
        return JobPaths(self.settings.jobs_dir / job_id)

    def create_job(
        self,
        topic: str,
        day: int | None = None,
        date: str | None = None,
        calendar_ref: dict | None = None,
        notes: str = "",
    ) -> JobInput:
        topic = " ".join(topic.split())
        if day is not None:
            existing = self.db.one("SELECT id FROM jobs WHERE day = ?", (day,))
            if existing:
                raise FacelessError(
                    f"calendar day {day} already has job {existing['id']}",
                    hint=f"use 'faceless status {existing['id']}' or 'faceless resume'",
                )
        job_id = new_job_id(topic, day)
        now = utcnow()
        try:
            job = JobInput(
                id=job_id, topic=topic, day=day, date=date, calendar_ref=calendar_ref,
                notes=notes, created=now,
            )
        except ValidationError as e:
            raise ValidationFailed(f"invalid job: {e.errors()[0]['loc'][0]}: "
                                   f"{e.errors()[0]['msg']}") from e
        p = self.paths(job_id)
        p.root.mkdir(parents=True, exist_ok=False)
        write_json(p.job_json, job.model_dump())
        self.db.execute(
            "INSERT INTO jobs(id, topic, day, date, state, created, updated) "
            "VALUES (?,?,?,?,?,?,?)",
            (job_id, topic, day, date, JobState.DRAFT.value, now, now),
        )
        self.db.execute(
            "INSERT INTO transitions(job_id, ts, from_state, to_state, note) VALUES (?,?,?,?,?)",
            (job_id, now, None, JobState.DRAFT.value, "created"),
        )
        log.info("created job %s", job_id)
        return job

    def get_job(self, job_id: str) -> dict[str, Any]:
        validate_job_id(job_id)
        row = self.db.one("SELECT * FROM jobs WHERE id = ?", (job_id,))
        if not row:
            raise NotFoundError(f"no such job: {job_id}", hint="list jobs with 'faceless list'")
        return row

    def job_input(self, job_id: str) -> JobInput:
        self.get_job(job_id)
        return JobInput.model_validate(read_json(self.paths(job_id).job_json))

    def list_jobs(self, state: str | None = None) -> list[dict[str, Any]]:
        if state:
            return self.db.all("SELECT * FROM jobs WHERE state = ? ORDER BY created", (state,))
        return self.db.all("SELECT * FROM jobs ORDER BY COALESCE(day, 1e9), created")

    def set_state(
        self,
        job_id: str,
        state: JobState,
        note: str = "",
        failed_stage: str | None = None,
        error: str | None = None,
    ) -> bool:
        row = self.get_job(job_id)
        changed = row["state"] != state.value
        now = utcnow()
        self.db.execute(
            "UPDATE jobs SET state=?, failed_stage=?, error=?, updated=? WHERE id=?",
            (state.value, failed_stage, error, now, job_id),
        )
        if changed:
            self.db.execute(
                "INSERT INTO transitions(job_id, ts, from_state, to_state, note) "
                "VALUES (?,?,?,?,?)",
                (job_id, now, row["state"], state.value, note),
            )
        return changed

    def transitions(self, job_id: str) -> list[dict[str, Any]]:
        return self.db.all("SELECT * FROM transitions WHERE job_id=? ORDER BY id", (job_id,))

    # ------------------------------------------------------------ stage runs
    def record_stage(
        self,
        job_id: str,
        stage: str,
        status: str,
        started: str,
        duration_s: float,
        input_hash: str | None = None,
        output_hash: str | None = None,
        metrics: dict[str, Any] | None = None,
        fallbacks: list[str] | None = None,
        details: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> int:
        m = metrics or {}
        return self.db.execute(
            "INSERT INTO stage_runs(job_id, stage, status, input_hash, output_hash, started, "
            "duration_s, device, peak_rss_mib, peak_children_rss_mib, peak_vram_used_mib, "
            "fallbacks, details, error) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                job_id, stage, status, input_hash, output_hash, started, duration_s,
                m.get("device"), m.get("peak_rss_mib"), m.get("peak_children_rss_mib"),
                m.get("peak_vram_used_mib"), json.dumps(fallbacks or []),
                json.dumps(details or {}, default=str), error,
            ),
        )

    def last_run(self, job_id: str, stage: str, status: str | None = None) -> dict | None:
        if status:
            row = self.db.one(
                "SELECT * FROM stage_runs WHERE job_id=? AND stage=? AND status=? "
                "ORDER BY id DESC LIMIT 1",
                (job_id, stage, status),
            )
        else:
            row = self.db.one(
                "SELECT * FROM stage_runs WHERE job_id=? AND stage=? ORDER BY id DESC LIMIT 1",
                (job_id, stage),
            )
        if row:
            row["fallbacks"] = loads(row["fallbacks"], [])
            row["details"] = loads(row["details"], {})
        return row

    def stage_runs(self, job_id: str) -> list[dict]:
        rows = self.db.all("SELECT * FROM stage_runs WHERE job_id=? ORDER BY id", (job_id,))
        for r in rows:
            r["fallbacks"] = loads(r["fallbacks"], [])
            r["details"] = loads(r["details"], {})
        return rows

    # -------------------------------------------------------------- approvals
    def approve(
        self,
        job_id: str,
        kind: str,
        artifact_hash: str,
        reviewer: str | None = None,
        notes: str = "",
        checklist: dict | None = None,
    ) -> None:
        self.db.execute(
            "INSERT INTO approvals(job_id, kind, artifact_hash, reviewer, ts, notes, checklist) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                job_id, kind, artifact_hash, reviewer or default_reviewer(), utcnow(), notes,
                json.dumps(checklist or {}),
            ),
        )

    def latest_approval(self, job_id: str, kind: str) -> dict | None:
        row = self.db.one(
            "SELECT * FROM approvals WHERE job_id=? AND kind=? ORDER BY id DESC LIMIT 1",
            (job_id, kind),
        )
        if row:
            row["checklist"] = loads(row["checklist"], {})
        return row

    def approvals(self, job_id: str) -> list[dict]:
        rows = self.db.all("SELECT * FROM approvals WHERE job_id=? ORDER BY id", (job_id,))
        for r in rows:
            r["checklist"] = loads(r["checklist"], {})
        return rows


def default_reviewer() -> str:
    try:
        return getpass.getuser()
    except Exception:  # pragma: no cover - containers without passwd entry
        return "reviewer"


def files_hash(*paths: Path) -> str | None:
    """Hash of existing files, or None if any is missing."""
    if not all(p.is_file() for p in paths):
        return None
    return hash_files(list(paths))
