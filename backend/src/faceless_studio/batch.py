"""Sequential batch preparation over calendar ranges. Never approves or publishes."""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from rich.console import Console

from .calendar import calendar_range
from .pipeline import Gate, Pipeline
from .util import FacelessError

log = logging.getLogger("faceless")


def _parse_days(days: str) -> tuple[int, int]:
    try:
        a, _, b = days.partition("-")
        return int(a), int(b or a)
    except ValueError as e:
        raise FacelessError("--days must look like 1-7") from e


def plan_batch(pl: Pipeline, date_from: str | None, date_to: str | None, days: str | None,
               limit: int) -> list[dict]:
    if days:
        lo, hi = _parse_days(days)
        rows = calendar_range(pl.studio.db, day_from=lo, day_to=hi)
    elif date_from or date_to:
        rows = calendar_range(pl.studio.db, start=date_from, end=date_to)
    else:
        raise FacelessError("give --from/--to dates or --days")
    if not rows:
        raise FacelessError("no calendar rows in that range",
                            hint="import a calendar: faceless calendar import FILE")
    existing = {j["day"]: j for j in pl.studio.list_jobs() if j["day"] is not None}
    plan = []
    for r in rows[:limit]:
        plan.append({"day": r["day"], "date": r["date"], "topic": r["topic"],
                     "job": existing.get(r["day"])})
    return plan


def _research_for(research_dir: Path | None, day: int) -> Path | None:
    if research_dir is None:
        return None
    for name in (f"day-{day:03d}.json", f"day{day:03d}.json", f"day-{day}.json"):
        p = research_dir / name
        if p.is_file():
            return p
    return None


def process_day(pl: Pipeline, item: dict, resume_jobs: bool, llm: bool,
                research_dir: Path | None) -> tuple[str, Gate | None, str | None]:
    """Create (or continue) the job for one calendar day and advance it to a gate."""
    job = item["job"]
    if job is None:
        row = item
        created = pl.studio.create_job(row["topic"], row["day"], row["date"],
                                       {"batch": True})
        jid = created.id
    else:
        jid = job["id"]
        if not resume_jobs:
            return jid, None, "exists (skipped: --no-resume)"
    try:
        gate = pl.next_gate(jid)
        if gate.state.value == "DRAFT":
            rpath = _research_for(research_dir, item["day"])
            if rpath:
                pl.set_research(jid, rpath)
            if llm and (rpath or pl.research(jid).sources):
                pl.draft(jid, use_llm=True)
            return jid, pl.next_gate(jid), None
        return jid, pl.advance(jid), None
    except FacelessError as e:
        return jid, None, f"{type(e).__name__}: {e}"


def run_batch(pl: Pipeline, console: Console, date_from: str | None, date_to: str | None,
              days: str | None, limit: int, dry_run: bool, resume_jobs: bool, llm: bool,
              research_dir: Path | None, concurrency: int = 1) -> list[dict]:
    plan = plan_batch(pl, date_from, date_to, days, limit)
    console.print(f"batch: {len(plan)} day(s); paid API spend: $0.00 (no paid providers "
                  "are used; Pexels is free and optional)")
    for it in plan:
        j = it["job"]
        console.print(f"  day {it['day']} {it['date'] or ''} — {it['topic']}  "
                      + (f"[dim]{j['id']} {j['state']}[/]" if j else "[dim]new[/]"))
    if dry_run:
        console.print("[yellow]dry run: nothing created or rendered[/]")
        return []
    if concurrency < 1:
        raise FacelessError("--concurrency must be >= 1")
    if concurrency > 1:
        console.print("[yellow]concurrency > 1: GPU stages and renders still run one at a "
                      "time; watch RAM (Kokoro/Whisper each need 1-2 GB)[/]")
    results: list[dict] = []
    t0 = time.monotonic()

    def work(it: dict) -> dict:
        t = time.monotonic()
        jid, gate, error = process_day(pl, it, resume_jobs, llm, research_dir)
        return {"day": it["day"], "job": jid, "gate": gate, "error": error,
                "seconds": round(time.monotonic() - t, 1)}

    if concurrency == 1:
        for it in plan:
            res = work(it)
            results.append(res)
            _report(console, res)
    else:
        # Renders are serialized by a process-wide lock inside the pipeline workers.
        with ThreadPoolExecutor(max_workers=concurrency) as ex:
            for res in ex.map(work, plan):
                results.append(res)
                _report(console, res)
    failures = sum(1 for r in results if r["error"])
    console.print(f"batch finished in {time.monotonic() - t0:.0f}s machine time; "
                  f"{failures} failure(s). Nothing was approved or published.")
    return results


def _report(console: Console, r: dict) -> None:
    if r["error"]:
        console.print(f"[red]day {r['day']} {r['job']}: {r['error']}[/]")
    elif r["gate"]:
        g = r["gate"]
        console.print(f"day {r['day']} {r['job']}: [bold]{g.state.value}[/] — {g.reason} "
                      f"({r['seconds']}s)\n    next: {g.next_command}")
