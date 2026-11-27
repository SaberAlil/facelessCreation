"""Pydantic models for every editable artifact stored in a job directory."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

ID_RE = r"^[A-Za-z0-9_-]{1,32}$"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class JobState(StrEnum):
    DRAFT = "DRAFT"
    RESEARCH_REVIEW = "RESEARCH_REVIEW"
    SCRIPT_APPROVED = "SCRIPT_APPROVED"
    ASSETS_READY = "ASSETS_READY"
    RENDERED = "RENDERED"
    FINAL_APPROVED = "FINAL_APPROVED"
    EXPORTED = "EXPORTED"
    FAILED = "FAILED"


STATE_ORDER = [
    JobState.DRAFT,
    JobState.RESEARCH_REVIEW,
    JobState.SCRIPT_APPROVED,
    JobState.ASSETS_READY,
    JobState.RENDERED,
    JobState.FINAL_APPROVED,
    JobState.EXPORTED,
]


# --------------------------------------------------------------------------- job


class JobInput(Strict):
    id: str
    topic: str = Field(min_length=3, max_length=300)
    day: int | None = None
    date: str | None = None
    calendar_ref: dict | None = None
    notes: str = ""
    created: str


# ---------------------------------------------------------------------- research


class Source(Strict):
    id: str = Field(pattern=ID_RE)
    url: HttpUrl | None = None
    title: str = Field(min_length=1)
    publisher: str = ""
    published: str = ""  # ISO date if known; "" = unknown (flagged)
    accessed: str = ""
    notes: str = ""
    excerpt: str = Field(default="", max_length=4000)


class ResearchClaim(Strict):
    id: str = Field(pattern=ID_RE)
    text: str = Field(min_length=3)
    source_ids: list[str] = Field(default_factory=list)
    verified: bool = False  # set by the human researcher


class Research(Strict):
    topic: str = ""
    sources: list[Source] = Field(default_factory=list)
    claims: list[ResearchClaim] = Field(default_factory=list)
    notes: str = ""

    @model_validator(mode="after")
    def _unique_ids(self) -> Research:
        for kind, items in (("source", self.sources), ("claim", self.claims)):
            ids = [i.id for i in items]
            dup = {i for i in ids if ids.count(i) > 1}
            if dup:
                raise ValueError(f"duplicate {kind} ids: {sorted(dup)}")
        return self


# ------------------------------------------------------------------------ script


class Scene(Strict):
    id: str = Field(pattern=ID_RE)
    narration: str = Field(min_length=1, max_length=1200)
    visual: str = Field(default="", max_length=300)
    search_terms: list[str] = Field(default_factory=list, max_length=8)
    overlay: str = Field(default="", max_length=80)
    claim_ids: list[str] = Field(default_factory=list)

    @field_validator("search_terms")
    @classmethod
    def _terms(cls, v: list[str]) -> list[str]:
        return [t.strip()[:60] for t in v if t and t.strip()]


class ScriptClaim(Strict):
    id: str = Field(pattern=ID_RE)
    text: str = Field(min_length=3)
    source_ids: list[str] = Field(default_factory=list)
    needs_review: bool = True


class PlatformCopy(Strict):
    youtube_title: str = Field(default="", max_length=100)
    youtube_description: str = Field(default="", max_length=5000)
    tiktok_caption: str = Field(default="", max_length=2200)
    instagram_caption: str = Field(default="", max_length=2200)
    hashtags: list[str] = Field(default_factory=list, max_length=15)

    @field_validator("hashtags")
    @classmethod
    def _tags(cls, v: list[str]) -> list[str]:
        out = []
        for t in v:
            t = re.sub(r"[^A-Za-z0-9_]", "", t.lstrip("#"))
            if t:
                out.append("#" + t)
        return out


class Script(Strict):
    title: str = Field(min_length=3, max_length=120)
    hook: str = Field(default="", max_length=400)
    narration: str = ""
    scenes: list[Scene] = Field(min_length=1, max_length=20)
    claims: list[ScriptClaim] = Field(default_factory=list)
    platform: PlatformCopy = Field(default_factory=PlatformCopy)
    provider: str = "manual"
    provider_meta: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _consistency(self) -> Script:
        ids = [s.id for s in self.scenes]
        if len(set(ids)) != len(ids):
            raise ValueError("scene ids must be unique")
        cids = [c.id for c in self.claims]
        if len(set(cids)) != len(cids):
            raise ValueError("claim ids must be unique")
        # Scene narration is authoritative; keep the flat narration in sync.
        self.narration = " ".join(s.narration for s in self.scenes)
        return self


# ------------------------------------------------------------------------- voice


class SceneTiming(Strict):
    scene_id: str
    start: float
    speech_end: float
    end: float  # includes the pause after the scene
    audio_file: str
    text: str


class VoiceTiming(Strict):
    provider: str
    voice: str
    model: str
    sample_rate: int
    duration: float
    scenes: list[SceneTiming]
    loudness_lufs: float | None = None
    demo: bool = False  # True for espeak or synthetic fixtures: not production audio


# ------------------------------------------------------------------------ assets


class RightsStatus(StrEnum):
    UNREVIEWED = "unreviewed"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class LibraryAsset(Strict):
    id: str
    kind: Literal["video", "image", "audio"]
    path: str  # relative to the library dir
    sha256: str
    title: str = ""
    tags: list[str] = Field(default_factory=list)
    source_url: str = ""
    creator: str = ""
    license: str
    license_url: str = ""
    attribution: str = ""
    attribution_required: bool = False
    commercial_use: bool
    acquired: str
    provider: str = "local"
    ai_generated: bool = False
    width: int | None = None
    height: int | None = None
    duration: float | None = None
    notes: str = ""


class SceneAsset(Strict):
    scene_id: str
    kind: Literal["video", "image", "procedural", "audio", "missing"]
    library_id: str | None = None
    path: str  # relative to the job dir or absolute library path
    sha256: str
    template: str | None = None
    source_url: str = ""
    creator: str = ""
    license: str
    attribution: str = ""
    attribution_required: bool = False
    commercial_use: bool
    provider: str
    ai_generated: bool = False
    rights: RightsStatus = RightsStatus.UNREVIEWED
    candidates: list[str] = Field(default_factory=list)
    reason: str = ""
    # hash of the scene's narration when you picked this picture by hand: a manual pick is
    # kept only while the scene says the same thing
    narration_sha: str = ""


class AssetManifest(Strict):
    scenes: list[SceneAsset]
    music: SceneAsset | None = None


# ---------------------------------------------------------------------- captions


class Word(Strict):
    text: str
    start: float
    end: float
    probability: float | None = None


class Caption(Strict):
    index: int
    start: float
    end: float
    text: str


class CaptionTrack(Strict):
    source: Literal["whisper", "approximate", "imported"]
    model: str = ""
    language: str = "en"
    translated_by: str = ""  # model that translated from the English narration, if any
    burn: bool = True  # False = "no subtitles" chosen: nothing burned into the video
    approximate: bool = False
    reviewed: bool = False
    divergence_ratio: float | None = None
    divergences: list[str] = Field(default_factory=list)
    captions: list[Caption]
