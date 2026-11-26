"""`faceless` command-line interface."""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import Annotated, Any

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table

from . import __version__
from .config import get_settings
from .jobs import Studio, default_reviewer
from .pipeline import Pipeline, find_job
from .schemas import RightsStatus
from .util import FacelessError, GateError

app = typer.Typer(help="Faceless Studio: local pipeline for original narrated vertical "
                  "documentaries.", no_args_is_help=True, add_completion=False)
calendar_app = typer.Typer(help="Import and browse the content calendar.", no_args_is_help=True)
research_app = typer.Typer(help="Record sources and verified claims for a job.",
                           no_args_is_help=True)
library_app = typer.Typer(help="Licensed local media library.", no_args_is_help=True)
cache_app = typer.Typer(help="Inspect and clean caches.", no_args_is_help=True)
tracking_app = typer.Typer(help="Publishing/analytics tracking sheet (manual entry).",
                           no_args_is_help=True)
app.add_typer(calendar_app, name="calendar")
app.add_typer(research_app, name="research")
app.add_typer(library_app, name="library")
app.add_typer(cache_app, name="cache")
app.add_typer(tracking_app, name="tracking")

console = Console()
err = Console(stderr=True)

JobArg = Annotated[str, typer.Argument(help="Job id (a unique prefix is enough)")]


def handle_errors(fn: Callable) -> Callable:
    @wraps(fn)
    def wrapper(*a: Any, **kw: Any) -> Any:
        try:
            return fn(*a, **kw)
        except FacelessError as e:
            err.print(f"[bold red]error:[/] {e}")
            if e.hint:
                err.print(f"[yellow]hint:[/] {e.hint}")
            raise typer.Exit(e.exit_code) from None
        except ValidationError as e:
            err.print(f"[bold red]invalid input:[/] {e}")
            raise typer.Exit(5) from None
        except KeyboardInterrupt:
            err.print("interrupted; job state is preserved, re-run the command to continue")
            raise typer.Exit(130) from None
    return wrapper


def _pipeline() -> Pipeline:
    return Pipeline(Studio(get_settings()))


def _job(pl: Pipeline, job_id: str) -> str:
    return find_job(pl.studio, job_id)["id"]


def _confirm(what: str, yes: bool) -> None:
    if yes:
        return
    if not sys.stdin.isatty():
        raise GateError(f"{what} needs explicit confirmation",
                        hint="re-run interactively, or pass --yes to confirm you reviewed it")
    answer = typer.prompt(f"Type APPROVE to confirm {what}", default="", show_default=False)
    if answer.strip() != "APPROVE":
        raise GateError("not approved")


def _print_outcome(o: Any) -> None:
    if isinstance(o, dict):
        console.print_json(json.dumps(o, default=str))
        return
    console.print(f"[green]✓ {o.stage}[/] in {o.metrics.get('duration_s', 0):.1f}s "
                  f"on {o.metrics.get('device')}")
    if o.fallbacks:
        console.print(f"[yellow]fallbacks:[/] {'; '.join(o.fallbacks)}")


def _next(pl: Pipeline, job_id: str) -> None:
    g = pl.next_gate(job_id)
    console.print(f"state: [bold]{g.state.value}[/] — {g.reason}")
    if g.next_command:
        console.print(f"next:  [cyan]{g.next_command}[/]")


