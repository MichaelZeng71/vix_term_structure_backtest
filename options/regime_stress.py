#!/usr/bin/env python3
"""Regime stress test: ATM call calendar walk-forward under 3 synthetic IV regimes.

Harness robustness check + calendar P&L response to IV regime — mechanics
validation, not edge discovery (synthetic surface has no smile/vol-of-vol).

Run:  python3 regime_stress.py
"""
import sys
import datetime as dt
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fixture_gen
from day_loop import run_day_loop

OUT = Path(__file__).resolve().parent / "hidden_files" / "regime_stress"

REGIMES = [
    ("iv_down", dict(spot0=30.0, long_run_spot=15.0, mean_reversion=0.12, seed=11)),
    ("iv_up",   dict(spot0=12.0, long_run_spot=30.0, mean_reversion=0.12, seed=12)),
    ("calm",    dict(spot0=17.0, long_run_spot=17.0, mean_reversion=0.25,
                      daily_vol=0.02, seed=13)),
]

N_DAYS = 40
HOLD_DAYS = 5


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for name, kw in REGIMES:
        spots, chains = fixture_gen.generate_chain_series("2026-08-01", N_DAYS, **kw)
        d = OUT / name
        d.mkdir(exist_ok=True)
        for day, grp in chains.groupby("date"):
            grp.to_csv(d / f"{day}.csv", index=False)
        r = run_day_loop(d, hold_days=HOLD_DAYS)
        print(f"== {name}: days={r['days']} cohorts={r['cohorts']} "
              f"skipped={len(r['skipped'])}")
        print(f"   mid:    {r['pnl_mid_usd']:+.2f} USD")
        for m, v in r["pnl_by_mult_usd"].items():
            print(f"   {m:6s}: {v:+.2f} USD  win={r['win_rate_by_mult'][m]}")
        print(f"   avg cost drag/cohort: {r['avg_cost_drag_per_cohort_usd']} USD  "
              f"avg entry premium: {r['avg_entry_premium_usd']} USD")


if __name__ == "__main__":
    main()
