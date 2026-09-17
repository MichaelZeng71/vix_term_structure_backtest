#!/usr/bin/env python3
"""Thesis-rule VX futures simulation over bid/ask snapshots from db.py.

2026-09-16: signal changed from slope mean-reversion to the thesis rule.
The slope versions are preserved in git history; the experiment now tests
the thesis strategy forward.
2026-09-17: Miao corrected the trading universe — the thesis trades ALL
months, not the front month alone. Each contract gets its own signal from
the same per-snapshot fit, so the book can be long some months and short
others (often close to neutral). Front-month-only versions are preserved
in git history.

Signal (thesis 'prediction vs book' rule): each snapshot fits the
Dupoyet-Daigler-Chen model F(V,dt) = V*A^dt + B*(1-A^dt) by nonlinear least
squares to spot VIX (dtm=0) + the 8 monthly futures Last prices (t=1
cross-section, thesis primary spec). For EVERY contract: target = +1 when
that contract's predicted price > its ask (lift the ask), -1 when its
predicted price < its bid (hit the bid), else 0 (flat).

Fills at the touch (ask to buy, bid to sell) + $2 fees per flip per
contract; the spread is in the fill price, not the cost line.
Mark-to-market on each contract's Last. 1 contract per month signal;
$1000 per point. A reversal (long<->short) counts as 2 flips.
Round-trip win rate is net of each trip's 2 flips.

Snapshots where the fit fails, or where NO contract has a book, are
skipped. Contracts without a book inside a usable snapshot simply get a
flat (0) target for that snapshot.

V1 'close-only': one signal per day from the LAST snapshot of each day,
held from one day's close to the next; P&L on each contract's Last change.
V2 'intraday': evaluated at EVERY snapshot; all positions flattened at
each day's last snapshot (no overnight).

There is no separate dead-band variant: each contract's bid/ask spread is
the no-trade band. A fixed 0.05-point buffer was briefly tested as V3/V4
and removed per Miao on 2026-09-16 — the thesis rule (pred > ask to buy,
pred < bid to sell) is traded exactly. The `band` parameter on the run
functions defaults to 0.0 and exists only for future sensitivity checks.

Run:  python3 simulate.py
"""
import sys
import re
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import db

USD_PER_POINT = 1000.0
FLIP_FEE_USD = 2.0  # per flip per contract; spread is captured by filling at the touch
FLIP_FEE_POINTS = FLIP_FEE_USD / USD_PER_POINT

# No hardcoded dead-band: each contract's bid/ask spread is the no-trade band.
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


def normalize_label(label: str) -> str:
    """Map label variants ('OCT 2026', 'OCT 26', 'Oct') to 'Oct'.

    A few stored snapshots used Barchart-style labels; the engine works
    with canonical three-letter month labels.
    """
    m = re.match(r"\s*([A-Za-z]+)", label or "")
    if not m:
        return label
    canon = m.group(1)[:3].title()
    return canon if canon in MONTHS else label


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
# Snapshot parsing + thesis signal (per contract, all months)
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


def thesis_targets(snap: dict, band: float = 0.0):
    """Thesis-rule targets for one snapshot, per contract.

    Returns (targets, infos) where both are dicts keyed by month label and
    infos[label] carries pred/bid/ask/last/A/B/rmse for that contract, or
    (None, None) when the snapshot is unusable (fit failed, or no contract
    has a book). Contracts without a book get target 0.
    """
    curve = snap["curve"]
    # normalize label variants ('OCT 2026' -> 'Oct'); keep first on collision
    norm_curve = {}
    for lab, val in curve.items():
        nl = normalize_label(lab)
        if nl not in norm_curve:
            norm_curve[nl] = val
    labels = [lab for lab in norm_curve if normalize_label(lab) in MONTHS]
    snap_date = date.fromisoformat(snap["ts_pt"][:10])
    # chronological contract order (contract_ym resolves the year)
    labels.sort(key=lambda lab: contract_ym(lab, snap_date))
    if not labels:
        return None, None
    lasts, dtms = [], []
    for lab in labels:
        lasts.append(_last(norm_curve, lab))
        y, m = contract_ym(lab, snap_date)
        dtms.append((vx_expiry(y, m) - snap_date).days)
    fit = fit_ddc(snap["vix"], lasts, dtms)
    if fit is None:
        return None, None
    A, B, rmse = fit
    targets, infos = {}, {}
    any_book = False
    for lab, dtm in zip(labels, dtms):
        pred = ddc_price(snap["vix"], A, B, max(0.0, dtm))
        q = _quote(norm_curve, lab)
        last_px = _last(norm_curve, lab)
        if q is None or not (q[1] > 0 and q[2] >= q[1]):
            targets[lab] = 0
            infos[lab] = {"pred": pred, "bid": None, "ask": None,
                          "last": last_px, "A": A, "B": B, "rmse": rmse,
                          "fit_ok": True, "has_book": False}
            continue
        any_book = True
        _, bid, ask = q
        if pred - ask > band:
            target = 1
        elif bid - pred > band:
            target = -1
        else:
            target = 0
        targets[lab] = target
        infos[lab] = {"pred": pred, "bid": bid, "ask": ask,
                      "last": last_px, "A": A, "B": B, "rmse": rmse,
                      "fit_ok": True, "has_book": True}
    if not any_book:
        return None, None
    return targets, infos


