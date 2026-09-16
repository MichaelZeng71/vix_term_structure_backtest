#!/usr/bin/env python3
"""Two-version VX futures simulation over screenshot snapshots from db.py.

Universe: front-month VX = first key of the curve dict (insertion order);
slope = F2 - F1. Costs: 1-tick spread ($50) + $4 fees = $54 per flip;
1 contract; $1000 per point. A reversal (long<->short) counts as 2 flips.
Round-trip win rate is net of each trip's 2 flips.

V1 'close-only': one signal per day from the LAST snapshot of each day.
    position = -sign(slope - trailing_median(slope, 3 days)), flat for the
    first 3 days (warmup). Held from one day's close to the next; P&L on
    front-month price change.
V2 'intraday': evaluated at EVERY snapshot against the trailing median of
    slope over the last 20 snapshots (flat until 20-snapshot warmup); trades
    only on sign flips of the target position; flattened at each day's last
    snapshot (no overnight).
V3 'intraday + dead-band': V2 logic, but the target must clear a no-trade
    buffer (Phase 2c analog of the thesis's 'prediction > ask' rule): only
    enter/flip when |slope - trailing median| exceeds DEADBAND_PTS; flatten
    when the signal falls back inside the band.
V4 'close-only + dead-band': V1 logic with the same dead-band applied to the
    daily signal (3-day median warmup).

Run:  python3 simulate.py
"""
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import db

USD_PER_POINT = 1000.0
FLIP_COST_USD = 54.0  # $50 one-tick spread + $4 fees
FLIP_COST_POINTS = FLIP_COST_USD / USD_PER_POINT

V1_WARMUP_DAYS = 3
V2_WARMUP_SNAPS = 20

# Dead-band half-width on the (slope - trailing median) signal, in price points.
# Data are delayed VolChart mids with no bid/ask, so the thesis Phase 2c
# 'prediction > ask' rule is implemented as: the prediction must beat the
# reference median by more than this buffer before entering/flipping, and the
# position flattens when the signal falls back inside the band.
# Default 0.05 = one VX tick ($50 at $1000/pt) — the full assumed spread in
# the cost model — and ~1.8x the observed typical 30-min |Δslope| (~0.028
# from early VolChart snapshots), covering the observed noise envelope.
DEADBAND_PTS = 0.05


def sign(x: float) -> int:
    return 1 if x > 0 else (-1 if x < 0 else 0)


def deadband_target(signal_raw: float, band: float = DEADBAND_PTS) -> int:
    """Phase 2c target: +1 when the prediction is below the reference by more
    than the band, -1 when above by more than the band, 0 (flat) inside."""
    if signal_raw > band:
        return -1
    if signal_raw < -band:
        return 1
    return 0


def f1_f2(curve: dict):
    keys = list(curve.keys())
    f1 = curve[keys[0]]
    f2 = curve[keys[1]] if len(keys) > 1 else f1
    return f1, f2


def base_result(version, n_days, n_snaps, note=""):
    return {
        "version": version,
        "n_days": n_days,
        "n_snaps": n_snaps,
        "n_trades": 0,
        "n_flips": 0,
        "total_pnl_points": 0.0,
        "total_pnl_usd": 0.0,
        "max_drawdown_usd": 0.0,
        "win_rate": None,
        "per_day_pnl": [],
        "note": note,
    }


class Sim:
    """Shared bookkeeping: position, flip costs, round trips, drawdown."""

    def __init__(self):
        self.pos = 0
        self.flips = 0
        self.eq_points = 0.0  # net equity, in points
        self.peak_points = 0.0
        self.maxdd_points = 0.0
        self.trips = []  # net pnl (points) per completed round trip
        self.open_trip = None  # (entry_f1, pos)
        self.day_pnl = {}  # date -> net points

    def _bump_dd(self):
        if self.eq_points > self.peak_points:
            self.peak_points = self.eq_points
        dd = self.peak_points - self.eq_points
        if dd > self.maxdd_points:
            self.maxdd_points = dd

    def set_target(self, target: int, price: float, date: str):
        """Move to target at `price`; charges flips; maintains round trips."""
        if target == self.pos:
            return
        if self.pos != 0:  # close the open trip at this price
            entry, p = self.open_trip
            self.trips.append(p * (price - entry) - 2 * FLIP_COST_POINTS)
            self.open_trip = None
        flips = 1 if (self.pos == 0 or target == 0) else 2
        self.flips += flips
        cost = flips * FLIP_COST_POINTS
        self.eq_points -= cost
        self.day_pnl[date] = self.day_pnl.get(date, 0.0) - cost
        self._bump_dd()
        if target != 0:
            self.open_trip = (price, target)
        self.pos = target

    def accrue(self, d_points: float, date: str):
        if d_points:
            self.eq_points += d_points
            self.day_pnl[date] = self.day_pnl.get(date, 0.0) + d_points
            self._bump_dd()

    def result(self, version, n_days, n_snaps, per_day, note=""):
        r = base_result(version, n_days, n_snaps, note)
        r["n_trades"] = len(self.trips)
        r["n_flips"] = self.flips
        r["total_pnl_points"] = round(self.eq_points, 6)
        r["total_pnl_usd"] = round(self.eq_points * USD_PER_POINT, 2)
        r["max_drawdown_usd"] = round(self.maxdd_points * USD_PER_POINT, 2)
        r["win_rate"] = (
            round(sum(1 for t in self.trips if t > 0) / len(self.trips), 4)
            if self.trips
            else None
        )
        r["per_day_pnl"] = per_day
        return r


