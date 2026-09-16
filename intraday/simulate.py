#!/usr/bin/env python3
"""Thesis-rule VX futures simulation over screenshot snapshots from db.py.

2026-09-16: signal changed from slope mean-reversion to the thesis rule.
The slope versions are preserved in git history; the experiment now tests
the thesis strategy forward.

Signal (thesis 'prediction > ask' rule): each snapshot fits the
Dupoyet-Daigler-Chen model F(V,dt) = V*A^dt + B*(1-A^dt) by nonlinear least
squares to spot VIX (dtm=0) + the 8 monthly futures Last prices (t=1
cross-section, thesis primary spec). Let pred = model price for the front
month. Target = +1 when pred > ask (lift the ask), -1 when pred < bid (hit
the bid), else 0 (flat).

Fills at the touch (ask to buy, bid to sell) + $4 fees per flip; the spread
is in the fill price, not the cost line. Mark-to-market on Last.
1 contract; $1000 per point. A reversal (long<->short) counts as 2 flips.
Round-trip win rate is net of each trip's 2 flips.

Snapshots without front-month bid/ask (Last-only legacy rows) are skipped:
the thesis rule cannot be evaluated without the book.

V1 'close-only': one signal per day from the LAST snapshot of each day,
held from one day's close to the next; P&L on front-month Last change.
V2 'intraday': evaluated at EVERY snapshot; flattened at each day's last
snapshot (no overnight).

There is no separate dead-band variant: the bid/ask spread itself is the
no-trade band. A fixed 0.05-point buffer was briefly tested as V3/V4 and
removed per Miao on 2026-09-16 — the thesis rule (pred > ask to buy,
pred < bid to sell) is traded exactly. The `band` parameter on the run
functions defaults to 0.0 and exists only for future sensitivity checks.

Run:  python3 simulate.py
"""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import db

USD_PER_POINT = 1000.0
FLIP_FEE_USD = 4.0  # per flip; spread is captured by filling at the touch
FLIP_FEE_POINTS = FLIP_FEE_USD / USD_PER_POINT

# No hardcoded dead-band: the bid/ask spread itself is the no-trade band.
# The `band` parameter on the run functions stays at its default 0.0 so an
# extra buffer can be reintroduced later as a sensitivity check, but no
# variant uses it — per Miao, the thesis rule is traded exactly.

MONTHS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


# --------------------------------------------------------------------------
# VX expiry calendar: the Wednesday 30 calendar days before the third Friday
# of the month immediately following the contract month.
# --------------------------------------------------------------------------
def vx_expiry(contract_year: int, contract_month: int) -> date:
    y, m = contract_year, contract_month + 1
    if m > 12:
        m, y = 1, y + 1
    first = date(y, m, 1)
    third_friday = first + timedelta(days=(4 - first.weekday()) % 7 + 14)
    return third_friday - timedelta(days=30)


def contract_ym(label: str, snap_date: date):
    m = MONTHS[label]
    y = snap_date.year if m >= snap_date.month else snap_date.year + 1
    return y, m


# --------------------------------------------------------------------------
# Dupoyet-Daigler-Chen fit by Nelder-Mead (stdlib only).
# --------------------------------------------------------------------------
def ddc_price(spot: float, A: float, B: float, dtm: float) -> float:
    return spot * (A ** dtm) + B * (1.0 - A ** dtm)


def _nelder_mead(obj, x0, step, max_iter=1000, tol=1e-12):
    n = len(x0)
    simplex = [list(x0)]
    for i in range(n):
        p = list(x0)
        p[i] += step[i]
        simplex.append(p)
    vals = [obj(p) for p in simplex]
    for _ in range(max_iter):
        order = sorted(range(n + 1), key=lambda i: vals[i])
        simplex = [simplex[i] for i in order]
        vals = [vals[i] for i in order]
        # centroid of all but worst
        centroid = [sum(simplex[i][j] for i in range(n)) / n for j in range(n)]
        worst, best = simplex[n], simplex[0]
        # reflect
        xr = [centroid[j] + (centroid[j] - worst[j]) for j in range(n)]
        fr = obj(xr)
        if fr < vals[0]:
            xe = [centroid[j] + 2.0 * (xr[j] - centroid[j]) for j in range(n)]
            fe = obj(xe)
            simplex[n], vals[n] = (xe, fe) if fe < fr else (xr, fr)
        elif fr < vals[n - 1]:
            simplex[n], vals[n] = xr, fr
        else:
            if fr < vals[n]:
                simplex[n], vals[n] = xr, fr
            # contract
            xc = [centroid[j] + 0.5 * (simplex[n][j] - centroid[j]) for j in range(n)]
            fc = obj(xc)
            if fc < vals[n]:
                simplex[n], vals[n] = xc, fc
            else:
                # shrink
                for i in range(1, n + 1):
                    simplex[i] = [best[j] + 0.5 * (simplex[i][j] - best[j]) for j in range(n)]
                    vals[i] = obj(simplex[i])
        spread = max(abs(vals[i] - vals[0]) for i in range(1, n + 1))
        if spread < tol:
            break
    return simplex[0], vals[0]


