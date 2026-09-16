#!/usr/bin/env python3
"""Validation of V3 (intraday + dead-band) and V4 (close + dead-band).

Every expectation below was computed BY HAND (see the inline traces) and is
asserted exactly against the engine's own rounded summary fields.
V1/V2 are run on the same synthetic series to demonstrate the dead-band
difference and to regression-check the originals.

Cost model: 1 flip = $54 = 0.054 pts (1 contract, $1000/pt).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from simulate import (
    run_v1, run_v2, run_v3, run_v4, deadband_target, DEADBAND_PTS, sign,
)


def snap(ts, f1, slope, day):
    return {
        "ts_pt": ts,
        "vix": 15.0,
        "curve": {"Oct": f1, "Nov": round(f1 + slope, 4)},
        "source": "synthetic",
    }


def intraday_series():
    """26 same-day snapshots. First 20: flat slope 0.50 (warmup baseline)."""
    snaps = []
    base = [("2026-01-05", "06:30", 30 * i) for i in range(26)]
    plan = {  # i -> (f1, slope); defaults (18.00, 0.50) elsewhere
        20: (18.00, 0.62),   # signal +0.12 -> enter short
        21: (18.10, 0.63),   # still > band -> hold
        22: (17.90, 0.51),   # signal +0.01 inside band -> V3 flattens; V2 holds
        23: (17.70, 0.35),   # signal -0.15 -> enter/reverse to long
        24: (18.05, 0.65),   # signal +0.15 -> reverse to short
        25: (18.00, 0.55),   # last of day -> forced flat
    }
    for i in range(26):
        f1, slope = plan.get(i, (18.00, 0.50))
        hh = 6 + (30 * i) // 60
        mm = (30 * i) % 60
        snaps.append(snap(f"2026-01-05T{hh:02d}:{mm:02d}:00-07:00", f1, slope, None))
    return snaps


def daily_series():
    """5 daily closes (one snapshot per day)."""
    days = [
        ("2026-01-05", 18.00, 0.50),
        ("2026-01-06", 18.05, 0.52),
        ("2026-01-07", 17.95, 0.53),
        ("2026-01-08", 17.90, 0.44),  # median(0.50,0.52,0.53)=0.52 -> signal -0.08 -> long
        ("2026-01-09", 18.00, 0.51),  # median(0.52,0.53,0.44)=0.52 -> signal -0.01 -> V4 flat; V1 holds
    ]
    return [snap(f"{d}T13:00:00-07:00", f1, s, None) for d, f1, s in days]


def check(name, got, want):
    for k, v in want.items():
        assert got[k] == v, f"{name}: {k}: expected {v}, got {got[k]}"
    print(f"OK  {name}: " + ", ".join(f"{k}={got[k]}" for k in want))


def main():
    assert DEADBAND_PTS == 0.05
    # unit: band edges and exact-boundary behavior
    assert deadband_target(0.06) == -1
    assert deadband_target(-0.06) == 1
    assert deadband_target(0.01) == 0
    assert deadband_target(0.0) == 0
    assert deadband_target(0.05) == 0 and deadband_target(-0.05) == 0  # strict 'more than'
    assert deadband_target(0.12) == -sign(0.12)  # consistent with V2's -sign outside band
    print("OK  deadband_target unit semantics")

    snaps = intraday_series()

    # --- V3 hand trace (points) ---
    # i20: enter short @18.00: eq=-0.054; accrue -1*(18.10-18.00)=-0.10 -> -0.154
    # i21: hold; accrue -1*(17.90-18.10)=+0.20 -> 0.046
    # i22: flatten @17.90: trip1=-1*(17.90-18.00)-2*.054=-0.008; eq=0.046-0.054=-0.008
    # i23: enter long @17.70: eq=-0.008-0.054=-0.062; accrue +1*(18.05-17.70)=+0.35 -> 0.288
    # i24: reverse to short @18.05 (2 flips): trip2=+1*(18.05-17.70)-2*.054=+0.242;
    #      eq=0.288-0.108=0.180; accrue -1*(18.00-18.05)=+0.05 -> 0.230
    # i25: flatten @18.00: trip3=-1*(18.00-18.05)-2*.054=-0.058; eq=0.230-0.054=0.176
    # flips=1+1+1+2+1=6; trips=[-0.008,+0.242,-0.058]; win=1/3
    # maxdd: peak0->-0.154 (0.154); peak0.046->-0.062 (0.108); peak0.288->0.176 (0.112)
    r3 = run_v3(snaps)
    check("v3_intraday_deadband", r3, {
        "version": "v3_intraday_deadband", "n_snaps": 26, "n_days": 1,
        "n_trades": 3, "n_flips": 6,
        "total_pnl_points": 0.176, "total_pnl_usd": 176.00,
        "max_drawdown_usd": 154.00, "win_rate": 0.3333,
    })
    per3 = {d["date"]: d["pnl_usd"] for d in r3["per_day_pnl"]}
    assert per3 == {"2026-01-05": 176.00}, per3
    print("OK  v3 per-day attribution:", per3)

    # --- V2 on the same series (regression + contrast) ---
    # V2 holds the short through i22 (signal +0.01 -> -sign=+1 wait: -sign(+0.01)=-1, hold short)
    # i22 accrue: -1*(17.70-17.90)=+0.20 -> eq=0.246
    # i23: signal -0.15 -> +1: reverse (2 flips): trip1=-1*(17.70-18.00)-2*.054=+0.192;
    #      eq=0.246-0.108=0.138; accrue +0.35 -> 0.488
    # i24: reverse to short (2 flips): trip2=+1*(18.05-17.70)-2*.054=+0.242;
    #      eq=0.488-0.108=0.380; accrue +0.05 -> 0.430
    # i25: flatten: trip3=-1*(18.00-18.05)-2*.054=-0.058; eq=0.430-0.054=0.376
    r2 = run_v2(snaps)
    check("v2_intraday (contrast)", r2, {
        "version": "v2_intraday", "n_trades": 3, "n_flips": 6,
        "total_pnl_points": 0.376, "total_pnl_usd": 376.00,
        "win_rate": 0.6667,
    })

    days = daily_series()

    # --- V4 hand trace ---
    # i0..2: warmup flat. i3: signal 0.44-0.52=-0.08 < -0.05 -> long @17.90:
    #   eq=-0.054; accrue +1*(18.00-17.90)=+0.10 -> 0.046 (day 2026-01-09)
    # i4: signal 0.51-0.52=-0.01 inside band -> flat @18.00:
    #   trip=+1*(18.00-17.90)-2*.054=-0.008; eq=0.046-0.054=-0.008
    # flips=2; trades=1; win=0.0; maxdd: 0->-0.054 (0.054), 0.046->-0.008 (0.054)
    r4 = run_v4(days)
    check("v4_close_deadband", r4, {
        "version": "v4_close_deadband", "n_snaps": 5, "n_days": 5,
        "n_trades": 1, "n_flips": 2,
        "total_pnl_points": -0.008, "total_pnl_usd": -8.00,
        "max_drawdown_usd": 54.00, "win_rate": 0.0,
    })
    per4 = {d["date"]: d["pnl_usd"] for d in r4["per_day_pnl"]}
    assert per4 == {
        "2026-01-05": 0.0, "2026-01-06": 0.0, "2026-01-07": 0.0,
        "2026-01-08": -54.00, "2026-01-09": 46.00,
    }, per4
    print("OK  v4 per-day attribution:", per4)

    # --- V1 on the same series (regression + contrast): holds long through i4 ---
    r1 = run_v1(days)
    check("v1_close_only (contrast)", r1, {
        "version": "v1_close_only", "n_trades": 0, "n_flips": 1,
        "total_pnl_points": 0.046, "total_pnl_usd": 46.00,
    })
    assert "open position" in r1["note"], r1["note"]
    print("OK  v1 holds the open long (dead-band-free behavior confirmed)")

    print("\nALL DEAD-BAND VALIDATION CHECKS PASSED")


if __name__ == "__main__":
    main()
