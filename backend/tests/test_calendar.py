from __future__ import annotations

from datetime import date

import pytest
from conftest import SAMPLES

from faceless_studio.calendar import (
    calendar_entry,
    calendar_range,
    parse_calendar,
    store_calendar,
)
from faceless_studio.util import FacelessError, ValidationFailed


def test_sample_csv(studio):
    imp = parse_calendar(SAMPLES / "calendar.csv")
    assert imp.mapping == {"topic": "Topic", "date": "Date", "day": "Day"}
    assert len(imp.rows) == 7
    assert imp.rows[0].extra == {"Pillar": "Everyday economics"}
    store_calendar(studio.db, imp)
    assert calendar_entry(studio.db, day=3)["topic"].startswith("Why airline")
    assert calendar_entry(studio.db, on_date="2027-01-02")["day"] == 2
    assert len(calendar_range(studio.db, start="2027-01-02", end="2027-01-04")) == 3
    with pytest.raises(FacelessError, match="already imported"):
        store_calendar(studio.db, imp)
    store_calendar(studio.db, imp, replace=True)


def _xlsx(path, rows, title="Plan 2027", preamble=()):
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = title
    for r in preamble:
        ws.append(r)
    for r in rows:
        ws.append(r)
    wb.save(path)
    return path


def test_xlsx_with_title_rows_and_real_dates(tmp_path):
    p = _xlsx(tmp_path / "cal.xlsx", [
        ["Day #", "Publish Date", "Video Topic", "Hook"],
        [1, date(2027, 1, 1), "  Why   prices end in 99 ", "x"],
        [2, date(2027, 1, 2), "Why coffee has sizes", None],
        [None, None, None, None],
    ], preamble=[["The Hidden Logic — 365 day calendar"], []])
    imp = parse_calendar(p)
    assert imp.sheet == "Plan 2027"
    assert [r.topic for r in imp.rows] == ["Why prices end in 99", "Why coffee has sizes"]
    assert imp.rows[0].original_topic == "  Why   prices end in 99 "
    assert imp.rows[0].date == "2027-01-01"
    assert imp.rows[0].source_row == 4


@pytest.mark.parametrize("rows,msg", [
    ([["Day", "Topic"], [1, "A topic"], [1, "Other"]], "duplicate day"),
    ([["Day", "Date", "Topic"], [1, "2027-01-01", "A"], [2, "2027-01-01", "B"]],
     "duplicate date"),
    ([["Day", "Topic"], [1, "Same"], [2, "same"]], "duplicate topic"),
    ([["Day", "Topic"], [1, ""]], "empty topic"),
    ([["Day", "Date", "Topic"], [1, "next tuesday", "A"]], "unrecognised date"),
])
def test_rejects_bad_rows(tmp_path, rows, msg):
    p = _xlsx(tmp_path / "bad.xlsx", rows)
    with pytest.raises(ValidationFailed, match=msg):
        parse_calendar(p)


def test_missing_topic_column(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text("a,b\n1,2\n")
    with pytest.raises(ValidationFailed, match="no topic column"):
        parse_calendar(p)


def test_explicit_columns(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text("When,Idea\n2027-02-01,Alpha\n2027-02-02,Beta\n")
    imp = parse_calendar(p, topic_col="Idea", date_col="When")
    assert [(r.day, r.date) for r in imp.rows] == [(1, "2027-02-01"), (2, "2027-02-02")]


def test_batch_creates_once_and_stops_at_gates(pipeline):
    from rich.console import Console

    from faceless_studio.batch import run_batch
    store_calendar(pipeline.studio.db, parse_calendar(SAMPLES / "calendar.csv"))
    con = Console(file=open("/dev/null", "w"))  # noqa: SIM115
    res = run_batch(pipeline, con, None, None, "1-7", 3, False, True, False, None)
    assert [r["day"] for r in res] == [1, 2, 3]
    assert all(r["gate"].state.value == "DRAFT" and not r["error"] for r in res)
    again = run_batch(pipeline, con, None, None, "1-7", 7, False, True, False, None)
    assert len(pipeline.studio.list_jobs()) == 7  # no duplicate jobs for days 1-3
    assert {r["job"] for r in again[:3]} == {r["job"] for r in res}
    assert run_batch(pipeline, con, None, None, "1-7", 7, True, True, False, None) == []
