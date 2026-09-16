#!/usr/bin/env python3
"""SQLite store for VIX futures term-structure snapshots.

DB file is resolved relative to this script's directory (so `./curve.db`
works no matter where the process is launched from).

Table `snapshots`:
    ts_pt      TEXT PRIMARY KEY   -- ISO 8601 with offset, e.g. 2026-09-16T06:30:00-07:00
    vix        REAL               -- VIX index value at snapshot time
    curve_json TEXT               -- JSON object {month_label: futures_price}, front month first
    source     TEXT               -- e.g. 'volchart_screenshot', 'manual_verification'
"""
import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "curve.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    ts_pt TEXT PRIMARY KEY,
    vix REAL,
    curve_json TEXT,
    source TEXT
)
"""


def _connect():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute(SCHEMA)


def upsert_snapshot(ts_pt: str, vix: float, curve: dict, source: str) -> None:
    """Insert or replace the snapshot for ts_pt (idempotent on re-run)."""
    init_db()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO snapshots (ts_pt, vix, curve_json, source) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(ts_pt) DO UPDATE SET "
            "vix=excluded.vix, curve_json=excluded.curve_json, source=excluded.source",
            (ts_pt, vix, json.dumps(curve), source),
        )


def get_snapshots() -> list[dict]:
    """All snapshots ordered by ts_pt ascending.

    Each dict: {ts_pt, vix, curve (parsed dict, insertion order preserved), source}.
    """
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT ts_pt, vix, curve_json, source FROM snapshots ORDER BY ts_pt ASC"
        ).fetchall()
    return [
        {
            "ts_pt": r["ts_pt"],
            "vix": r["vix"],
            "curve": json.loads(r["curve_json"]),
            "source": r["source"],
        }
        for r in rows
    ]


def count() -> int:
    init_db()
    with _connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