def fit_ddc(spot: float, lasts: list, dtms: list):
    """Fit (A, B) by NLS to spot + futures Last prices. Returns (A, B, rmse)
    or None on failure."""
    if spot <= 0 or len(lasts) < 2:
        return None
    prices = [spot] + list(lasts)
    ds = [0.0] + [max(0.0, d) for d in dtms]

    def sse(p):
        A, B = p
        if not (1e-4 < A < 1.0 - 1e-9 and 1e-2 <= B <= 300.0):
            return 1e18
        err = 0.0
        for y, d in zip(prices, ds):
            err += (ddc_price(spot, A, B, d) - y) ** 2
        return err

    try:
        (A, B), best = _nelder_mead(sse, [0.99, 20.0], [0.005, 2.0])
    except Exception:
        return None
    if best >= 1e18 or not (1e-4 < A < 1.0 - 1e-9 and 1e-2 <= B <= 300.0):
        return None
    rmse = (best / len(prices)) ** 0.5
    return A, B, rmse


# --------------------------------------------------------------------------
# Snapshot parsing + thesis signal
# --------------------------------------------------------------------------
def _quote(curve: dict, label: str):
    """Return (last, bid, ask) for a month label; None if legacy Last-only."""
    v = curve[label]
    if isinstance(v, dict):
        try:
            return float(v["last"]), float(v["bid"]), float(v["ask"])
        except (KeyError, TypeError, ValueError):
            return None
    return None  # legacy plain-float row: no book


def _last(curve: dict, label: str) -> float:
    v = curve[label]
    return float(v["last"]) if isinstance(v, dict) else float(v)


def thesis_signal(snap: dict, band: float = 0.0):
    """Thesis-rule target for one snapshot.

    Returns (target, info) where info carries pred/bid/ask/A/B/rmse, or
    (None, None) when the snapshot lacks a front-month book (skipped).
    """
    curve = snap["curve"]
    labels = list(curve.keys())
    if not labels:
        return None, None
    front = labels[0]
    q = _quote(curve, front)
    if q is None:
        return None, None
    last_f, bid, ask = q
    if not (bid > 0 and ask >= bid):
        return None, None
    snap_date = date.fromisoformat(snap["ts_pt"][:10])
    lasts, dtms = [], []
    for lab in labels:
        lasts.append(_last(curve, lab))
        y, m = contract_ym(lab, snap_date)
        dtms.append((vx_expiry(y, m) - snap_date).days)
    fit = fit_ddc(snap["vix"], lasts, dtms)
    if fit is None:
        return 0, {"pred": None, "bid": bid, "ask": ask, "last": last_f,
                   "A": None, "B": None, "rmse": None, "fit_ok": False}
    A, B, rmse = fit
    pred = ddc_price(snap["vix"], A, B, max(0.0, dtms[0]))
    if pred - ask > band:
        target = 1
    elif bid - pred > band:
        target = -1
    else:
        target = 0
    return target, {"pred": pred, "bid": bid, "ask": ask, "last": last_f,
                    "A": A, "B": B, "rmse": rmse, "fit_ok": True}


# --------------------------------------------------------------------------
# Bookkeeping: position, flip fees, round trips, drawdown
# --------------------------------------------------------------------------
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
    """Shared bookkeeping. Fills at the touch; fees per flip."""

    def __init__(self):
        self.pos = 0
        self.flips = 0
        self.eq_points = 0.0
        self.peak_points = 0.0
        self.maxdd_points = 0.0
        self.trips = []
        self.open_trip = None  # (entry_price, pos)
        self.day_pnl = {}

    def _bump_dd(self):
        if self.eq_points > self.peak_points:
            self.peak_points = self.eq_points
        dd = self.peak_points - self.eq_points
        if dd > self.maxdd_points:
            self.maxdd_points = dd

    def set_target(self, target: int, date: str, close_px: float, open_px: float = None):
        """Move to target: close any open trip at close_px, open at open_px."""
        if target == self.pos:
            return
        if open_px is None:
            open_px = close_px
        if self.pos != 0:
            entry, p = self.open_trip
            self.trips.append(p * (close_px - entry) - 2 * FLIP_FEE_POINTS)
            self.open_trip = None
        flips = 1 if (self.pos == 0 or target == 0) else 2
        self.flips += flips
        cost = flips * FLIP_FEE_POINTS
        self.eq_points -= cost
        self.day_pnl[date] = self.day_pnl.get(date, 0.0) - cost
        self._bump_dd()
        if target != 0:
            self.open_trip = (open_px, target)
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


