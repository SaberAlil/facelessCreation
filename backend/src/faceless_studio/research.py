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
from .schemas import Research, ResearchClaim, Script, Source
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


def _get_public(client: httpx.Client, url: str, max_redirects: int = 3) -> httpx.Response:
    """GET with redirects followed manually, re-checking every hop is a public host."""
    for _ in range(max_redirects + 1):
        u = urlparse(url)
        if u.scheme not in ("http", "https") or not u.hostname:
            raise FacelessError("only http(s) URLs are supported")
        _public_host(u.hostname)
        r = client.get(url)
        if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
            url = str(httpx.URL(url).join(r.headers["location"]))
            continue
        return r
    raise FacelessError("too many redirects")


def _paragraphs_from_html(text: str) -> list[str]:
    text = re.sub(r"(?is)<(script|style|noscript|nav|header|footer|aside|form).*?</\1>", " ",
                  text)
    paras = re.findall(r"(?is)<p[^>]*>(.*?)</p>", text) or [text]
    out = []
    for p in paras:
        t = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", p)).split())
        if len(t) >= 60:
            out.append(t)
    return out


_STOP = {"why", "how", "what", "the", "a", "an", "of", "and", "to", "in", "on", "for", "do",
         "does", "is", "are", "so", "use", "uses", "make", "makes", "with", "from", "its",
         "can", "who", "when", "where", "which", "your", "you", "they", "them", "their", "it",
         "be", "about", "into", "over", "under", "like", "just", "often", "some", "more",
         "less", "than", "actually", "really", "many", "much", "get", "gets", "most", "still",
         "matter", "matters", "means", "mean", "work", "works", "way", "ways", "behind",
         "explained", "every", "everyday", "need", "needs", "keep", "keeps",
         "people", "thing", "things", "teaches", "year"}


