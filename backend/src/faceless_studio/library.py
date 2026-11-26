"""Local licensed-media library with provenance metadata."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from pydantic import ValidationError

from . import media
from .db import Database
from .schemas import LibraryAsset
from .util import FacelessError, ValidationFailed, safe_filename, sha256_file, user_file, utcnow

VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}
AUDIO_EXT = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aac"}
MAX_IMPORT_BYTES = 2 * 1024**3


def kind_for(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in VIDEO_EXT:
        return "video"
    if ext in IMAGE_EXT:
        return "image"
    if ext in AUDIO_EXT:
        return "audio"
    raise FacelessError(f"unsupported media type {ext}; supported: "
                        f"{sorted(VIDEO_EXT | IMAGE_EXT | AUDIO_EXT)}")


def add_asset(db: Database, library_dir: Path, src: str | Path, *, license: str,
              commercial_use: bool, source_url: str = "", creator: str = "",
              license_url: str = "", attribution: str = "", attribution_required: bool = False,
              tags: list[str] | None = None, title: str = "", provider: str = "local",
              ai_generated: bool = False, notes: str = "", move: bool = False) -> LibraryAsset:
    path = user_file(src)
    if path.stat().st_size > MAX_IMPORT_BYTES:
        raise FacelessError(f"{path.name} is larger than {MAX_IMPORT_BYTES // 1024**3} GB")
    if not license.strip():
        raise ValidationFailed("a license is required (e.g. 'Pexels License', 'CC0-1.0', "
                               "'own work')")
    kind = kind_for(path)
    digest = sha256_file(path)
    existing = db.one("SELECT data FROM library WHERE sha256 = ?", (digest,))
    if existing:
        return LibraryAsset.model_validate_json(existing["data"])
    w, h, dur = media.media_dims(path)  # also proves FFmpeg can decode the file
    if kind in ("video", "image") and not (w and h):
        raise ValidationFailed(f"{path.name}: no decodable picture")
    asset_id = f"{kind[0]}{digest[:10]}"
    rel = Path(kind) / f"{asset_id}-{safe_filename(path.stem)[:40]}{path.suffix.lower()}"
    dest = library_dir / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    if move:
        shutil.move(str(path), dest)
    else:
        shutil.copy2(path, dest)
    asset = LibraryAsset(
        id=asset_id, kind=kind, path=str(rel), sha256=digest, title=title or path.stem,
        tags=[t.strip().lower() for t in (tags or []) if t.strip()], source_url=source_url,
        creator=creator, license=license.strip(), license_url=license_url,
        attribution=attribution, attribution_required=attribution_required,
        commercial_use=commercial_use, acquired=utcnow(), provider=provider,
        ai_generated=ai_generated, width=w, height=h,
        duration=None if kind == "image" else dur, notes=notes,
    )
    db.execute("INSERT INTO library(id, sha256, data) VALUES (?,?,?)",
               (asset.id, digest, asset.model_dump_json()))
    return asset


def add_from_manifest(db: Database, library_dir: Path, manifest: Path) -> list[LibraryAsset]:
    """JSON list of {file, license, commercial_use, ...}; paths relative to the manifest."""
    entries = json.loads(user_file(manifest).read_text(encoding="utf-8"))
    if not isinstance(entries, list):
        raise ValidationFailed("manifest must be a JSON list")
    out = []
    for i, e in enumerate(entries):
        try:
            f = manifest.parent / e.pop("file")
            out.append(add_asset(db, library_dir, f, **e))
        except (KeyError, TypeError, ValidationError) as err:
            raise ValidationFailed(f"manifest entry {i}: {err}") from err
    return out


def list_assets(db: Database, kind: str | None = None) -> list[LibraryAsset]:
    rows = db.all("SELECT data FROM library ORDER BY id")
    items = [LibraryAsset.model_validate_json(r["data"]) for r in rows]
    return [a for a in items if kind is None or a.kind == kind]


def get_asset(db: Database, asset_id: str) -> LibraryAsset:
    row = db.one("SELECT data FROM library WHERE id = ?", (asset_id,))
    if not row:
        raise FacelessError(f"no library asset {asset_id}")
    return LibraryAsset.model_validate_json(row["data"])


def remove_asset(db: Database, library_dir: Path, asset_id: str) -> None:
    a = get_asset(db, asset_id)
    (library_dir / a.path).unlink(missing_ok=True)
    db.execute("DELETE FROM library WHERE id = ?", (asset_id,))


def tokens(text: str) -> set[str]:
    stop = {"a", "an", "the", "of", "and", "or", "to", "in", "on", "for", "with", "at", "by",
            "is", "are", "close", "up", "shot", "view"}
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in stop and len(t) > 1}


def score(asset: LibraryAsset, terms: list[str], visual: str) -> int:
    want = tokens(" ".join(terms) + " " + visual)
    have = tokens(" ".join(asset.tags) + " " + asset.title)
    return len(want & have)
