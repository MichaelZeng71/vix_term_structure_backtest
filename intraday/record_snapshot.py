#!/usr/bin/env python3
"""Record one term-structure snapshot into SQLite AND the CSV ledger.

Idempotent on ts_pt: re-running with the same --ts upserts the DB row
(no duplicate) and skips the CSV append (no duplicate row).

Example:
    python3 record_snapshot.py --ts '2026-09-16T06:30:00-07:00' --vix 16.97 \\
        --curve '{"Oct":{"last":18.43,"bid":18.38,"ask":18.48},"Nov":18.96}' \\
        --source volchart_screenshot

Each month value is either a Last-only float (legacy) or an object with
last/bid/ask (the thesis 'prediction > ask' rule needs the book).
"""
import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import db

CSV_PATH = Path(__file__).resolve().parent.parent / "hidden_files" / "intraday-curve.csv"
CSV_HEADER = ["timestamp_pt", "vix_index", "curve_json"]


def csv_has_ts(ts: str) -> bool:
    if not CSV_PATH.exists():
        return False
    with open(CSV_PATH, newline="") as f:
        for row in csv.reader(f):
            if row and row[0] == ts:
                return True
    return False


def append_csv(ts: str, vix: float, curve: dict) -> bool:
    """Append one row; returns True if appended, False if ts already present."""
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    if csv_has_ts(ts):
        return False
    new_file = not CSV_PATH.exists()
    with open(CSV_PATH, "a", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(CSV_HEADER)
        w.writerow([ts, vix, json.dumps(curve)])
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description="Record one curve snapshot (idempotent).")
    ap.add_argument("--ts", required=True, help="ISO 8601 with offset, e.g. 2026-09-16T06:30:00-07:00")
    ap.add_argument("--vix", required=True, type=float, help="VIX index value")
    ap.add_argument("--curve", required=True, help="JSON object like '{\"Oct\":18.43,\"Nov\":18.96}'")
    ap.add_argument("--source", default="volchart_screenshot")
    a = ap.parse_args()

    curve = json.loads(a.curve)
    if not isinstance(curve, dict) or not curve:
        ap.error("--curve must be a non-empty JSON object")
    for label, v in curve.items():
        if isinstance(v, dict):
            try:
                last, bid, ask = float(v["last"]), float(v["bid"]), float(v["ask"])
            except (KeyError, TypeError, ValueError):
                ap.error(f"--curve['{label}'] must have numeric last/bid/ask")
            if not (last > 0 and bid > 0 and ask >= bid):
                ap.error(f"--curve['{label}']: need last>0, bid>0, ask>=bid")
        elif not isinstance(v, (int, float)) or v <= 0:
            ap.error(f"--curve['{label}'] must be a positive price or a last/bid/ask object")

    db.upsert_snapshot(a.ts, a.vix, curve, a.source)
    appended = append_csv(a.ts, a.vix, curve)
    print(
        f"db: upserted {a.ts} (rows now: {db.count()}) | "
        f"csv: {'appended' if appended else 'ts already present, skipped'} -> {CSV_PATH}"
    )


if __name__ == "__main__":
    main()