def run_v1(snaps):
    """Close-only: daily signal from each day's last snapshot."""
    by_date = {}
    for s in snaps:  # ascending; last snapshot of the day wins
        by_date[s["ts_pt"][:10]] = s
    days = sorted(by_date.items())
    n_days, n_snaps = len(days), len(snaps)
    if n_days < 2:
        return base_result(
            "v1_close_only", n_days, n_snaps,
            note="insufficient data: need at least 2 daily closes",
        )
    f1s, slopes = [], []
    for _, s in days:
        f1, f2 = f1_f2(s["curve"])
        f1s.append(f1)
        slopes.append(f2 - f1)

    sim = Sim()
    for i, (date, _) in enumerate(days):
        if i < V1_WARMUP_DAYS:
            target = 0
        else:
            med = statistics.median(slopes[i - V1_WARMUP_DAYS : i])
            target = -sign(slopes[i] - med)
        sim.set_target(target, f1s[i], date)
        if i < n_days - 1:  # hold to next day's close; P&L attributed to that day
            sim.accrue(sim.pos * (f1s[i + 1] - f1s[i]), days[i + 1][0])

    per_day = [
        {
            "date": d,
            "pnl_points": round(sim.day_pnl.get(d, 0.0), 6),
            "pnl_usd": round(sim.day_pnl.get(d, 0.0) * USD_PER_POINT, 2),
        }
        for d, _ in days
    ]
    note = ""
    if sim.pos != 0:
        note = "1 open position at end (marked to market through last close)"
    elif n_days <= V1_WARMUP_DAYS:
        note = f"all {n_days} day(s) inside {V1_WARMUP_DAYS}-day warmup: flat"
    return sim.result("v1_close_only", n_days, n_snaps, per_day, note)


def run_v2(snaps):
    """Intraday: signal at every snapshot, flattened at each day's close."""
    n = len(snaps)
    dates = [s["ts_pt"][:10] for s in snaps]
    n_days = len(set(dates))
    if n < 2:
        return base_result(
            "v2_intraday", n_days, n,
            note="insufficient data: need at least 2 snapshots",
        )
    f1s, slopes = [], []
    for s in snaps:
        f1, f2 = f1_f2(s["curve"])
        f1s.append(f1)
        slopes.append(f2 - f1)

    sim = Sim()
    for i in range(n):
        date = dates[i]
        last_of_day = (i == n - 1) or (dates[i + 1] != date)
        if last_of_day or i < V2_WARMUP_SNAPS:
            target = 0
        else:
            med = statistics.median(slopes[i - V2_WARMUP_SNAPS : i])
            target = -sign(slopes[i] - med)
        sim.set_target(target, f1s[i], date)
        if i < n - 1:
            # no overnight: position is 0 across day boundaries by construction
            sim.accrue(sim.pos * (f1s[i + 1] - f1s[i]), date)

    per_day = [
        {
            "date": d,
            "pnl_points": round(sim.day_pnl.get(d, 0.0), 6),
            "pnl_usd": round(sim.day_pnl.get(d, 0.0) * USD_PER_POINT, 2),
        }
        for d in sorted(set(dates))
    ]
    note = ""
    if n <= V2_WARMUP_SNAPS:
        note = f"all {n} snapshot(s) inside {V2_WARMUP_SNAPS}-snapshot warmup: flat"
    return sim.result("v2_intraday", n_days, n, per_day, note)


