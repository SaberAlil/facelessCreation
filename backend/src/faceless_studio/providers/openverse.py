"""Real photos from Openverse (WordPress's open media search: Flickr, Commons, museums…).

Free API, no key (anonymous rate limit of a few hundred searches a day). Only CC0, public
domain mark and CC BY photos are requested, the same rule as Wikimedia Commons: no
share-alike, non-commercial or no-derivatives licenses. Creator and license are recorded
and CC BY photos are credited in the export texts.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import httpx

from ..research import _get_public
from ..util import FacelessError, atomic_write_bytes

log = logging.getLogger("faceless")

API = "https://api.openverse.org/v1/images/"
UA = "FacelessStudio/0.1 (local research helper)"
MAX_BYTES = 25 * 1024 * 1024
LICENSES = {"cc0": "CC0 1.0", "pdm": "Public domain", "by": "CC BY"}
SKIP_TITLE = re.compile(r"\b(logo|icon|map|diagram|chart|graph|flag|screenshot|infographic|"
                        r"poster|meme|text)\b", re.I)


@dataclass
class OpenversePhoto:
    id: str
    title: str
    page_url: str
    image_url: str
    width: int
    height: int
    license: str
    license_url: str
    artist: str
    attribution_required: bool
    description: str

    @property
    def key(self) -> str:
        return f"openverse:{self.id}"


def _client(timeout: float = 25.0) -> httpx.Client:
    return httpx.Client(timeout=timeout, headers={"User-Agent": UA}, follow_redirects=False)


def search(query: str, min_width: int = 900, limit: int = 12,
           exclude: set[str] | None = None) -> list[OpenversePhoto]:
    """Photos for a query, best first, filtered by license and size."""
    # page_size 20 is the anonymous maximum (more returns 401)
    params = {"q": query, "license": ",".join(LICENSES), "page_size": 20, "mature": "false",
              "category": "photograph"}
    try:
        with _client() as c:
            r = c.get(API, params=params)
    except httpx.HTTPError as e:
        raise FacelessError(f"Openverse request failed: {e}") from e
    if r.status_code == 429:
        raise FacelessError("Openverse daily search limit reached; try again tomorrow")
    if r.status_code >= 400:
        raise FacelessError(f"Openverse request failed: HTTP {r.status_code}")
    out: list[OpenversePhoto] = []
    for it in r.json().get("results", []):
        lic = (it.get("license") or "").lower()
        if lic not in LICENSES:
            continue
        key = f"openverse:{it.get('id')}"
        title = (it.get("title") or "").strip()
        if (exclude and key in exclude) or SKIP_TITLE.search(title):
            continue
        w, h = int(it.get("width") or 0), int(it.get("height") or 0)
        if max(w, h) < min_width:
            continue
        version = it.get("license_version") or ""
        name = LICENSES[lic] + (f" {version}" if lic == "by" and version else "")
        tags = " ".join(t.get("name", "") for t in (it.get("tags") or [])[:15])
        out.append(OpenversePhoto(
            id=str(it.get("id")), title=title or "untitled", image_url=it.get("url") or "",
            page_url=it.get("foreign_landing_url") or "", width=w, height=h, license=name,
            license_url=it.get("license_url") or "",
            artist=(it.get("creator") or "Unknown author")[:200],
            attribution_required=lic == "by", description=tags[:300]))
        if len(out) >= limit:
            break
    return out


def download(photo: OpenversePhoto, dest: Path) -> Path:
    try:
        with _client(60) as c:
            r = _get_public(c, photo.image_url)
    except httpx.HTTPError as e:
        raise FacelessError(f"photo download failed: {e}") from e
    if r.status_code >= 400:
        raise FacelessError(f"photo download failed: HTTP {r.status_code}")
    if not r.headers.get("content-type", "").startswith(("image/jpeg", "image/png")):
        raise FacelessError("photo is not a JPEG or PNG")
    if len(r.content) > MAX_BYTES:
        raise FacelessError("photo larger than 25 MB")
    atomic_write_bytes(dest, r.content)
    return dest


def attribution(photo: OpenversePhoto) -> str:
    return f"Photo: {photo.artist}, {photo.license}, via Openverse"
