"""Optional Pexels video provider (official API, key from PEXELS_API_KEY).

Downloads are bounded in size, limited to Pexels' own CDN hosts, and every file is
recorded with its page URL, creator and the Pexels License. Free to use; no paid calls.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

from ..config import StockConfig
from ..util import FacelessError, ProviderUnavailable, tmp_path_for

log = logging.getLogger("faceless")

API = "https://api.pexels.com"
ALLOWED_DOWNLOAD_HOSTS = ("videos.pexels.com", "player.vimeo.com", "vod-progressive.akamaized.net")
PEXELS_LICENSE = "Pexels License"
PEXELS_LICENSE_URL = "https://www.pexels.com/license/"


class PexelsProvider:
    name = "pexels"

    def __init__(self, cfg: StockConfig, api_key: str | None = None,
                 transport: httpx.BaseTransport | None = None):
        key = api_key or os.environ.get("PEXELS_API_KEY")
        if not key:
            raise ProviderUnavailable("PEXELS_API_KEY is not set",
                                      hint="optional: create a free key at pexels.com/api "
                                      "and put it in .env")
        self.cfg = cfg
        self.client = httpx.Client(timeout=cfg.timeout_s, transport=transport,
                                   headers={"Authorization": key})

    def search(self, query: str, per_page: int = 6) -> list[dict]:
        params = {"query": query, "orientation": "portrait", "per_page": per_page,
                  "size": "medium"}
        for attempt in range(3):
            try:
                r = self.client.get(f"{API}/videos/search", params=params)
            except httpx.HTTPError as e:
                if attempt == 2:
                    raise FacelessError(f"Pexels search failed: {e}") from e
                time.sleep(2 * (attempt + 1))
                continue
            if r.status_code == 429:
                reset = r.headers.get("X-Ratelimit-Reset")
                raise FacelessError("Pexels rate limit reached",
                                    hint=f"retry after reset ({reset})" if reset else None)
            if r.status_code in (401, 403):
                raise ProviderUnavailable("Pexels rejected the API key")
            r.raise_for_status()
            return [self._candidate(v) for v in r.json().get("videos", [])
                    if self._candidate(v)]
        return []

    @staticmethod
    def _candidate(v: dict) -> dict | None:
        files = [f for f in v.get("video_files", []) if f.get("file_type") == "video/mp4"
                 and f.get("width") and f.get("height")]
        portrait = [f for f in files if f["height"] >= f["width"] and f["height"] <= 2560]
        pool = portrait or files
        if not pool:
            return None
        best = min(pool, key=lambda f: abs(f["height"] - 1920))
        user = v.get("user") or {}
        return {"id": v.get("id"), "page_url": v.get("url", ""), "creator": user.get("name", ""),
                "creator_url": user.get("url", ""), "duration": v.get("duration"),
                "width": best["width"], "height": best["height"], "link": best["link"]}

    def download(self, cand: dict, dest: Path) -> Path:
        host = urlparse(cand["link"]).hostname or ""
        if urlparse(cand["link"]).scheme != "https" or not any(
                host == h or host.endswith("." + h) for h in ALLOWED_DOWNLOAD_HOSTS):
            raise FacelessError(f"refusing download from unexpected host {host}")
        limit = self.cfg.max_download_mb * 1024 * 1024
        tmp = tmp_path_for(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        size = 0
        try:
            with self.client.stream("GET", cand["link"], follow_redirects=True) as r:
                r.raise_for_status()
                final_host = r.url.host
                if not any(final_host == h or final_host.endswith("." + h)
                           for h in ALLOWED_DOWNLOAD_HOSTS):
                    raise FacelessError(f"download redirected to {final_host}")
                with open(tmp, "wb") as f:
                    for chunk in r.iter_bytes(1 << 16):
                        size += len(chunk)
                        if size > limit:
                            raise FacelessError(f"download exceeds {self.cfg.max_download_mb} MB")
                        f.write(chunk)
            tmp.replace(dest)
        finally:
            tmp.unlink(missing_ok=True)
        return dest
