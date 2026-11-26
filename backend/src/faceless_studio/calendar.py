"""Content calendar import (CSV / XLSX) with header detection and strict validation."""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .db import Database
from .util import FacelessError, ValidationFailed, user_file

DAY_HEADERS = ("day", "day #", "day number", "day_no", "day no", "#", "no", "number")
DATE_HEADERS = ("date", "publish date", "publish_date", "scheduled date", "post date")
TOPIC_HEADERS = ("topic", "title", "video topic", "video title", "subject", "idea", "headline")


@dataclass
class CalendarRow:
    day: int
    date: str | None
    topic: str
    original_topic: str
    source_row: int
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class CalendarImport:
    source_file: str
    sheet: str | None
    headers: list[str]
    mapping: dict[str, str | None]
    rows: list[CalendarRow]


def _norm(h: Any) -> str:
    return re.sub(r"\s+", " ", str(h or "").strip().lower())


def _pick(headers: list[str], wanted: tuple[str, ...], override: str | None) -> str | None:
    if override:
        for h in headers:
            if _norm(h) == _norm(override):
                return h
        raise FacelessError(f"column {override!r} not found; headers: {headers}")
    normed = {_norm(h): h for h in headers}
    for w in wanted:
        if w in normed:
            return normed[w]
    for w in wanted:  # contains-match as a second pass, e.g. "Video Topic (draft)"
        for n, h in normed.items():
            if w in n and len(w) > 2:
                return h
    return None


def _to_date(v: Any) -> str | None:
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d.%m.%Y", "%Y/%m/%d", "%d %B %Y",
                "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError(f"unrecognised date {s!r} (use YYYY-MM-DD)")


def _to_day(v: Any) -> int | None:
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    if isinstance(v, float) and v.is_integer():
        return int(v)
    m = re.search(r"\d+", str(v))
    if not m:
        raise ValueError(f"unrecognised day number {v!r}")
    return int(m.group(0))


def _read_table(path: Path, sheet: str | None) -> tuple[str | None, list[list[Any]]]:
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        import openpyxl

        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            if sheet:
                if sheet not in wb.sheetnames:
                    raise FacelessError(f"sheet {sheet!r} not found; sheets: {wb.sheetnames}")
                ws = wb[sheet]
            else:
                ws = wb[wb.sheetnames[0]]
            rows = [list(r) for r in ws.iter_rows(values_only=True)]
            return ws.title, rows
        finally:
            wb.close()
    if path.suffix.lower() in (".csv", ".tsv"):
        text = path.read_text(encoding="utf-8-sig")
        dialect = csv.excel_tab if path.suffix.lower() == ".tsv" else csv.excel
        return None, [list(r) for r in csv.reader(text.splitlines(), dialect)]
    raise FacelessError(f"unsupported calendar format {path.suffix}; use .csv or .xlsx")


def list_sheets(path: Path) -> list[str]:
    import openpyxl

    wb = openpyxl.load_workbook(user_file(path), read_only=True)
    try:
        return list(wb.sheetnames)
    finally:
        wb.close()


