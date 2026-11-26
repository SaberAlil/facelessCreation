"""SQLite persistence for jobs, transitions, stage runs, approvals, calendar and library."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  topic TEXT NOT NULL,
  day INTEGER,
  date TEXT,
  state TEXT NOT NULL,
  failed_stage TEXT,
  error TEXT,
  created TEXT NOT NULL,
  updated TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS jobs_day ON jobs(day) WHERE day IS NOT NULL;

CREATE TABLE IF NOT EXISTS transitions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT NOT NULL REFERENCES jobs(id),
  ts TEXT NOT NULL,
  from_state TEXT,
  to_state TEXT NOT NULL,
  note TEXT
);

CREATE TABLE IF NOT EXISTS stage_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT NOT NULL REFERENCES jobs(id),
  stage TEXT NOT NULL,
  status TEXT NOT NULL,           -- ok | failed
  input_hash TEXT,
  output_hash TEXT,
  started TEXT NOT NULL,
  duration_s REAL,
  device TEXT,
  peak_rss_mib REAL,
  peak_children_rss_mib REAL,
  peak_vram_used_mib REAL,
  fallbacks TEXT,                 -- JSON list
  details TEXT,                   -- JSON object
  error TEXT
);
CREATE INDEX IF NOT EXISTS stage_runs_job ON stage_runs(job_id, stage, id);

CREATE TABLE IF NOT EXISTS approvals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT NOT NULL REFERENCES jobs(id),
  kind TEXT NOT NULL,             -- script | assets | captions | final
  artifact_hash TEXT NOT NULL,
  reviewer TEXT NOT NULL,
  ts TEXT NOT NULL,
  notes TEXT,
  checklist TEXT                  -- JSON
);

CREATE TABLE IF NOT EXISTS calendar (
  day INTEGER PRIMARY KEY,
  date TEXT UNIQUE,
  topic TEXT NOT NULL,
  original_topic TEXT NOT NULL,
  source_file TEXT NOT NULL,
  source_sheet TEXT,
  source_row INTEGER NOT NULL,
  extra TEXT                      -- JSON of other columns
);

CREATE TABLE IF NOT EXISTS library (
  id TEXT PRIMARY KEY,
  sha256 TEXT NOT NULL UNIQUE,
  data TEXT NOT NULL              -- LibraryAsset JSON
);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as c:
            c.executescript(SCHEMA)
            c.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    # ------------------------------------------------------------------ helpers
    def one(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        with self.connect() as c:
            row = c.execute(sql, params).fetchone()
            return dict(row) if row else None

    def all(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self.connect() as c:
            return [dict(r) for r in c.execute(sql, params).fetchall()]

    def execute(self, sql: str, params: tuple = ()) -> int:
        with self.connect() as c:
            cur = c.execute(sql, params)
            return cur.lastrowid or cur.rowcount


def loads(value: str | None, default: Any = None) -> Any:
    return json.loads(value) if value else default
