"""Re-date the content calendar and fill empty "Research / sources" cells with real links.

Sources come from a plan file (content/calendar_source_plan.tsv: day, Wikipedia article
title, research-paper search phrase), because topics are phrased as hooks while articles
and papers use the concept's name. For each day:
  1. The planned Wikipedia article is verified to exist (not a disambiguation page) and
     its canonical URL is used.
  2. Crossref (free registry of scholarly DOIs): the most-cited journal article whose
     *title* contains the planned phrase's words, linked by DOI. Skipped when nothing
     matches: no source is better than a wrong one. (Some cached results came from
     OpenAlex, which allows only ~100 anonymous searches a day.)

Every link comes from a live API result; nothing is invented. These are starting points
for research, found automatically: check them before relying on them. A report CSV lists
every match so you can review them quickly.

    python backend/scripts/fill_calendar_sources.py --start 2026-12-01
    python backend/scripts/fill_calendar_sources.py --overwrite   # replace generated cells

Safe to re-run: the original workbook is backed up first, lookups are cached
(data/cache/calendar_sources.json) and cells you already filled are never overwritten.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

import openpyxl

from faceless_studio.research import _stem, topic_terms
from faceless_studio.util import FacelessError

REPO = Path(__file__).resolve().parents[2]
UA = {"User-Agent": "FacelessStudio/0.1 (local research helper)"}
OPENALEX = "https://api.openalex.org/works"
JUNK_HOSTS = ("zenodo", "figshare", "osf.io", "researchsquare", "preprints.org")


def _openalex(params: dict) -> dict:
    url = f"{OPENALEX}?{urllib.parse.urlencode(params)}"
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers=UA)  # noqa: S310 (fixed https host)
            with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310
                return json.loads(r.read(5_000_000))
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and attempt < 3:
                time.sleep(10 * (attempt + 1))
                continue
            raise
    return {}


CROSSREF = "https://api.crossref.org/works"


def crossref_paper(phrase: str, min_cited: int = 5) -> dict | None:
    """Most-cited journal article (Crossref) whose title contains the phrase's words.

    Crossref is free with no credit limit (OpenAlex allows ~100 anonymous searches a day).
    """
    stems = {_stem(t) for t in topic_terms(phrase)}
    need = len(stems) if len(stems) <= 3 else 3
    params = {"query.title": phrase, "filter": "type:journal-article", "rows": 40,
              "select": "DOI,title,is-referenced-by-count,published,container-title"}
    url = f"{CROSSREF}?{urllib.parse.urlencode(params)}"
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=UA)  # noqa: S310 (fixed https host)
            with urllib.request.urlopen(req, timeout=40) as r:  # noqa: S310
                items = json.loads(r.read(10_000_000))["message"]["items"]
            break
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and attempt < 2:
                time.sleep(15 * (attempt + 1))
                continue
            raise
    best = None
    for it in items:
        title = " ".join(it.get("title") or [])
        tstems = {_stem(x) for x in re.findall(r"[a-z0-9]+", title.lower())}
        cited = it.get("is-referenced-by-count") or 0
        if len(stems & tstems) >= need and cited >= min_cited and \
                (best is None or cited > best["cited_by"]):
            year = ((it.get("published") or {}).get("date-parts") or [[None]])[0][0]
            best = {"url": f"https://doi.org/{it['DOI']}", "title": title, "year": year,
                    "cited_by": cited, "venue": " ".join(it.get("container-title") or [])}
    return best


class LookupFailed(Exception):
    """A source API could not answer (rate limit, network): not the same as 'no match'."""


def verify_wiki_titles(titles: list[str]) -> dict[str, dict | None]:
    """title → {url, title} for existing, non-disambiguation articles (50 per request)."""
    from faceless_studio.research import _wiki_get

    out: dict[str, dict | None] = {}
    uniq = list(dict.fromkeys(titles))
    for i in range(0, len(uniq), 50):
        batch = uniq[i:i + 50]
        for attempt in range(3):
            try:
                data = _wiki_get({"action": "query", "titles": "|".join(batch), "redirects": 1,
                                  "prop": "info|pageprops", "inprop": "url",
                                  "format": "json"}, 30)
                break
            except FacelessError as e:
                if "429" in str(e) and attempt < 2:
                    time.sleep(60)
                    continue
                raise
        q = data.get("query", {})
        alias = {n["from"]: n["to"] for n in q.get("normalized", [])}
        alias.update({r["from"]: r["to"] for r in q.get("redirects", [])})
        pages = {p.get("title"): p for p in q.get("pages", {}).values()}
        for t in batch:
            final = alias.get(alias.get(t, t), alias.get(t, t))
            p = pages.get(final)
            ok = p and "missing" not in p and "disambiguation" not in (p.get("pageprops") or {})
            out[t] = {"url": p["fullurl"], "title": p["title"]} if ok else None
        time.sleep(1.0)
    return out


def paper_for_phrase(phrase: str, min_cited: int = 5) -> dict | None:
    """Most-cited journal article whose title contains the phrase's words."""
    stems = {_stem(t) for t in topic_terms(phrase)}
    need = len(stems) if len(stems) <= 3 else 3
    data = _openalex({"filter": f"title.search:{phrase},has_doi:true,type:article",
                      "per-page": 25, "sort": "cited_by_count:desc",
                      "select": "doi,display_name,publication_year,cited_by_count,"
                                "primary_location"})
    for w in data.get("results", []):
        title = w.get("display_name") or ""
        doi = w.get("doi") or ""
        if not doi or any(j in doi for j in JUNK_HOSTS):
            continue
        tstems = {_stem(x) for x in re.findall(r"[a-z0-9]+", title.lower())}
        if len(stems & tstems) >= need and (w.get("cited_by_count") or 0) >= min_cited:
            src = (w.get("primary_location") or {}).get("source") or {}
            return {"url": doi, "title": title, "year": w.get("publication_year"),
                    "cited_by": w.get("cited_by_count"), "venue": src.get("display_name")}
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", type=Path,
                    default=REPO / "content/faceless_content_365_day_calendar.xlsx")
    ap.add_argument("--start", default="2026-12-01", help="date of day 1 (YYYY-MM-DD)")
    ap.add_argument("--limit", type=int, default=0, help="only the first N rows (0 = all)")
    ap.add_argument("--no-sources", action="store_true", help="only change the dates")
    # wikipedia_title may list several articles separated by ";"
    ap.add_argument("--plan", type=Path, default=REPO / "content/calendar_source_plan.tsv")
    ap.add_argument("--overwrite", action="store_true",
                    help="replace cells that contain only links this tool generated")
    args = ap.parse_args()

    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = REPO / "data" / "backups" / f"{args.file.stem}.{stamp}{args.file.suffix}"
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(args.file, backup)
    print(f"backup: {backup}")

    cache_path = REPO / "data" / "cache" / "calendar_sources_v2.json"
    cache = json.loads(cache_path.read_text()) if cache_path.is_file() else {}
    plan: dict[int, tuple[str, str]] = {}
    if args.plan.is_file():
        with open(args.plan, encoding="utf-8") as f:
            for row in csv.DictReader(f, delimiter="\t"):
                plan[int(row["day"])] = (row["wikipedia_title"].strip(),
                                         row["paper_query"].strip())
    print(f"plan: {len(plan)} days from {args.plan}")

    wb = openpyxl.load_workbook(args.file)
    ws = wb["365-day calendar"]
    head = [str(c.value or "") for c in ws[1]]
    col = {name: head.index(name) + 1 for name in head if name}
    c_day, c_date = col["Day"], col["Date (editable)"]
    c_topic = col["Video topic / working hook"]
    c_src = col["Research / sources"]

    wiki_checked: dict[str, dict | None] = {}
    if plan and not args.no_sources:
        titles = [t.strip() for w, _q in plan.values() for t in w.split(";")
                  if t.strip() and f"wiki:{t.strip()}" not in cache]
        if titles:
            print(f"verifying {len(set(titles))} Wikipedia titles …", flush=True)
            for t, v in verify_wiki_titles(titles).items():
                cache[f"wiki:{t}"] = v
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(cache, indent=1))
        wiki_checked = {t.strip(): cache.get(f"wiki:{t.strip()}")
                        for w, _q in plan.values() for t in w.split(";") if t.strip()}

    report = []
    rows = list(range(2, ws.max_row + 1))
    if args.limit:
        rows = rows[: args.limit]
    for n, r in enumerate(rows, 1):
        day = ws.cell(r, c_day).value
        topic = str(ws.cell(r, c_topic).value or "").strip()
        if day is None or not topic:
            continue
        ws.cell(r, c_date).value = (start + timedelta(days=int(day) - 1)).isoformat()
        if args.no_sources:
            continue
        existing = str(ws.cell(r, c_src).value or "").strip()
        generated = existing and all(x.startswith(("https://en.wikipedia.org/",
                                                   "https://doi.org/"))
                                     for x in existing.splitlines() if x.strip())
        entry: dict = {"wiki": None, "paper": None, "errors": []}
        if int(day) in plan:
            wtitles, phrase = plan[int(day)]
            wikis = []
            for wt in (t.strip() for t in wtitles.split(";") if t.strip()):
                if wiki_checked.get(wt):
                    wikis.append(wiki_checked[wt])
                else:
                    entry["errors"].append(f"wikipedia: '{wt}' not found or ambiguous")
            entry["wiki"] = wikis[0] if wikis else None
            entry["more_wiki"] = wikis[1:]
            key = f"paper:{phrase}"
            if key not in cache:
                try:
                    cache[key] = crossref_paper(phrase)
                    cache_path.write_text(json.dumps(cache, indent=1))
                except (OSError, ValueError, KeyError) as e:
                    # not cached: a failed lookup is retried on the next run
                    entry["errors"].append(f"paper lookup failed (retry later): {e}")
                time.sleep(0.5)
            entry["paper"] = cache.get(key)
            if key in cache and entry["paper"] is None:
                entry["errors"].append(f"paper: nothing well-matched for '{phrase}'")
        links = [e["url"] for e in (entry["wiki"], *entry.get("more_wiki", []), entry["paper"])
                 if e]
        keep = bool(existing) and not (generated and args.overwrite)
        if not keep and links:
            ws.cell(r, c_src).value = "\n".join(links)
        report.append({
            "day": day, "date": ws.cell(r, c_date).value, "topic": topic,
            "wikipedia": entry["wiki"]["url"] if entry["wiki"] else "",
            "wikipedia_title": entry["wiki"]["title"] if entry["wiki"] else "",
            "paper": entry["paper"]["url"] if entry["paper"] else "",
            "paper_title": entry["paper"]["title"] if entry["paper"] else "",
            "paper_year": entry["paper"]["year"] if entry["paper"] else "",
            "paper_cited_by": entry["paper"]["cited_by"] if entry["paper"] else "",
            "kept_your_cell": keep, "errors": "; ".join(entry["errors"]),
        })
        if n % 25 == 0:
            wb.save(args.file)
            print(f"{n}/{len(rows)} rows", flush=True)
    wb.save(args.file)

    if report:
        out = REPO / "content" / "calendar_sources_report.csv"
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(report[0].keys()))
            w.writeheader()
            w.writerows(report)
        both = sum(1 for x in report if x["wikipedia"] and x["paper"])
        wiki = sum(1 for x in report if x["wikipedia"])
        paper = sum(1 for x in report if x["paper"])
        print(f"report: {out}")
        print(f"rows: {len(report)} · Wikipedia: {wiki} · paper: {paper} · both: {both} · "
              f"none: {sum(1 for x in report if not x['wikipedia'] and not x['paper'])}")
    print(f"dates: day 1 = {start.isoformat()} … done ({date.today().isoformat()})")


if __name__ == "__main__":
    main()