def parse_calendar(
    path: Path,
    sheet: str | None = None,
    topic_col: str | None = None,
    date_col: str | None = None,
    day_col: str | None = None,
    max_rows: int = 2000,
) -> CalendarImport:
    path = user_file(path)
    sheet_name, table = _read_table(path, sheet)
    # Header = first row with at least one recognisable topic header
    header_idx = None
    for i, row in enumerate(table[:20]):
        names = [str(c) if c is not None else "" for c in row]
        if _pick(names, TOPIC_HEADERS, None) or (topic_col and _norm(topic_col) in
                                                   map(_norm, names)):
            header_idx = i
            break
    if header_idx is None:
        raise ValidationFailed(
            f"{path.name}: no topic column found in the first 20 rows",
            hint=f"name a column one of {TOPIC_HEADERS} or pass --topic-col",
        )
    headers = [str(c).strip() if c is not None else "" for c in table[header_idx]]
    tcol = _pick(headers, TOPIC_HEADERS, topic_col)
    dcol = _pick(headers, DATE_HEADERS, date_col)
    ncol = _pick(headers, DAY_HEADERS, day_col)
    if tcol is None:
        raise ValidationFailed(f"no topic column in {headers}")
    ti = headers.index(tcol)
    di = headers.index(dcol) if dcol else None
    ni = headers.index(ncol) if ncol else None

    errors: list[str] = []
    rows: list[CalendarRow] = []
    seen_days: dict[int, int] = {}
    seen_dates: dict[str, int] = {}
    seen_topics: dict[str, int] = {}
    data_rows = table[header_idx + 1 :]
    if len(data_rows) > max_rows:
        raise ValidationFailed(f"calendar has {len(data_rows)} rows; limit is {max_rows}")
    seq = 0
    for offset, raw in enumerate(data_rows):
        rownum = header_idx + 2 + offset  # 1-based spreadsheet row
        cells = list(raw) + [None] * (len(headers) - len(raw))
        if all(c is None or str(c).strip() == "" for c in cells):
            continue
        seq += 1
        original = "" if cells[ti] is None else str(cells[ti])
        topic = " ".join(original.split())
        try:
            day = _to_day(cells[ni]) if ni is not None else seq
            d = _to_date(cells[di]) if di is not None else None
        except ValueError as e:
            errors.append(f"row {rownum}: {e}")
            continue
        if not topic:
            errors.append(f"row {rownum}: empty topic")
            continue
        if day is None:
            errors.append(f"row {rownum}: empty day number")
            continue
        if day in seen_days:
            errors.append(f"row {rownum}: duplicate day {day} (first at row {seen_days[day]})")
            continue
        if d and d in seen_dates:
            errors.append(f"row {rownum}: duplicate date {d} (first at row {seen_dates[d]})")
            continue
        key = topic.lower()
        if key in seen_topics:
            errors.append(
                f"row {rownum}: duplicate topic {topic!r} (first at row {seen_topics[key]})")
            continue
        seen_days[day] = rownum
        if d:
            seen_dates[d] = rownum
        seen_topics[key] = rownum
        extra = {h: (c.isoformat() if isinstance(c, date | datetime) else c)
                 for h, c in zip(headers, cells, strict=False)
                 if h and h not in (tcol, dcol, ncol) and c not in (None, "")}
        rows.append(CalendarRow(day, d, topic, original, rownum, extra))
    if errors:
        shown = "\n".join(errors[:25])
        more = f"\n… and {len(errors) - 25} more" if len(errors) > 25 else ""
        raise ValidationFailed(f"{path.name}: {len(errors)} invalid rows:\n{shown}{more}")
    if not rows:
        raise ValidationFailed(f"{path.name}: no data rows")
    return CalendarImport(str(path), sheet_name, headers,
                          {"topic": tcol, "date": dcol, "day": ncol}, rows)


def store_calendar(db: Database, imp: CalendarImport, replace: bool = False) -> int:
    with db.connect() as c:
        existing = c.execute("SELECT COUNT(*) FROM calendar").fetchone()[0]
        if existing and not replace:
            raise FacelessError(
                f"a calendar with {existing} rows is already imported",
                hint="pass --replace to overwrite it (existing jobs are kept)",
            )
        c.execute("DELETE FROM calendar")
        c.executemany(
            "INSERT INTO calendar(day, date, topic, original_topic, source_file, source_sheet, "
            "source_row, extra) VALUES (?,?,?,?,?,?,?,?)",
            [(r.day, r.date, r.topic, r.original_topic, imp.source_file, imp.sheet,
              r.source_row, json.dumps(r.extra, default=str)) for r in imp.rows],
        )
    return len(imp.rows)


def calendar_entry(db: Database, day: int | None = None, on_date: str | None = None
                   ) -> dict[str, Any]:
    if day is not None:
        row = db.one("SELECT * FROM calendar WHERE day = ?", (day,))
        what = f"day {day}"
    elif on_date:
        row = db.one("SELECT * FROM calendar WHERE date = ?", (_to_date(on_date),))
        what = f"date {on_date}"
    else:
        raise FacelessError("give --day or --date")
    if not row:
        raise FacelessError(f"no calendar entry for {what}",
                            hint="import one with 'faceless calendar import FILE'")
    row["extra"] = json.loads(row["extra"] or "{}")
    return row


def calendar_range(db: Database, start: str | None = None, end: str | None = None,
                   day_from: int | None = None, day_to: int | None = None) -> list[dict]:
    if start or end:
        rows = db.all(
            "SELECT * FROM calendar WHERE date IS NOT NULL AND date >= ? AND date <= ? "
            "ORDER BY date",
            (_to_date(start) or "0000-01-01", _to_date(end) or "9999-12-31"),
        )
    else:
        rows = db.all("SELECT * FROM calendar WHERE day >= ? AND day <= ? ORDER BY day",
                      (day_from or 1, day_to or 10**6))
    for r in rows:
        r["extra"] = json.loads(r["extra"] or "{}")
    return rows
