"""Real photos from Wikimedia Commons (free API, no key).

Only photos whose license allows commercial use without share-alike are accepted
(public domain, CC0, CC BY): share-alike licenses could require licensing the whole
video the same way. Photographer and license are recorded; CC BY photos are credited in
the export's post descriptions. Uses urllib: Wikimedia's bot filter rejects httpx.
"""

from __future__ import annotations

import html
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from ..util import FacelessError, atomic_write_bytes

log = logging.getLogger("faceless")

API = "https://commons.wikimedia.org/w/api.php"
UA = "FacelessStudio/0.1 (local research helper)"
MAX_BYTES = 25 * 1024 * 1024
# Files that are rarely useful as a documentary background
SKIP_TITLE = re.compile(r"\b(logo|icon|map|diagram|chart|graph|flag|coat of arms|seal|"
                        r"signature|screenshot|svg|plan|drawing|scheme|infographic)\b", re.I)


@dataclass
class CommonsPhoto:
    title: str
    page_url: str
    image_url: str  # scaled rendition, ~1600 px
    width: int
    height: int
    license: str
    license_url: str
    artist: str
    attribution_required: bool
    description: str


def _get(params: dict, timeout: float = 25.0) -> dict:
    req = urllib.request.Request(f"{API}?{urllib.parse.urlencode(params)}",  # noqa: S310
                                 headers={"User-Agent": UA})
    for attempt in range(3):
        time.sleep(0.4)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (fixed host)
                return json.loads(r.read(5_000_000))
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 2:
                time.sleep(min(30.0, float(e.headers.get("Retry-After") or 8)))
                continue
            raise FacelessError(f"Wikimedia Commons request failed: HTTP {e.code}") from e
        except OSError as e:
            raise FacelessError(f"Wikimedia Commons request failed: {e}") from e
    raise FacelessError("Wikimedia Commons rate limit")


def _plain(value: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", value or "")).split())


def license_ok(short: str, allowed: list[str]) -> bool:
    """Commercial use without share-alike: PD, CC0, CC BY (any version/jurisdiction)."""
    s = short.lower().strip()
    if re.search(r"(\b|-)(sa|nc|nd)\b|gfdl|non-free|fair use", s):
        return False  # share-alike, non-commercial, no-derivatives, GFDL
    return any(s == a or s.startswith(a) for a in allowed)


def search(query: str, allowed: list[str], min_width: int = 900, limit: int = 12,
           exclude: set[str] | None = None) -> list[CommonsPhoto]:
    """Photos for a query, best first, filtered by license, size and type."""
    data = _get({"action": "query", "format": "json", "generator": "search",
                 "gsrsearch": f"{query} filetype:bitmap", "gsrnamespace": 6,
                 "gsrlimit": max(limit * 2, 20), "prop": "imageinfo",
                 "iiprop": "url|size|mime|extmetadata", "iiurlwidth": 1600})
    pages = sorted(data.get("query", {}).get("pages", {}).values(),
                   key=lambda p: p.get("index", 999))
    out: list[CommonsPhoto] = []
    for p in pages:
        title = p.get("title", "")
        if exclude and title in exclude:
            continue
        if SKIP_TITLE.search(title):
            continue
        info = (p.get("imageinfo") or [{}])[0]
        if info.get("mime") not in ("image/jpeg", "image/png"):
            continue
        w, h = int(info.get("width") or 0), int(info.get("height") or 0)
        if max(w, h) < min_width:
            continue
        meta = info.get("extmetadata") or {}
        short = _plain((meta.get("LicenseShortName") or {}).get("value", ""))
        if not license_ok(short, allowed):
            continue
        artist = _plain((meta.get("Artist") or {}).get("value", "")) or "Unknown author"
        attr_req = (meta.get("AttributionRequired") or {}).get("value", "true") == "true" \
            and not short.lower().startswith(("public domain", "pd", "cc0"))
        out.append(CommonsPhoto(
            title=title, page_url=info.get("descriptionurl") or
            f"https://commons.wikimedia.org/wiki/{urllib.parse.quote(title.replace(' ', '_'))}",
            image_url=info.get("thumburl") or info.get("url"), width=w, height=h,
            license=short, license_url=(meta.get("LicenseUrl") or {}).get("value", ""),
            artist=artist[:200], attribution_required=attr_req,
            description=_plain((meta.get("ImageDescription") or {}).get("value", ""))[:300]))
        if len(out) >= limit:
            break
    return out


def download(photo: CommonsPhoto, dest: Path) -> Path:
    host = urllib.parse.urlparse(photo.image_url).hostname or ""
    if host not in ("upload.wikimedia.org", "thumb.wikimedia.org"):
        raise FacelessError(f"unexpected image host {host}")
    req = urllib.request.Request(photo.image_url, headers={"User-Agent": UA})  # noqa: S310
    try:
        with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310 (fixed host)
            data = r.read(MAX_BYTES + 1)
    except OSError as e:
        raise FacelessError(f"photo download failed: {e}") from e
    if len(data) > MAX_BYTES:
        raise FacelessError("photo larger than 25 MB")
    atomic_write_bytes(dest, data)
    return dest


def attribution(photo: CommonsPhoto) -> str:
    return f"Photo: {photo.artist}, {photo.license}, via Wikimedia Commons"