@app.callback()
def main(verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(levelname)s %(message)s")
    for noisy in ("httpx", "httpx2", "httpcore", "httpcore2", "faster_whisper", "urllib3",
                  "huggingface_hub"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


@app.command()
def version() -> None:
    """Print the version."""
    console.print(__version__)


# ----------------------------------------------------------------- doctor


@app.command()
@handle_errors
def doctor(no_network: Annotated[bool, typer.Option(help="skip Ollama/ComfyUI checks")] = False,
           json_out: Annotated[bool, typer.Option("--json")] = False) -> None:
    """Check tools, encoders, fonts, models, providers, disk and hardware."""
    from .doctor import run_checks

    s = get_settings()
    checks = run_checks(s, network=not no_network)
    if json_out:
        console.print_json(json.dumps([c.__dict__ for c in checks]))
    else:
        t = Table(title="faceless doctor")
        for col in ("check", "status", "detail", "fix"):
            t.add_column(col)
        colors = {"ok": "green", "warn": "yellow", "fail": "red", "info": "blue"}
        for c in checks:
            t.add_row(c.name, f"[{colors[c.status]}]{c.status}[/]", c.detail, c.hint)
        console.print(t)
    if any(c.status == "fail" for c in checks):
        raise typer.Exit(2)


@app.command("config")
@handle_errors
def config_show() -> None:
    """Show the effective configuration."""
    s = get_settings()
    console.print_json(s.model_dump_json())


# --------------------------------------------------------------- calendar


@calendar_app.command("import")
@handle_errors
def calendar_import(
    file: Path,
    sheet: Annotated[str | None, typer.Option(help="XLSX sheet (default: first)")] = None,
    topic_col: Annotated[str | None, typer.Option()] = None,
    date_col: Annotated[str | None, typer.Option()] = None,
    day_col: Annotated[str | None, typer.Option()] = None,
    replace: Annotated[bool, typer.Option(help="replace an existing calendar")] = False,
    dry_run: Annotated[bool, typer.Option(help="parse and report only")] = False,
) -> None:
    """Import a CSV/XLSX calendar (detects sheet, date and topic columns)."""
    from .calendar import list_sheets, parse_calendar, store_calendar

    if file.suffix.lower() in (".xlsx", ".xlsm"):
        console.print(f"sheets: {list_sheets(file)}")
    imp = parse_calendar(file, sheet, topic_col, date_col, day_col)
    console.print(f"sheet: {imp.sheet or '-'}; headers: {imp.headers}")
    console.print(f"mapping: {imp.mapping}; rows: {len(imp.rows)}")
    for r in imp.rows[:5]:
        console.print(f"  day {r.day} {r.date or ''} — {r.topic} (row {r.source_row})")
    if dry_run:
        return
    n = store_calendar(Studio().db, imp, replace)
    console.print(f"[green]imported {n} calendar rows[/]")


@calendar_app.command("list")
@handle_errors
def calendar_list(start: Annotated[int, typer.Option("--from-day")] = 1,
                  end: Annotated[int, typer.Option("--to-day")] = 30) -> None:
    """List calendar rows with their job (if any)."""
    from .calendar import calendar_range

    st = Studio()
    jobs = {j["day"]: j for j in st.list_jobs() if j["day"] is not None}
    t = Table()
    for col in ("day", "date", "topic", "job", "state"):
        t.add_column(col)
    for r in calendar_range(st.db, day_from=start, day_to=end):
        j = jobs.get(r["day"])
        t.add_row(str(r["day"]), r["date"] or "", r["topic"], j["id"] if j else "",
                  j["state"] if j else "")
    console.print(t)


# ------------------------------------------------------------------- jobs


@app.command()
@handle_errors
def new(
    topic: Annotated[str | None, typer.Option(help="free topic (no calendar)")] = None,
    day: Annotated[int | None, typer.Option(help="calendar day number")] = None,
    on_date: Annotated[str | None, typer.Option("--date", help="calendar date YYYY-MM-DD")] = None,
    notes: str = "",
) -> None:
    """Create a job from a calendar day/date or a free topic."""
    from .calendar import calendar_entry

    st = Studio()
    if day is not None or on_date:
        row = calendar_entry(st.db, day, on_date)
        ref = {"source_file": row["source_file"], "sheet": row["source_sheet"],
               "row": row["source_row"], "original_topic": row["original_topic"],
               "extra": row["extra"]}
        job = st.create_job(row["topic"], row["day"], row["date"], ref, notes)
    elif topic:
        job = st.create_job(topic, notes=notes)
    else:
        raise FacelessError("give --day, --date or --topic")
    console.print(f"[green]created[/] {job.id}  ({job.topic})")
    console.print(f"dir: {st.paths(job.id).root}")
    _next(Pipeline(st), job.id)


@app.command("list")
@handle_errors
def list_jobs(state: Annotated[str | None, typer.Option()] = None) -> None:
    """List jobs."""
    pl = _pipeline()
    t = Table()
    for col in ("job", "day", "date", "state", "topic", "updated"):
        t.add_column(col)
    for j in pl.studio.list_jobs(state):
        t.add_row(j["id"], str(j["day"] or ""), j["date"] or "", j["state"], j["topic"][:50],
                  j["updated"])
    console.print(t)


@app.command()
@handle_errors
def status(job_id: JobArg, json_out: Annotated[bool, typer.Option("--json")] = False) -> None:
    """Show job state, stage freshness, approvals and the next step."""
    pl = _pipeline()
    jid = _job(pl, job_id)
    pl.refresh(jid)
    job = pl.studio.get_job(jid)
    fresh = {s: pl.stage_fresh(jid, s) for s in ("voice", "assets", "subtitles", "render",
                                                  "export")}
    appr = {k: pl.approval_valid(jid, k) for k in ("script", "assets", "final")}
    runs = pl.studio.stage_runs(jid)
    if json_out:
        console.print_json(json.dumps({"job": job, "fresh": fresh, "approvals": appr,
                                       "runs": runs}, default=str))
        return
    console.print(f"[bold]{jid}[/] — {job['topic']}")
    console.print(f"state: [bold]{job['state']}[/]"
                  + (f"  (failed stage: {job['failed_stage']}: {job['error']})"
                     if job["failed_stage"] else ""))
    console.print("approvals: " + ", ".join(f"{k}={'✓' if v else '✗'}" for k, v in appr.items()))
    console.print("current:   " + ", ".join(f"{k}={'✓' if v else '✗'}" for k, v in fresh.items()))
    t = Table(title="stage runs")
    for col in ("stage", "status", "started", "secs", "device", "rss MiB", "child MiB",
                "VRAM peak", "fallbacks"):
        t.add_column(col)
    for r in runs[-15:]:
        t.add_row(r["stage"], r["status"], r["started"], f"{r['duration_s'] or 0:.1f}",
                  r["device"] or "", str(r["peak_rss_mib"] or ""),
                  str(r["peak_children_rss_mib"] or ""), str(r["peak_vram_used_mib"] or ""),
                  "; ".join(r["fallbacks"])[:60])
    console.print(t)
    _next(pl, jid)


@app.command()
@handle_errors
def resume(job_id: JobArg) -> None:
    """Continue a job through automatic stages until the next human review gate."""
    pl = _pipeline()
    jid = _job(pl, job_id)
    gate = pl.advance(jid, on_step=lambda s: console.print(f"→ {s}"))
    console.print(f"stopped at: [bold]{gate.state.value}[/] — {gate.reason}")
    if gate.next_command:
        console.print(f"next: [cyan]{gate.next_command}[/]")


# --------------------------------------------------------------- research


@research_app.command("init")
@handle_errors
def research_init(job_id: JobArg) -> None:
    """Create an empty research.json for the job."""
    from .research import write_template

    pl = _pipeline()
    jid = _job(pl, job_id)
    p = pl.paths(jid).research
    write_template(p, pl.studio.job_input(jid).topic)
    console.print(f"edit {p}")


@research_app.command("add-source")
@handle_errors
def research_add_source(
    job_id: JobArg, source_id: Annotated[str, typer.Option("--id")],
    title: Annotated[str, typer.Option()], url: Annotated[str | None, typer.Option()] = None,
    publisher: str = "", published: str = "", accessed: str = "", notes: str = "",
) -> None:
    """Add a source you have read."""
    from datetime import date

    from .research import load_research, save_research
    from .schemas import Source

    pl = _pipeline()
    jid = _job(pl, job_id)
    p = pl.paths(jid).research
    r = load_research(p)
    if any(s.id == source_id for s in r.sources):
        raise FacelessError(f"source {source_id} exists")
    r.sources.append(Source(id=source_id, url=url, title=title, publisher=publisher,
                            published=published, accessed=accessed or date.today().isoformat(),
                            notes=notes))
    save_research(p, r)
    pl.refresh(jid, "research updated")
    console.print(f"added source {source_id}")


@research_app.command("add-claim")
@handle_errors
def research_add_claim(
    job_id: JobArg, claim_id: Annotated[str, typer.Option("--id")],
    text: Annotated[str, typer.Option()],
    sources: Annotated[str, typer.Option(help="comma-separated source ids")] = "",
    verified: Annotated[bool, typer.Option(help="you checked it against the sources")] = False,
) -> None:
    """Add a factual claim and the sources that support it."""
    from .research import load_research, save_research
    from .schemas import ResearchClaim

    pl = _pipeline()
    jid = _job(pl, job_id)
    p = pl.paths(jid).research
    r = load_research(p)
    r.claims.append(ResearchClaim(id=claim_id, text=text, verified=verified,
                                  source_ids=[x.strip() for x in sources.split(",") if x.strip()]))
    save_research(p, r)
    pl.refresh(jid, "research updated")
    console.print(f"added claim {claim_id}")


@research_app.command("verify")
@handle_errors
def research_verify(job_id: JobArg, claim_ids: Annotated[list[str], typer.Argument()]) -> None:
    """Mark research claims as verified by you."""
    from .research import load_research, save_research

    pl = _pipeline()
    jid = _job(pl, job_id)
    p = pl.paths(jid).research
    r = load_research(p)
    found = 0
    for c in r.claims:
        if c.id in claim_ids:
            c.verified = True
            found += 1
    if found != len(set(claim_ids)):
        raise FacelessError(f"unknown claim ids among {claim_ids}")
    save_research(p, r)
    pl.refresh(jid, "research updated")
    console.print(f"verified {found} claim(s)")


@research_app.command("fetch")
@handle_errors
def research_fetch(job_id: JobArg, url: str,
                   source_id: Annotated[str, typer.Option("--id")]) -> None:
    """Fetch a public page's title/excerpt as a source candidate (bounded, public hosts)."""
    from .research import fetch_source, load_research, save_research

    pl = _pipeline()
    jid = _job(pl, job_id)
    p = pl.paths(jid).research
    r = load_research(p)
    src = fetch_source(url, source_id)
    r.sources.append(src)
    save_research(p, r)
    pl.refresh(jid, "research updated")
    console.print(f"added {source_id}: {src.title}\n[yellow]verify the publication date and "
                  "content yourself[/]")


@research_app.command("show")
@handle_errors
def research_show(job_id: JobArg) -> None:
    """Show research and open problems."""
    from .research import research_problems

    pl = _pipeline()
    jid = _job(pl, job_id)
    r = pl.research(jid)
    console.print_json(r.model_dump_json(exclude={"sources": {"__all__": {"excerpt"}}}))
    for prob in research_problems(r):
        console.print(f"[yellow]- {prob}[/]")


# ----------------------------------------------------------------- script


@app.command()
@handle_errors
def draft(
    job_id: JobArg,
    script: Annotated[Path | None, typer.Option(help="script .json/.txt/.md")] = None,
    text: Annotated[str | None, typer.Option(help="pasted script text")] = None,
    research_file: Annotated[Path | None, typer.Option("--research")] = None,
    llm: Annotated[bool, typer.Option("--llm", help="draft with local Ollama")] = False,
    title: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Create/replace the job's script (manual or local LLM). Invalidates approvals."""
    pl = _pipeline()
    jid = _job(pl, job_id)
    o = pl.draft(jid, script, research_file, title, use_llm=llm, pasted_text=text)
    _print_outcome(o)
    d = o.details
    console.print(f"words: {d['words']}  provider: {d['provider']}")
    for w in d["errors"]:
        console.print(f"[red]error:[/] {w}")
    for w in d["warnings"]:
        console.print(f"[yellow]warning:[/] {w}")
    for w in d["flagged"]:
        console.print(f"[magenta]needs review:[/] {w}")
    _next(pl, jid)


@app.command("review-script")
@handle_errors
def review_script(job_id: JobArg) -> None:
    """Show the script, flagged claims and research problems."""
    pl = _pipeline()
    jid = _job(pl, job_id)
    rv = pl.review_script(jid)
    sc, rep = rv["script"], rv["report"]
    console.print(f"[bold]{sc.title}[/]  ({rep.words} words, ≈{rep.est_seconds}s)  "
                  f"provider: {sc.provider}")
    for s in sc.scenes:
        console.print(f"[cyan]{s.id}[/] {s.narration}")
        console.print(f"     [dim]visual: {s.visual or '-'} | terms: "
                      f"{', '.join(s.search_terms) or '-'} | overlay: {s.overlay or '-'}[/]")
    for label, items, color in (("errors", rep.errors, "red"), ("warnings", rep.warnings,
                                "yellow"), ("claims needing review", rep.flagged_claims,
                                "magenta"), ("research problems", rv["research_problems"],
                                "yellow")):
        if items:
            console.print(f"[{color}]{label}:[/]")
            for i in items:
                console.print(f"  - {i}")
    console.print(f"full report: {rv['review_file']}")
    console.print("approved: " + ("[green]yes[/]" if rv["approved"] else "no"))


@app.command("approve-script")
@handle_errors
def approve_script(
    job_id: JobArg, yes: Annotated[bool, typer.Option("--yes")] = False,
    acknowledge_flags: Annotated[bool, typer.Option(
        help="you fact-checked every flagged claim yourself")] = False,
    reviewer: Annotated[str | None, typer.Option()] = None, notes: str = "",
) -> None:
    """Approve research + script (records the script hash)."""
    pl = _pipeline()
    jid = _job(pl, job_id)
    _confirm("that you reviewed the script and its factual claims", yes)
    h = pl.approve_script(jid, reviewer or default_reviewer(), acknowledge_flags, notes)
    console.print(f"[green]script approved[/] (hash {h[:12]})")
    _next(pl, jid)


# ------------------------------------------------------------------ voice


@app.command()
@handle_errors
def voice(
    job_id: JobArg,
    provider: Annotated[str | None, typer.Option(help="kokoro | espeak (demo only)")] = None,
    narration: Annotated[Path | None, typer.Option(help="your own recorded narration")] = None,
) -> None:
    """Generate narration (Kokoro on CPU by default) and real scene timing."""
    pl = _pipeline()
    jid = _job(pl, job_id)
    o = pl.voice(jid, provider, narration)
    _print_outcome(o)
    console.print(f"duration: {o.details['duration_s']:.2f}s  cache hits: "
                  f"{o.details.get('cache_hits', 0)}")
    _next(pl, jid)


# ----------------------------------------------------------------- assets


@library_app.command("add")
@handle_errors
def library_add(
    file: Path,
    license: Annotated[str, typer.Option(help="e.g. 'Pexels License', 'CC0-1.0', 'own work'")],
    commercial_use: Annotated[bool, typer.Option(
        "--commercial-use/--no-commercial-use", help="license allows commercial use")],
    source_url: str = "", creator: str = "", license_url: str = "", attribution: str = "",
    attribution_required: bool = False, tags: str = "", title: str = "",
    ai_generated: bool = False, notes: str = "",
) -> None:
    """Add a user-owned or licensed media file with its provenance."""
    from .library import add_asset

    st = Studio()
    a = add_asset(st.db, st.settings.library_dir, file, license=license,
                  commercial_use=commercial_use, source_url=source_url, creator=creator,
                  license_url=license_url, attribution=attribution,
                  attribution_required=attribution_required,
                  tags=[t for t in tags.split(",") if t.strip()], title=title,
                  ai_generated=ai_generated, notes=notes)
    console.print(f"[green]{a.id}[/] {a.kind} {a.width}x{a.height} {a.license}")


@library_app.command("add-manifest")
@handle_errors
def library_add_manifest(manifest: Path) -> None:
    """Bulk-add media from a JSON list of {file, license, commercial_use, ...}."""
    from .library import add_from_manifest

    st = Studio()
    for a in add_from_manifest(st.db, st.settings.library_dir, manifest):
        console.print(f"{a.id} {a.kind} {a.title} ({a.license})")


@library_app.command("list")
@handle_errors
def library_list(kind: Annotated[str | None, typer.Option()] = None) -> None:
    """List library media."""
    from .library import list_assets

    t = Table()
    for col in ("id", "kind", "size", "license", "commercial", "creator", "tags"):
        t.add_column(col)
    for a in list_assets(Studio().db, kind):
        t.add_row(a.id, a.kind, f"{a.width}x{a.height}" if a.width else "", a.license,
                  "yes" if a.commercial_use else "NO", a.creator, ", ".join(a.tags))
    console.print(t)


@library_app.command("remove")
@handle_errors
def library_remove(asset_id: str) -> None:
    """Remove a library item (and its file)."""
    from .library import remove_asset

    st = Studio()
    remove_asset(st.db, st.settings.library_dir, asset_id)
    console.print(f"removed {asset_id}")


@app.command()
@handle_errors
def assets(
    job_id: JobArg,
    pexels: Annotated[bool | None, typer.Option("--pexels/--no-pexels")] = None,
    rebuild: Annotated[bool, typer.Option(help="discard manual choices")] = False,
    scene: Annotated[str | None, typer.Option(help="scene id to replace")] = None,
    use: Annotated[str | None, typer.Option(help="library id for --scene")] = None,
    procedural: Annotated[bool, typer.Option(help="use an original graphic for --scene")] = False,
    overlay: Annotated[str | None, typer.Option(help="graphic text for --procedural")] = None,
    music: Annotated[str | None, typer.Option(help="library audio id, or 'none'")] = None,
    ai_images: Annotated[bool, typer.Option(help="generate missing visuals with local "
                                            "ComfyUI (needs comfyui.enabled)")] = False,
) -> None:
    """Select visuals per scene (library → Pexels → original graphics) or replace one."""
    pl = _pipeline()
    jid = _job(pl, job_id)
    if scene:
        pl.replace_asset(jid, scene, use, procedural, overlay)
        console.print(f"replaced {scene}")
    elif music is not None:
        pl.set_music(jid, None if music == "none" else music)
        console.print(f"music: {music}")
    else:
        o = pl.assets(jid, pexels, rebuild, ai_images)
        _print_outcome(o)
        for sid, prov, ref in o.details["scenes"]:
            console.print(f"  {sid}: {prov} {ref or ''}")
        for e in o.details.get("ai_errors", []):
            console.print(f"[yellow]comfyui:[/] {e}")
        for e in o.details.get("pexels_errors", []):
            console.print(f"[yellow]pexels:[/] {e}")
    _next(pl, jid)


@app.command("review-assets")
@handle_errors
def review_assets(
    job_id: JobArg,
    confirm: Annotated[str | None, typer.Option(help="scene ids whose rights you confirmed")]
    = None,
    confirm_all: Annotated[bool, typer.Option(help="you checked every asset's license")] = False,
    reject: Annotated[str | None, typer.Option(help="scene ids to reject")] = None,
    approve: Annotated[bool, typer.Option(help="approve the asset set")] = False,
    yes: Annotated[bool, typer.Option("--yes")] = False,
    reviewer: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Review asset provenance/licenses; confirm rights; approve the set."""
    pl = _pipeline()
    jid = _job(pl, job_id)
    if confirm or confirm_all:
        ids = None if confirm_all else [x.strip() for x in (confirm or "").split(",")]
        pl.set_rights(jid, ids, RightsStatus.CONFIRMED)
    if reject:
        pl.set_rights(jid, [x.strip() for x in reject.split(",")], RightsStatus.REJECTED)
    rv = pl.asset_review(jid)
    t = Table(title=f"assets for {jid}")
    for col in ("scene", "kind", "provider", "license", "commercial", "creator / attribution",
                "rights", "file"):
        t.add_column(col)
    m = rv["manifest"]
    for a in m.scenes + ([m.music] if m.music else []):
        t.add_row(a.scene_id, a.kind, a.provider, a.license[:40],
                  "yes" if a.commercial_use else "NO",
                  (a.attribution or a.creator)[:40], a.rights.value, Path(a.path).name)
    console.print(t)
    console.print(f"previews: {pl.paths(jid).assets_dir}")
    for prob in rv["problems"]:
        console.print(f"[yellow]- {prob}[/]")
    if approve:
        _confirm("the asset set (rights and depictions checked)", yes)
        h = pl.approve_assets(jid, reviewer or default_reviewer())
        console.print(f"[green]assets approved[/] ({h[:12]})")
    _next(pl, jid)


# ------------------------------------------------------- subtitles/render


@app.command("subtitles")
@handle_errors
def subtitles_cmd(
    job_id: JobArg,
    approximate: Annotated[bool, typer.Option(
        help="fallback timing without Whisper (requires review)")] = False,
    model: Annotated[str | None, typer.Option(help="whisper model, e.g. base.en")] = None,
    import_srt: Annotated[Path | None, typer.Option("--import", help="corrected SRT")] = None,
    mark_reviewed: Annotated[bool, typer.Option(help="you checked caption timing")] = False,
) -> None:
    """Generate captions from the real narration (faster-whisper CPU INT8)."""
    pl = _pipeline()
    jid = _job(pl, job_id)
    if import_srt:
        tr = pl.import_captions(jid, import_srt)
        console.print(f"imported {len(tr.captions)} captions")
    elif mark_reviewed:
        pl.mark_captions_reviewed(jid)
        console.print("captions marked reviewed")
    else:
        o = pl.subtitles(jid, approximate, model)
        _print_outcome(o)
        d = o.details
        console.print(f"{d['captions']} captions from {d['source']}"
                      + (f", script match {d['divergence_ratio']:.1%}, {d['divergences']} "
                         "divergence(s)" if "divergence_ratio" in d else ""))
        tr = pl.captions(jid)
        for x in (tr.divergences if tr else [])[:10]:
            console.print(f"[yellow]  {x}[/]")
    p = pl.paths(jid)
    console.print(f"edit {p.srt} and re-import with --import to correct captions")
    _next(pl, jid)


@app.command()
@handle_errors
def render(
    job_id: JobArg,
    preview: Annotated[bool, typer.Option(help="fast 540x960 preview")] = False,
    dry_run: Annotated[bool, typer.Option(help="print FFmpeg commands only")] = False,
    encoder: Annotated[str | None, typer.Option(help="h264_nvenc | libx264")] = None,
) -> None:
    """Render the vertical video (1080x1920, 30 fps, H.264/AAC, faststart)."""
    pl = _pipeline()
    jid = _job(pl, job_id)
    if encoder not in (None, "h264_nvenc", "libx264"):
        raise FacelessError("encoder must be h264_nvenc or libx264")
    o = pl.render(jid, preview, dry_run, encoder)
    if isinstance(o, dict):
        import shlex
        for cmd in o["commands"]:
            console.print(shlex.join(cmd), soft_wrap=True)
        return
    _print_outcome(o)
    d = o.details
    console.print(f"{d['output']}  ({d['duration_s']:.2f}s, {d['encoder']}, "
                  f"{d['clips_reused']} cached clips)")
    _next(pl, jid)


@app.command()
@handle_errors
def preview(job_id: JobArg) -> None:
    """Shortcut for 'render --preview'."""
    render(job_id, preview=True, dry_run=False, encoder=None)


@app.command()
@handle_errors
def review(job_id: JobArg, json_out: Annotated[bool, typer.Option("--json")] = False) -> None:
    """Final review checklist: sources, rights, captions, validation, files."""
    pl = _pipeline()
    jid = _job(pl, job_id)
    rv = pl.review(jid)
    if json_out:
        console.print_json(json.dumps(rv, default=str))
        return
    console.print(f"[bold]{rv['title']}[/]  ({jid})")
    for i in rv["checklist"]:
        mark = "[green]✓[/]" if i["ok"] else ("[red]✗[/]" if i["required"] else "[yellow]![/]")
        console.print(f" {mark} {i['check']}" + (f" — {i['detail']}" if i["detail"] else ""))
    for k, v in rv["files"].items():
        console.print(f"   {k}: {v}")
    console.print("Watch the full video before approving. Check every claim, caption and "
                  "visual; confirm nothing misrepresents real people or companies.")


@app.command("approve-final")
@handle_errors
def approve_final(job_id: JobArg, yes: Annotated[bool, typer.Option("--yes")] = False,
                  reviewer: Annotated[str | None, typer.Option()] = None,
                  notes: str = "") -> None:
    """Approve the rendered video (records hashes). Refused if required checks fail."""
    pl = _pipeline()
    jid = _job(pl, job_id)
    for i in pl.final_checklist(jid):
        if i["required"] and not i["ok"]:
            break
    else:
        _confirm("that you watched the final video and it is ready to publish", yes)
    h = pl.approve_final(jid, reviewer or default_reviewer(), notes)
    console.print(f"[green]final approved[/] ({h[:12]})")
    _next(pl, jid)


@app.command()
@handle_errors
def export(job_id: JobArg) -> None:
    """Write the export package (MP4, cover, SRT, captions, sources, metadata, review)."""
    pl = _pipeline()
    jid = _job(pl, job_id)
    o = pl.export(jid)
    console.print(f"[green]exported[/] {o.details['dir']}")
    for f in o.details["files"]:
        console.print(f"  {f}")
    console.print("Publishing is manual. Read the disclosure reminder in youtube.txt.")


# ------------------------------------------------------------------ batch


@app.command()
@handle_errors
def batch(
    date_from: Annotated[str | None, typer.Option("--from", help="YYYY-MM-DD")] = None,
    date_to: Annotated[str | None, typer.Option("--to", help="YYYY-MM-DD")] = None,
    days: Annotated[str | None, typer.Option(help="day range, e.g. 1-7")] = None,
    limit: Annotated[int, typer.Option(help="max jobs this run")] = 7,
    dry_run: Annotated[bool, typer.Option(help="show the plan only")] = False,
    resume_jobs: Annotated[bool, typer.Option("--resume/--no-resume",
                                              help="continue existing jobs")] = True,
    llm: Annotated[bool, typer.Option(help="draft scripts with Ollama when research "
                                      "exists")] = False,
    research_dir: Annotated[Path | None, typer.Option(help="dir with day-NNN.json research")]
    = None,
    concurrency: Annotated[int, typer.Option(help="jobs in parallel (1 = safe default)")] = 1,
) -> None:
    """Prepare a range of calendar days sequentially, stopping at review gates."""
    from .batch import run_batch

    run_batch(_pipeline(), console, date_from, date_to, days, limit, dry_run, resume_jobs, llm,
              research_dir, concurrency)


# ------------------------------------------------------------ cache/tracking


@cache_app.command("stats")
@handle_errors
def cache_stats() -> None:
    """Disk usage of caches, jobs and library."""
    from .cachectl import usage

    for k, v in usage(get_settings()).items():
        console.print(f"{k:10s} {v / 1e6:10.1f} MB")


@cache_app.command("clean")
@handle_errors
def cache_clean(
    max_gb: Annotated[float | None, typer.Option(help="target cache size")] = None,
    dry_run: Annotated[bool, typer.Option()] = False,
) -> None:
    """Delete least-recently-used cache files and rebuildable render intermediates."""
    from .cachectl import clean

    removed, freed = clean(get_settings(), max_gb, dry_run)
    console.print(f"{'would remove' if dry_run else 'removed'} {removed} files, "
                  f"{freed / 1e6:.1f} MB")


@tracking_app.command("export")
@handle_errors
def tracking_export(out: Annotated[Path, typer.Argument()] = Path("data/tracking.csv")) -> None:
    """Write/refresh the tracking CSV (keeps your manual columns)."""
    from .export import write_tracking_csv
    from .util import read_json

    pl = _pipeline()
    rows = []
    for j in pl.studio.list_jobs():
        p = pl.paths(j["id"])
        meta = p.export_dir / "metadata.json"
        md = read_json(meta) if meta.is_file() else {}
        sc = pl.script(j["id"]) if p.script.is_file() else None
        rows.append({"job_id": j["id"], "day": j["day"] or "", "date": j["date"] or "",
                     "topic": j["topic"], "title": sc.title if sc else "", "state": j["state"],
                     "exported": md.get("exported", ""), "duration_s": md.get("duration_s", "")})
    n = write_tracking_csv(out, rows)
    console.print(f"wrote {n} rows to {out}")


@app.command("gpu")
@handle_errors
def gpu_status() -> None:
    """Show GPU memory and who holds the GPU lock."""
    from .resources import meminfo_mib, query_gpu

    s = get_settings()
    g = query_gpu()
    console.print(f"GPU: {g}" if g else "GPU: none visible")
    m = meminfo_mib()
    console.print(f"RAM available: {m.get('MemAvailable')} / {m.get('MemTotal')} MiB")
    lock = s.locks_dir / "gpu.lock"
    holder = lock.read_text().strip() if lock.is_file() else ""
    console.print(f"GPU lock: {holder or 'free'}")


if __name__ == "__main__":
    app()
