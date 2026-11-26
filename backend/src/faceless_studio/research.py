"""Manual research records and script-vs-research claim checks.

LLM output is never treated as research. Optional URL ingestion is bounded,
http(s)-only and refuses private/loopback addresses.
"""

from __future__ import annotations

import html
import ipaddress
import re
import socket
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import httpx
from pydantic import ValidationError

from .config import ScriptConfig
from .schemas import Research, Script, Source
from .util import FacelessError, ValidationFailed, read_json, word_count, write_json


def load_research(path: Path) -> Research:
    if not path.is_file():
        return Research()
    try:
        return Research.model_validate(read_json(path))
    except (ValidationError, ValueError) as e:
        raise ValidationFailed(f"invalid research file {path}: {e}") from e


def save_research(path: Path, research: Research) -> None:
    write_json(path, research.model_dump(mode="json"))


def research_problems(r: Research) -> list[str]:
    """Problems a human must look at before the research supports a script."""
    out: list[str] = []
    if not r.sources:
        out.append("no sources recorded")
    src_ids = {s.id for s in r.sources}
    for s in r.sources:
        if not s.url:
            out.append(f"source {s.id}: no URL")
        if not s.published:
            out.append(f"source {s.id}: publication date unknown")
        if not s.accessed:
            out.append(f"source {s.id}: access date missing")
    for c in r.claims:
        missing = [i for i in c.source_ids if i not in src_ids]
        if not c.source_ids:
            out.append(f"claim {c.id}: no supporting source")
        if missing:
            out.append(f"claim {c.id}: unknown source ids {missing}")
        if not c.verified:
            out.append(f"claim {c.id}: not marked verified")
    return out


@dataclass
class ScriptReport:
    words: int
    est_seconds: float
    errors: list[str]
    warnings: list[str]
    flagged_claims: list[str]

    @property
    def ok(self) -> bool:
        return not self.errors


WORDS_PER_SECOND = 2.45  # measured default for Kokoro af_heart at speed 1.0 (re-measure)


def check_script(script: Script, research: Research, cfg: ScriptConfig) -> ScriptReport:
    """Validate length/structure and mark claims that the research does not support.

    Mutates ``script.claims[*].needs_review`` so the flags are persisted.
    """
    errors: list[str] = []
    warnings: list[str] = []
    words = word_count(script.narration)
    if words < cfg.hard_words_min or words > cfg.hard_words_max:
        errors.append(f"narration is {words} words; allowed {cfg.hard_words_min}-"
                      f"{cfg.hard_words_max} (target {cfg.target_words_min}-"
                      f"{cfg.target_words_max})")
    elif not cfg.target_words_min <= words <= cfg.target_words_max:
        warnings.append(f"narration is {words} words; target {cfg.target_words_min}-"
                        f"{cfg.target_words_max}")
    n = len(script.scenes)
    if n < cfg.min_scenes or n > cfg.max_scenes:
        warnings.append(f"{n} scenes; recommended {cfg.min_scenes}-{cfg.max_scenes}")
    if not script.hook:
        warnings.append("no hook recorded")
    for s in script.scenes:
        if word_count(s.narration) > 60:
            warnings.append(f"scene {s.id} is long ({word_count(s.narration)} words)")
        if not s.visual and not s.search_terms:
            warnings.append(f"scene {s.id}: no visual concept or search terms")

    rsrc = {x.id for x in research.sources}
    rclaims = {c.id: c for c in research.claims}
    flagged: list[str] = []
    claim_ids = {c.id for c in script.claims}
    for c in script.claims:
        reasons = []
        if not c.source_ids:
            reasons.append("no source")
        unknown = [i for i in c.source_ids if i not in rsrc and i not in rclaims]
        if unknown:
            reasons.append(f"unknown sources {unknown}")
        linked = [rclaims[i] for i in c.source_ids if i in rclaims]
        if linked and not all(x.verified for x in linked):
            reasons.append("research claim not verified")
        if c.needs_review and not reasons:
            # e.g. every LLM-written claim, or a claim citing a source directly: a human
            # must check it against the source (then set needs_review to false).
            reasons.append("marked for human review")
        if reasons:
            c.needs_review = True
            flagged.append(f"{c.id}: {c.text[:80]} ({', '.join(reasons)})")
    for s in script.scenes:
        for cid in s.claim_ids:
            if cid not in claim_ids:
                errors.append(f"scene {s.id} references unknown claim {cid}")
    if re.search(r"\d", script.narration) and not script.claims:
        warnings.append("narration contains numbers but no claims are recorded")
    if not research.sources:
        warnings.append("no research sources: every factual statement needs human checking")
    return ScriptReport(words, round(words / WORDS_PER_SECOND, 1), errors, warnings, flagged)


# ------------------------------------------------------------- URL ingestion

MAX_FETCH_BYTES = 2_000_000


def _public_host(host: str) -> None:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise FacelessError(f"cannot resolve {host}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified):
            raise FacelessError(f"refusing non-public address {ip} for {host}")


def fetch_source(url: str, source_id: str, timeout: float = 20.0) -> Source:
    """Fetch a public page's title and a short excerpt as a research *candidate*.

    The excerpt is untrusted data for the human researcher; it is never sent to the
    LLM as instructions. No redirects to other hosts, no paywall circumvention.
    """
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise FacelessError("only http(s) URLs are supported")
    _public_host(u.hostname)
    headers = {"User-Agent": "FacelessStudio/0.1 (research helper)"}
    with httpx.Client(timeout=timeout, follow_redirects=False, headers=headers) as client, \
            client.stream("GET", url) as r:
        if r.status_code in (301, 302, 303, 307, 308):
            raise FacelessError(f"redirected to {r.headers.get('location')}; add that URL "
                                "instead after checking it")
        if r.status_code in (401, 402, 403):
            raise FacelessError(f"access restricted (HTTP {r.status_code}); add the source "
                                "manually if you have legitimate access")
        r.raise_for_status()
        ctype = r.headers.get("content-type", "")
        if "html" not in ctype and "text" not in ctype:
            raise FacelessError(f"unsupported content type {ctype!r}")
        body = b""
        for chunk in r.iter_bytes():
            body += chunk
            if len(body) > MAX_FETCH_BYTES:
                break
    text = body.decode(r.encoding or "utf-8", errors="replace")
    m = re.search(r"<title[^>]*>(.*?)</title>", text, re.S | re.I)
    title = html.unescape(m.group(1)).strip() if m else u.hostname
    body_text = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", text)
    body_text = html.unescape(re.sub(r"<[^>]+>", " ", body_text))
    excerpt = " ".join(body_text.split())[:1200]
    return Source(id=source_id, url=url, title=title[:300], publisher=u.hostname,
                  accessed=date.today().isoformat(), excerpt=excerpt,
                  notes="Fetched automatically: verify publication date and content.")


def research_template(topic: str) -> Research:
    return Research(topic=topic, notes="Add sources and verified claims. Unverified claims "
                    "are flagged during script review.")


def write_template(path: Path, topic: str) -> None:
    if path.exists():
        raise FacelessError(f"{path} already exists")
    save_research(path, research_template(topic))