def fill_prices(cur_pos: int, target: int, bid: float, ask: float):
    """(close_px, open_px) for a position transition, filling at the touch."""
    if target == 1:
        open_px = ask
    elif target == -1:
        open_px = bid
    else:
        open_px = None
    if cur_pos == 1:
        close_px = bid
    elif cur_pos == -1:
        close_px = ask
    else:
        close_px = open_px
    return close_px, open_px


def usable_snaps(snaps, band):
    """Snapshots with a computable thesis signal, in time order."""
    rows, skipped = [], 0
    for s in snaps:
        target, info = thesis_signal(s, band)
        if target is None:
            skipped += 1
            continue
        rows.append((s, target, info))
    return rows, skipped


def per_day_table(day_pnl, days):
    return [
        {
            "date": d,
            "pnl_points": round(day_pnl.get(d, 0.0), 6),
            "pnl_usd": round(day_pnl.get(d, 0.0) * USD_PER_POINT, 2),
        }
        for d in days
    ]


def skip_note(skipped, extra=""):
    note = f"{skipped} snapshot(s) skipped: Last-only, no bid/ask" if skipped else ""
    return f"{note}; {extra}".strip("; ") if extra else note


# --------------------------------------------------------------------------
# Versions
# --------------------------------------------------------------------------
def run_v1(snaps, band=0.0, version="v1_close_only"):
    """Close-only thesis rule: signal from each day's last snapshot."""
    rows, skipped = usable_snaps(snaps, band)
    by_date = {}
    for s, target, info in rows:  # ascending; last snapshot of the day wins
        by_date[s["ts_pt"][:10]] = (s, target, info)
    days = sorted(by_date.items())
    n_days = len(days)
    if n_days < 1:
        return base_result(version, 0, 0,
                           note=skip_note(skipped, "insufficient data: no usable snapshots"))
    sim = Sim()
    for i, (d, (s, target, info)) in enumerate(days):
        cpx, opx = fill_prices(sim.pos, target, info["bid"], info["ask"])
        sim.set_target(target, d, cpx, opx)
        if i < n_days - 1:  # hold to next day's close; P&L on Last change
            nxt = days[i + 1][1][2]
            sim.accrue(sim.pos * (nxt["last"] - info["last"]), days[i + 1][0])
    note = skip_note(skipped)
    if sim.pos != 0:
        note = (note + "; " if note else "") + "1 open position at end (marked on Last)"
    return sim.result(version, n_days, len(rows),
                      per_day_table(sim.day_pnl, [d for d, _ in days]), note)


def run_v2(snaps, band=0.0, version="v2_intraday"):
    """Intraday thesis rule: every snapshot, flattened at each day's close."""
    rows, skipped = usable_snaps(snaps, band)
    n = len(rows)
    if n < 1:
        return base_result(version, 0, 0,
                           note=skip_note(skipped, "insufficient data: no usable snapshots"))
    dates = [s["ts_pt"][:10] for s, _, _ in rows]
    n_days = len(set(dates))
    sim = Sim()
    for i, (s, target, info) in enumerate(rows):
        d = dates[i]
        last_of_day = (i == n - 1) or (dates[i + 1] != d)
        tgt = 0 if last_of_day else target
        cpx, opx = fill_prices(sim.pos, tgt, info["bid"], info["ask"])
        sim.set_target(tgt, d, cpx, opx)
        if i < n - 1:
            nxt = rows[i + 1][2]
            # no overnight: position is 0 across day boundaries by construction
            sim.accrue(sim.pos * (nxt["last"] - info["last"]), d)
    note = skip_note(skipped)
    if band:
        note = (note + "; " if note else "") + f"dead-band {band} pts"
    return sim.result(version, n_days, n,
                      per_day_table(sim.day_pnl, sorted(set(dates))), note)


# --------------------------------------------------------------------------
def main():
    snaps = db.get_snapshots()
    print(f"loaded {len(snaps)} snapshot(s) from {db.DB_PATH}\n")
    for fn in (run_v1, run_v2):
        r = fn(snaps)
        wr = "n/a" if r["win_rate"] is None else f"{r['win_rate'] * 100:.1f}%"
        print(f"--- {r['version']} ---")
        print(f"snapshots: {r['n_snaps']}  days: {r['n_days']}  "
              f"trades: {r['n_trades']}  flips: {r['n_flips']}")
        print(f"P&L: {r['total_pnl_points']:+.4f} pts  ({r['total_pnl_usd']:+.2f} USD)")
        print(f"max drawdown: {r['max_drawdown_usd']:.2f} USD   win rate: {wr}")
        if r["note"]:
            print(f"note: {r['note']}")
        if r["per_day_pnl"]:
            per = "  ".join(f"{d['date']}:{d['pnl_usd']:+.0f}" for d in r["per_day_pnl"])
            print(f"per-day P&L (USD): {per}")
        print()


if __name__ == "__main__":
    main()