def topic_terms(topic: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", topic.lower()) if w not in _STOP and len(w) > 2}


def relevant_excerpt(paragraphs: list[str], topic: str, max_chars: int) -> str:
    """Pick the paragraphs that cover the topic's *specific* words, in document order.

    Words found in most paragraphs (e.g. "IKEA" in the IKEA article) carry little weight;
    rarer topic words ("showroom", "path") decide which paragraphs are relevant.
    """
    import math

    terms = topic_terms(topic)
    low = [p.lower() for p in paragraphs]
    n = max(1, len(paragraphs))
    df = {t: sum(1 for p in low if t in p) for t in terms}
    weight = {t: math.log((n + 1) / (df[t] + 0.5)) for t in terms if df[t]}
    scored = [(sum(w for t, w in weight.items() if t in p), i, paragraphs[i])
              for i, p in enumerate(low)]
    best = sorted(scored, key=lambda x: (-x[0], x[1]))
    chosen, total = [], 0
    for score, i, p in best:
        if total and score <= 0:
            break
        if total + len(p) > max_chars:
            if not chosen:
                chosen.append((i, p[:max_chars]))
            continue
        chosen.append((i, p))
        total += len(p) + 1
    return " ".join(p for _i, p in sorted(chosen))[:max_chars]


def fetch_source(url: str, source_id: str, timeout: float = 20.0, topic: str = "",
                 max_chars: int = 1500, transport: httpx.BaseTransport | None = None) -> Source:
    """Fetch a public page's title and the paragraphs most relevant to the topic.

    The excerpt is untrusted data: the LLM receives it inside a research block marked as
    data, never as instructions. Public hosts only (checked on every redirect hop),
    bounded size, no paywall circumvention.
    """
    headers = {"User-Agent": "FacelessStudio/0.1 (local research helper)"}
    with httpx.Client(timeout=timeout, follow_redirects=False, headers=headers,
                      transport=transport) as client:
        r = _get_public(client, url)
        if r.status_code in (401, 402, 403):
            raise FacelessError(f"access restricted (HTTP {r.status_code}); add the facts "
                                "yourself if you have legitimate access")
        r.raise_for_status()
        ctype = r.headers.get("content-type", "")
        if "html" not in ctype and "text" not in ctype:
            raise FacelessError(f"unsupported content type {ctype!r}")
        body = r.content[:MAX_FETCH_BYTES]
    text = body.decode(r.encoding or "utf-8", errors="replace")
    m = re.search(r"<title[^>]*>(.*?)</title>", text, re.S | re.I)
    host = urlparse(str(r.url)).hostname or ""
    title = " ".join(html.unescape(m.group(1)).split()) if m else host
    excerpt = relevant_excerpt(_paragraphs_from_html(text), topic, max_chars)
    return Source(id=source_id, url=url, title=title[:300] or host, publisher=host,
                  accessed=date.today().isoformat(), excerpt=excerpt,
                  notes="Fetched automatically: verify publication date and content.")


WIKI_API = "https://en.wikipedia.org/w/api.php"
WIKI_UA = "FacelessStudio/0.1 (local research helper)"


def _wiki_get(params: dict, timeout: float) -> dict:
    """Wikipedia API call. Uses urllib: Wikimedia's bot filter rejects httpx clients.

    Polite: a short pause before every call and one bounded retry on HTTP 429.
    """
    import json
    import time
    import urllib.error
    import urllib.parse
    import urllib.request

    req = urllib.request.Request(f"{WIKI_API}?{urllib.parse.urlencode(params)}",  # noqa: S310
                                 headers={"User-Agent": WIKI_UA})
    for attempt in range(2):
        time.sleep(0.5)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (fixed host)
                return json.loads(r.read(MAX_FETCH_BYTES))
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt == 0:
                wait = min(15.0, float(e.headers.get("Retry-After") or 5))
                time.sleep(wait)
                continue
            raise FacelessError(f"Wikipedia request failed: HTTP {e.code}") from e
        except OSError as e:
            raise FacelessError(f"Wikipedia request failed: {e}") from e
    raise FacelessError("Wikipedia rate limit")


def _stem(w: str) -> str:
    return w[:-1] if len(w) > 4 and w.endswith("s") else w


def is_relevant(title: str, excerpt: str, terms: set[str]) -> bool:
    """Title shares a topic word, or the excerpt uses at least 3 distinct topic words."""
    stems = {_stem(t) for t in terms}
    title_words = {_stem(w) for w in re.findall(r"[a-z0-9]+", title.lower())}
    text = excerpt.lower()
    distinct = sum(1 for st in stems if st in text)
    return bool(stems & title_words) and distinct >= 1 or distinct >= 3


def wiki_queries(topic: str) -> list[str]:
    """Search queries from specific to broad (Wikipedia search requires every word)."""
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'-]*", topic)
    proper = [w for i, w in enumerate(words) if i > 0 and (w[0].isupper() or w.isupper())]
    terms = sorted(topic_terms(topic), key=len, reverse=True)
    queries = [" ".join(terms), " ".join([*proper, *terms[:1]]), " ".join(proper),
               " ".join(terms[:2]), terms[0] if terms else topic]
    return list(dict.fromkeys(q for q in queries if q.strip()))


def wikipedia_sources(topic: str, max_articles: int = 2, max_chars: int = 1500,
                      timeout: float = 20.0, max_queries: int = 4) -> list[Source]:
    """Find English Wikipedia articles for the topic and keep their relevant paragraphs.

    One request per query (search + article text together). A starting point only:
    Wikipedia is a tertiary source, so every claim built on it stays flagged for human
    checking against the primary sources it cites.
    """
    terms = topic_terms(topic)
    out: list[Source] = []
    seen: set[str] = set()
    for query in wiki_queries(topic)[:max_queries]:
        if len(out) >= max_articles:
            break
        data = _wiki_get({"action": "query", "generator": "search", "gsrsearch": query,
                          "gsrlimit": 1, "prop": "extracts|info", "explaintext": 1,
                          "inprop": "url", "redirects": 1, "format": "json"}, timeout)
        for page in data.get("query", {}).get("pages", {}).values():
            title = page.get("title", "")
            if not title or title in seen:
                continue
            seen.add(title)
            paras = [p.strip() for p in (page.get("extract") or "").split("\n")
                     if len(p.strip()) >= 60 and not p.startswith("=")]
            excerpt = relevant_excerpt(paras, topic, max_chars)
            if not is_relevant(title, excerpt, terms):
                continue
            out.append(Source(
                id=f"W{len(out) + 1}", url=page.get("fullurl") or
                f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}",
                title=f"{title} (Wikipedia)", publisher="Wikipedia",
                accessed=date.today().isoformat(), excerpt=excerpt,
                notes="Found automatically on Wikipedia: check claims against the primary "
                      "sources the article cites."))
    return out


def fetch_wikipedia_url(url: str, sid: str, topic: str = "", max_chars: int = 1500) -> Source:
    """Read a Wikipedia article through the API (the site blocks automated page loads)."""
    from urllib.parse import unquote
    title = unquote(urlparse(url).path.split("/wiki/", 1)[-1]).replace("_", " ")
    data = _wiki_get({"action": "query", "prop": "extracts|info", "explaintext": 1,
                      "inprop": "url", "redirects": 1, "format": "json", "titles": title}, 20)
    page = next(iter(data.get("query", {}).get("pages", {}).values()), {})
    if "missing" in page or not page.get("extract"):
        raise FacelessError(f"Wikipedia article not found: {title}")
    paras = [p.strip() for p in page["extract"].split("\n")
             if len(p.strip()) >= 60 and not p.startswith("=")]
    return Source(id=sid, url=page.get("fullurl") or url,
                  title=f"{page.get('title', title)} (Wikipedia)", publisher="Wikipedia",
                  accessed=date.today().isoformat(),
                  excerpt=relevant_excerpt(paras, topic, max_chars) or " ".join(paras)[:max_chars],
                  notes="Wikipedia: check claims against the primary sources it cites.")


