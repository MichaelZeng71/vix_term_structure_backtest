#!/usr/bin/env python3
"""Validation of the all-months thesis engine (2026-09-17).

Miao's correction: the thesis trades EVERY month, not the front month
alone — each contract gets its own pred-vs-book signal from the same
per-snapshot DDC fit, so the book can be long some months and short others.

Every expectation below was computed BY HAND (see the inline traces) and is
asserted exactly against the engine's own rounded summary fields. Curves
are built exactly on a DDC curve (spot 15.0, A=0.99, B=22.0) so the fit
recovers pred ~= last to ~1e-9; books are placed +/-0.02..0.03 around last
to force signals with wide margins.

Cost model: $4 per flip per contract = 0.004 pts (1 contract, $1000/pt).
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from simulate import (
    run_v1, run_v2, thesis_targets, fit_ddc, ddc_price, vx_expiry,
    contract_ym, FLIP_FEE_POINTS,
)

SPOT, A_TRUE, B_TRUE = 15.0, 0.99, 22.0
FEE = FLIP_FEE_POINTS  # 0.004


def dtm(snap_day: date, label: str) -> int:
    y, m = contract_ym(label, snap_day)
    return (vx_expiry(y, m) - snap_day).days


def exact_last(snap_day: date, label: str) -> float:
    return ddc_price(SPOT, A_TRUE, B_TRUE, max(0, dtm(snap_day, label)))


def book(last: float, kind: str):
    """kind: 'long' (ask below pred), 'short' (bid above pred), 'flat'."""
    if kind == "long":
        return {"last": last, "bid": last - 0.03, "ask": last - 0.02}
    if kind == "short":
        return {"last": last, "bid": last + 0.02, "ask": last + 0.03}
    return {"last": last, "bid": last - 0.03, "ask": last + 0.03}


def snap(ts: str, plan: dict, drift: dict | None = None):
    """plan: label -> book kind (or 'none' for Last-only). drift: label -> dLast."""
    day = date.fromisoformat(ts[:10])
    curve = {}
    for lab, kind in plan.items():
        last = exact_last(day, lab) + (drift.get(lab, 0.0) if drift else 0.0)
        curve[lab] = last if kind == "none" else book(last, kind)
    return {"ts_pt": ts, "vix": SPOT, "curve": curve, "source": "synthetic"}


def check(name, got, want):
    for k, v in want.items():
        assert got[k] == v, f"{name}: {k}: expected {v}, got {got[k]}"
    print(f"OK  {name}: " + ", ".join(f"{k}={got[k]}" for k in want))


def main():
    # --- fit sanity on the exact curve: pred ~= last everywhere ---
    s0 = snap("2026-09-17T13:00:00-07:00", {"Oct": "flat", "Nov": "flat"})
    t0, i0 = thesis_targets(s0)
    assert t0 == {"Oct": 0, "Nov": 0}, t0
    assert i0["Oct"]["rmse"] < 1e-6, i0["Oct"]["rmse"]
    for lab in ("Oct", "Nov"):
        assert abs(i0[lab]["pred"] - i0[lab]["last"]) < 1e-6
    print("OK  exact-DDC fit recovers pred ~= last; straddled books -> flat")

    # --- per-contract signals: long Oct, short Nov simultaneously ---
    s1 = snap("2026-09-17T13:00:00-07:00", {"Oct": "long", "Nov": "short"})
    t1, i1 = thesis_targets(s1)
    assert t1 == {"Oct": 1, "Nov": -1}, t1
    print("OK  all-months signal: Oct +1 and Nov -1 from one fit")

    # --- V1 hand trace (points) ---
    # NOTE: day2's lasts also roll one day closer to expiry (dtm shrinks),
    # so the day-2 curve is NOT day-1 lasts + drift; the trace below reads
    # the actual lasts out of the snapshots and hand-applies the engine's
    # accounting (fills at the touch, $4/flip, mark on Last).
    # Day1: enter long Oct @ ask1 = l1o-0.02; short Nov @ bid1 = l1n+0.02.
    #   eq = -2*FEE
    # Hold to day2 close: Oct accrues +(l2o-l1o); Nov accrues -(l2n-l1n).
    # Day2: flat signals -> close Oct @ bid2 = l2o-0.03, Nov @ ask2 = l2n+0.03.
    #   trip Oct = (l2o-0.03)-(l1o-0.02)-2*FEE   (> 0: l2o-l1o ~ +0.15)
    #   trip Nov = -((l2n+0.03)-(l1n+0.02))-2*FEE (> 0: l2n-l1n ~ -0.34)
    #   eq = -2*FEE + (l2o-l1o) - (l2n-l1n) - 2*FEE
    # flips = 4; maxdd: 0 -> -2*FEE => $8
    day1 = snap("2026-09-17T13:00:00-07:00", {"Oct": "long", "Nov": "short"})
    day2 = snap("2026-09-18T13:00:00-07:00", {"Oct": "flat", "Nov": "flat"},
                drift={"Oct": 0.20, "Nov": -0.30})
    l1o = day1["curve"]["Oct"]["last"]
    l1n = day1["curve"]["Nov"]["last"]
    l2o = day2["curve"]["Oct"]["last"]
    l2n = day2["curve"]["Nov"]["last"]
    exp_eq = -2 * FEE + (l2o - l1o) - (l2n - l1n) - 2 * FEE
    exp_d2 = (l2o - l1o) - (l2n - l1n) - 2 * FEE
    exp_dd = round(2 * FEE * 1000, 2)  # peak 0 -> trough -2*FEE
    r1 = run_v1([day1, day2])
    check("v1 all-months", r1, {
        "version": "v1_close_only", "n_snaps": 2, "n_days": 2,
        "n_trades": 2, "n_flips": 4,
        "total_pnl_points": round(exp_eq, 6),
        "total_pnl_usd": round(exp_eq * 1000, 2),
        "max_drawdown_usd": exp_dd, "win_rate": 1.0,
    })
    per1 = {d["date"]: d["pnl_usd"] for d in r1["per_day_pnl"]}
    assert per1 == {"2026-09-17": -round(2 * FEE * 1000, 2),
                    "2026-09-18": round(exp_d2 * 1000, 2)}, per1
    print("OK  v1 per-day attribution:", per1)

    # --- V2 hand trace (same day, 2 snapshots) ---
    # Snap1: enter long Oct @ P_Oct-0.02, short Nov @ P_Nov+0.02 -> eq=-2*FEE
    # Accrue to snap2: Oct +0.10, Nov +0.10 -> eq = 0.20-2*FEE
    # Snap2 (last of day): force flat; close Oct @ P_Oct+0.07, Nov @ P_Nov-0.07.
    #   trip Oct = 0.09-2*FEE; trip Nov = 0.09-2*FEE
    #   eq = 0.20-4*FEE; flips = 4
    m1 = snap("2026-09-17T06:30:00-07:00", {"Oct": "long", "Nov": "short"})
    m2 = snap("2026-09-17T13:00:00-07:00", {"Oct": "flat", "Nov": "flat"},
              drift={"Oct": 0.10, "Nov": -0.10})
    r2 = run_v2([m1, m2])
    check("v2 all-months", r2, {
        "version": "v2_intraday", "n_snaps": 2, "n_days": 1,
        "n_trades": 2, "n_flips": 4,
        "total_pnl_points": round(0.20 - 4 * FEE, 6),
        "total_pnl_usd": round((0.20 - 4 * FEE) * 1000, 2),
        "max_drawdown_usd": round(2 * FEE * 1000, 2), "win_rate": 1.0,
    })

    # --- skip rules ---
    # All Last-only -> snapshot unusable (no book anywhere).
    bare = snap("2026-09-17T13:00:00-07:00", {"Oct": "none", "Nov": "none"})
    assert thesis_targets(bare) == (None, None)
    r = run_v1([bare])
    assert r["n_snaps"] == 0 and "skipped" in r["note"], r
    print("OK  all-Last-only snapshot skipped")
    # Mixed: Oct has a book (long), Nov Last-only -> Oct trades, Nov flat.
    mixed = snap("2026-09-17T13:00:00-07:00", {"Oct": "long", "Nov": "none"})
    tm, im = thesis_targets(mixed)
    assert tm == {"Oct": 1, "Nov": 0}, tm
    assert im["Nov"]["has_book"] is False
    print("OK  mixed book: only the quoted contract trades")

    print("\nALL ALL-MONTHS VALIDATION CHECKS PASSED")


if __name__ == "__main__":
    main()