def _book(info: dict):
    """(bid, ask) for fills; falls back to Last when a contract has no book."""
    if info.get("has_book"):
        return info["bid"], info["ask"]
    return info["last"], info["last"]


# --------------------------------------------------------------------------
# Bookkeeping: per-contract positions, flip fees, round trips, drawdown
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
    """Shared bookkeeping across all contracts. Fills at the touch;
    fees per flip per contract. Equity/drawdown are portfolio-wide."""

    def __init__(self):
        self.pos = {}        # label -> -1/0/1
        self.open_trip = {}  # label -> (entry_price, pos)
        self.flips = 0
        self.eq_points = 0.0
        self.peak_points = 0.0
        self.maxdd_points = 0.0
        self.trips = []
        self.day_pnl = {}

    def _bump_dd(self):
        if self.eq_points > self.peak_points:
            self.peak_points = self.eq_points
        dd = self.peak_points - self.eq_points
        if dd > self.maxdd_points:
            self.maxdd_points = dd

    def set_target(self, label: str, target: int, date: str,
                   close_px: float, open_px: float = None):
        """Move one contract to target: close any open trip at close_px,
        open at open_px."""
        cur = self.pos.get(label, 0)
        if target == cur:
            return
        if open_px is None:
            open_px = close_px
        if cur != 0:
            entry, p = self.open_trip[label]
            self.trips.append(p * (close_px - entry) - 2 * FLIP_FEE_POINTS)
            del self.open_trip[label]
        flips = 1 if (cur == 0 or target == 0) else 2
        self.flips += flips
        cost = flips * FLIP_FEE_POINTS
        self.eq_points -= cost
        self.day_pnl[date] = self.day_pnl.get(date, 0.0) - cost
        self._bump_dd()
        if target != 0:
            self.open_trip[label] = (open_px, target)
        self.pos[label] = target

    def accrue(self, label: str, d_points: float, date: str):
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
    """Snapshots with computable thesis targets, in time order."""
    rows, skipped = [], 0
    for s in snaps:
        targets, infos = thesis_targets(s, band)
        if targets is None:
            skipped += 1
            continue
        rows.append((s, targets, infos))
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
    note = f"{skipped} snapshot(s) skipped: no usable fit/book" if skipped else ""
    return f"{note}; {extra}".strip("; ") if extra else note


# --------------------------------------------------------------------------
# Versions
# --------------------------------------------------------------------------
def _apply_targets(sim, targets, infos, d, flat=False):
    """Set per-contract targets; when flat=True force every target to 0."""
    for lab, target in targets.items():
        tgt = 0 if flat else target
        bid, ask = _book(infos[lab])
        cpx, opx = fill_prices(sim.pos.get(lab, 0), tgt, bid, ask)
        sim.set_target(lab, tgt, d, cpx, opx)


def run_v1(snaps, band=0.0, version="v1_close_only"):
    """Close-only thesis rule, all months: signal from each day's last
    snapshot; each contract held to the next day's close; P&L on each
    contract's Last change."""
    rows, skipped = usable_snaps(snaps, band)
    by_date = {}
    for s, targets, infos in rows:  # ascending; last snapshot of the day wins
        by_date[s["ts_pt"][:10]] = (s, targets, infos)
    days = sorted(by_date.items())
    n_days = len(days)
    if n_days < 1:
        return base_result(version, 0, 0,
                           note=skip_note(skipped, "insufficient data: no usable snapshots"))
    sim = Sim()
    for i, (d, (s, targets, infos)) in enumerate(days):
        _apply_targets(sim, targets, infos, d)
        if i < n_days - 1:  # hold to next day's close
            nxt_infos = days[i + 1][1][2]
            for lab in targets:
                if lab in nxt_infos and sim.pos.get(lab, 0) != 0:
                    sim.accrue(lab,
                               sim.pos[lab] * (nxt_infos[lab]["last"] - infos[lab]["last"]),
                               days[i + 1][0])
    note = skip_note(skipped)
    open_n = sum(1 for p in sim.pos.values() if p != 0)
    if open_n:
        note = (note + "; " if note else "") + f"{open_n} open position(s) at end (marked on Last)"
    return sim.result(version, n_days, len(rows),
                      per_day_table(sim.day_pnl, [d for d, _ in days]), note)


def run_v2(snaps, band=0.0, version="v2_intraday"):
    """Intraday thesis rule, all months: every snapshot; all positions
    flattened at each day's last snapshot (no overnight)."""
    rows, skipped = usable_snaps(snaps, band)
    n = len(rows)
    if n < 1:
        return base_result(version, 0, 0,
                           note=skip_note(skipped, "insufficient data: no usable snapshots"))
    dates = [s["ts_pt"][:10] for s, _, _ in rows]
    n_days = len(set(dates))
    sim = Sim()
    for i, (s, targets, infos) in enumerate(rows):
        d = dates[i]
        last_of_day = (i == n - 1) or (dates[i + 1] != d)
        _apply_targets(sim, targets, infos, d, flat=last_of_day)
        if i < n - 1:
            nxt_infos = rows[i + 1][2]
            # no overnight: positions are 0 across day boundaries by construction
            for lab in targets:
                if lab in nxt_infos and sim.pos.get(lab, 0) != 0:
                    sim.accrue(lab,
                               sim.pos[lab] * (nxt_infos[lab]["last"] - infos[lab]["last"]),
                               d)
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