def fetch_doi(url: str, sid: str, topic: str = "", max_chars: int = 1500) -> Source:
    """Paper metadata (and abstract when published) from Crossref; publisher sites block
    automated access."""
    import json
    import urllib.parse
    import urllib.request

    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", url, flags=re.I)
    api = f"https://api.crossref.org/works/{urllib.parse.quote(doi, safe='/')}"
    req = urllib.request.Request(api, headers={"User-Agent": WIKI_UA})  # noqa: S310
    try:
        with urllib.request.urlopen(req, timeout=20) as r:  # noqa: S310 (fixed https host)
            m = json.loads(r.read(MAX_FETCH_BYTES))["message"]
    except OSError as e:
        raise FacelessError(f"Crossref lookup failed for {doi}: {e}") from e
    title = " ".join(m.get("title") or []) or doi
    journal = " ".join(m.get("container-title") or [])
    year = ((m.get("published") or m.get("issued") or {}).get("date-parts") or [[None]])[0][0]
    authors = ", ".join(f"{a.get('given', '')} {a.get('family', '')}".strip()
                        for a in (m.get("author") or [])[:3])
    abstract = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", m.get("abstract") or "")).split())
    abstract = re.sub(r"^(Abstract|Purpose)\s*", "", abstract)
    excerpt = (f"Peer-reviewed article: \"{title}\" by {authors or 'unknown authors'}, "
               f"{journal} ({year}). ") + (f"Abstract: {abstract}" if abstract else
                                           "No abstract available: use the title only.")
    return Source(id=sid, url=url, title=title[:300], publisher=journal[:200],
                  published=str(year or ""), accessed=date.today().isoformat(),
                  excerpt=excerpt[:max_chars],
                  notes="Metadata from Crossref; read the paper before relying on details.")


def fetch_any(url: str, sid: str, topic: str = "", max_chars: int = 1500) -> Source:
    host = (urlparse(url).hostname or "").lower()
    if host.endswith("wikipedia.org") and "/wiki/" in url:
        return fetch_wikipedia_url(url, sid, topic, max_chars)
    if host in ("doi.org", "dx.doi.org"):
        return fetch_doi(url, sid, topic, max_chars)
    return fetch_source(url, sid, topic=topic, max_chars=max_chars)


_URL = re.compile(r"https?://\S+")


def parse_research_cell(cell: str) -> tuple[list[tuple[str, str]], list[str]]:
    """Calendar research cell → ([(url, fact)], [notes]).

    One item per line (Alt+Enter in Excel) or separated by ';':
    ``https://… | what this source says`` · ``https://…`` · any other text = a note.
    """
    links: list[tuple[str, str]] = []
    notes: list[str] = []
    for raw in re.split(r"[\r\n]+|\s;\s|;(?=\s*https?://)", cell or ""):
        line = raw.strip().strip("•-* ").strip()
        if not line:
            continue
        m = _URL.search(line)
        if m:
            url = m.group(0).rstrip(").,]")
            rest = (line[: m.start()] + line[m.end():]).strip(" |:-–")
            links.append((url, rest))
        else:
            notes.append(line)
    return links, notes


def build_research(topic: str, cell: str, max_sources: int = 4, excerpt_chars: int = 1500,
                   auto_wikipedia: bool = True, fetch=None, wiki=wikipedia_sources
                   ) -> tuple[Research, list[str]]:
    """Research for a job from the calendar cell (or Wikipedia when it is empty)."""
    fetch = fetch or fetch_any
    log: list[str] = []
    links, notes = parse_research_cell(cell)
    sources: list[Source] = []
    claims: list[ResearchClaim] = []
    for i, (url, fact) in enumerate(links[:max_sources], 1):
        sid = f"S{i}"
        try:
            src = fetch(url, sid, topic=topic, max_chars=excerpt_chars)
            log.append(f"{sid}: fetched {url}")
        except (FacelessError, httpx.HTTPError) as e:
            src = Source(id=sid, url=url, title=urlparse(url).hostname or url,
                         publisher=urlparse(url).hostname or "", accessed=date.today().isoformat(),
                         notes=f"Could not fetch automatically ({e}); only your notes are used.")
            log.append(f"{sid}: could not fetch {url}: {e}")
        sources.append(src)
        if fact:
            # Facts you typed next to a source in your own calendar are your research.
            claims.append(ResearchClaim(id=f"R{len(claims) + 1}", text=fact, source_ids=[sid],
                                        verified=True))
    if not sources and auto_wikipedia:
        try:
            sources = wiki(topic, max_chars=excerpt_chars)
            log.append(f"no research in the calendar: found {len(sources)} Wikipedia article(s)")
        except (FacelessError, httpx.HTTPError) as e:
            log.append(f"Wikipedia lookup failed: {e}")
    note = "Research built automatically from the calendar"
    if not links:
        note += " (empty research cell; Wikipedia used as a starting point)"
    if notes:
        note += ". Your notes: " + " | ".join(notes)
    return Research(topic=topic, sources=sources, claims=claims, notes=note[:3000]), log


def research_template(topic: str) -> Research:
    return Research(topic=topic, notes="Add sources and verified claims. Unverified claims "
                    "are flagged during script review.")


def write_template(path: Path, topic: str) -> None:
    if path.exists():
        raise FacelessError(f"{path} already exists")
    save_research(path, research_template(topic))