def run_v3(snaps):
    """Intraday + dead-band: V2 logic, but the target clears a no-trade buffer.

    target = deadband_target(slope - trailing median of last 20 slopes):
    enter/flip only when the signal exceeds DEADBAND_PTS; flatten when it
    falls back inside the band. Same 20-snapshot warmup and day-end flatten
    as V2; same cost model.
    """
    n = len(snaps)
    dates = [s["ts_pt"][:10] for s in snaps]
    n_days = len(set(dates))
    if n < 2:
        return base_result(
            "v3_intraday_deadband", n_days, n,
            note="insufficient data: need at least 2 snapshots",
        )
    f1s, slopes = [], []
    for s in snaps:
        f1, f2 = f1_f2(s["curve"])
        f1s.append(f1)
        slopes.append(f2 - f1)

    sim = Sim()
    for i in range(n):
        date = dates[i]
        last_of_day = (i == n - 1) or (dates[i + 1] != date)
        if last_of_day or i < V2_WARMUP_SNAPS:
            target = 0
        else:
            med = statistics.median(slopes[i - V2_WARMUP_SNAPS : i])
            target = deadband_target(slopes[i] - med)
        sim.set_target(target, f1s[i], date)
        if i < n - 1:
            sim.accrue(sim.pos * (f1s[i + 1] - f1s[i]), date)

    per_day = [
        {
            "date": d,
            "pnl_points": round(sim.day_pnl.get(d, 0.0), 6),
            "pnl_usd": round(sim.day_pnl.get(d, 0.0) * USD_PER_POINT, 2),
        }
        for d in sorted(set(dates))
    ]
    note = f"dead-band {DEADBAND_PTS} pts"
    if n <= V2_WARMUP_SNAPS:
        note = (
            f"all {n} snapshot(s) inside {V2_WARMUP_SNAPS}-snapshot warmup: flat "
            f"(dead-band {DEADBAND_PTS} pts)"
        )
    return sim.result("v3_intraday_deadband", n_days, n, per_day, note)


def run_v4(snaps):
    """Close-only + dead-band: V1 logic with the dead-band on the daily signal.

    One signal per day from the last snapshot; target =
    deadband_target(slope - trailing median of last 3 days). Same 3-day
    warmup and daily-rebalance mechanics as V1; same cost model.
    """
    by_date = {}
    for s in snaps:  # ascending; last snapshot of the day wins
        by_date[s["ts_pt"][:10]] = s
    days = sorted(by_date.items())
    n_days, n_snaps = len(days), len(snaps)
    if n_days < 2:
        return base_result(
            "v4_close_deadband", n_days, n_snaps,
            note="insufficient data: need at least 2 daily closes",
        )
    f1s, slopes = [], []
    for _, s in days:
        f1, f2 = f1_f2(s["curve"])
        f1s.append(f1)
        slopes.append(f2 - f1)

    sim = Sim()
    for i, (date, _) in enumerate(days):
        if i < V1_WARMUP_DAYS:
            target = 0
        else:
            med = statistics.median(slopes[i - V1_WARMUP_DAYS : i])
            target = deadband_target(slopes[i] - med)
        sim.set_target(target, f1s[i], date)
        if i < n_days - 1:  # hold to next day's close; P&L attributed to that day
            sim.accrue(sim.pos * (f1s[i + 1] - f1s[i]), days[i + 1][0])

    per_day = [
        {
            "date": d,
            "pnl_points": round(sim.day_pnl.get(d, 0.0), 6),
            "pnl_usd": round(sim.day_pnl.get(d, 0.0) * USD_PER_POINT, 2),
        }
        for d, _ in days
    ]
    note = f"dead-band {DEADBAND_PTS} pts"
    if sim.pos != 0:
        note = (
            f"1 open position at end (marked to market through last close; "
            f"dead-band {DEADBAND_PTS} pts)"
        )
    elif n_days <= V1_WARMUP_DAYS:
        note = (
            f"all {n_days} day(s) inside {V1_WARMUP_DAYS}-day warmup: flat "
            f"(dead-band {DEADBAND_PTS} pts)"
        )
    return sim.result("v4_close_deadband", n_days, n_snaps, per_day, note)


def fmt(r):
    wr = "n/a" if r["win_rate"] is None else f"{r['win_rate']:.1%}"
    lines = [
        f"--- {r['version']} ---",
        f"snapshots: {r['n_snaps']}  days: {r['n_days']}  "
        f"trades: {r['n_trades']}  flips: {r['n_flips']}",
        f"P&L: {r['total_pnl_points']:+.4f} pts  ({r['total_pnl_usd']:+.2f} USD)",
        f"max drawdown: {r['max_drawdown_usd']:.2f} USD   win rate: {wr}",
    ]
    if r["note"]:
        lines.append(f"note: {r['note']}")
    if r["per_day_pnl"]:
        lines.append(
            "per-day P&L (USD): "
            + ", ".join(f"{d['date']}:{d['pnl_usd']:+.0f}" for d in r["per_day_pnl"])
        )
    return "\n".join(lines)


def main():
    snaps = db.get_snapshots()
    print(f"loaded {len(snaps)} snapshot(s) from {db.DB_PATH}\n")
    print(fmt(run_v1(snaps)))
    print()
    print(fmt(run_v2(snaps)))
    print()
    print(fmt(run_v3(snaps)))
    print()
    print(fmt(run_v4(snaps)))


if __name__ == "__main__":
    main()
