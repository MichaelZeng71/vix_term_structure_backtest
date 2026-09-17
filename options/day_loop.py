#!/usr/bin/env python3
"""Day-level walk-forward loop: the options analog of the futures V1 sim.

Each trading day, evaluate ONE mechanical candidate — an ATM call calendar
(short near-expiry call / long far-expiry call, strike = spot ATM on entry
day) — and hold it `hold_days` calendar days. Entry and exit are priced
with the bid/ask execution model (costs.py) under 0.5x/1x/2x spread
assumptions plus fees, both ways. Cohorts whose exit leg is missing from
the exit-day chain are SKIPPED, never invented.

Input: a directory of per-day canonical chain CSVs (one file per date,
`date` column present), as produced by fixture_gen.generate_chain_series
or by a real multi-day pull. Files are ingested through ingest.py so the
same validation applies.

Run:
    python3 day_loop.py --dir <daily chain dir> [--hold-days 5]

This is harness mechanics + cost sensitivity on synthetic data — it proves
the loop runs and quantifies the spread drag; it cannot discover an edge.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import costs
import ingest
from costs import calendar_spread_legs, price_trade

MULT = costs.MULTIPLIER


def _pick_calendar(chain: pd.DataFrame, date: dt.date,
                   min_near_dte: int = 7) -> list[dict] | None:
    """ATM call calendar legs for one day. None if expirations/strike unusable."""
    spot = float(chain["underlying"].iloc[0])
    exps = sorted(e for e in chain["expiration"].unique()
                  if (e - date).days >= min_near_dte)
    if len(exps) < 2:
        return None
    near, far = exps[0], exps[1]
    near_calls = chain[(chain["expiration"] == near) & (chain["right"] == "C")]
    far_calls = chain[(chain["expiration"] == far) & (chain["right"] == "C")]
    if near_calls.empty or far_calls.empty:
        return None
    # Trade the strike closest to spot that exists on BOTH expirations
    # (real strike grids differ by tenor).
    common = sorted(set(near_calls["strike"]) & set(far_calls["strike"]))
    if not common:
        return None  # strike grids do not overlap
    strike = float(min(common, key=lambda k: abs(k - spot)))
    return calendar_spread_legs(near, far, strike, "C", 1), near, far, strike, spot


def _fill(chain: pd.DataFrame, legs: list[dict], mult: float) -> float:
    """Net cash received (+) / paid (-) to execute `legs` at spread mult `mult`.

    Reuses costs.price_trade at a single multiplier; negates because a
    calendar entry is normally a net debit while price_trade reports credit.
    """
    res = price_trade(chain, legs, spread_mults=(mult,))
    return res["scenarios"][f"spread_{mult}x"]["net_credit_usd"]


def run_day_loop(chain_dir: str | Path, hold_days: int = 5,
                 spread_mults: tuple[float, ...] = (0.5, 1.0, 2.0),
                 min_near_dte: int = 7) -> dict:
    """Walk-forward: enter one ATM call calendar each day, hold, exit.

    Returns a report dict with per-scenario P&L, cohort ledger, skips.
    """
    chain_dir = Path(chain_dir)
    files = sorted(p for p in chain_dir.glob("*.csv")
                   if not p.name.startswith("_"))
    if not files:
        raise FileNotFoundError(f"no chain CSVs in {chain_dir}")

    by_date: dict[dt.date, pd.DataFrame] = {}
    for f in files:
        chain, rep = ingest.ingest_chain(str(f))
        d = chain["date"].iloc[0]
        by_date[d] = chain

    dates = sorted(by_date)
    cohorts = []   # (entry_date, exit_date, near, far, strike, spot, legs, exit_legs)
    skipped = []
    for d in dates:
        pick = _pick_calendar(by_date[d], d, min_near_dte)
        if pick is None:
            skipped.append((str(d), "no usable near/far expirations or ATM strike"))
            continue
        legs, near, far, strike, spot = pick
        exit_d = d + dt.timedelta(days=hold_days)
        if exit_d not in by_date:
            skipped.append((str(d), f"exit date {exit_d} beyond series end"))
            continue
        exit_chain = by_date[exit_d]
        # closing legs: buy back the near call, sell the far call
        exit_legs = [
            {"expiration": near, "strike": strike, "right": "C", "side": "buy", "qty": 1},
            {"expiration": far, "strike": strike, "right": "C", "side": "sell", "qty": 1},
        ]
        cohorts.append({"entry": d, "exit": exit_d, "near": near, "far": far,
                        "strike": strike, "spot": spot, "legs": legs,
                        "exit_legs": exit_legs})

    # Price every cohort under each execution assumption, entry and exit.
    ledger = []
    totals = {m: 0.0 for m in spread_mults}
    mid_total = 0.0
    wins = {m: 0 for m in spread_mults}
    entry_mid_total = 0.0
    for c in cohorts:
        ch_in, ch_out = by_date[c["entry"]], by_date[c["exit"]]
        try:
            entry_mid = price_trade(ch_in, c["legs"], spread_mults=(0.0,))["scenarios"]["spread_0.0x"]["net_credit_usd"]
            exit_mid = price_trade(ch_out, c["exit_legs"], spread_mults=(0.0,))["scenarios"]["spread_0.0x"]["net_credit_usd"]
        except costs.CostModelError as e:
            skipped.append((str(c["entry"]), f"leg missing on exit chain: {e}"))
            continue
        pnl_mid = entry_mid + exit_mid  # entry usually negative (debit)
        mid_total += pnl_mid
        entry_mid_total += -entry_mid
        row = {"entry": str(c["entry"]), "exit": str(c["exit"]),
               "strike": c["strike"], "spot": round(c["spot"], 2),
               "pnl_mid_usd": round(pnl_mid, 2)}
        for m in spread_mults:
            try:
                e = _fill(ch_in, c["legs"], m)
                x = _fill(ch_out, c["exit_legs"], m)
            except costs.CostModelError as err:
                row[f"pnl_{m}x_usd"] = None
                continue
            pnl = round(e + x, 2)
            row[f"pnl_{m}x_usd"] = pnl
            totals[m] += pnl
            if pnl > 0:
                wins[m] += 1
        ledger.append(row)

    n = len(ledger)
    # spread_0.0x = mid minus fees only: isolate the pure spread drag
    return {
        "days": len(dates),
        "cohorts": n,
        "skipped": skipped,
        "hold_days": hold_days,
        "pnl_mid_usd": round(mid_total, 2),
        "pnl_by_mult_usd": {f"{m}x": round(totals[m], 2) for m in spread_mults},
        "win_rate_by_mult": {f"{m}x": (round(wins[m] / n, 4) if n else None)
                             for m in spread_mults},
        "avg_cost_drag_per_cohort_usd": round(
            (mid_total - totals[1.0]) / n, 2) if n else None,
        "avg_entry_premium_usd": round(entry_mid_total / n, 2) if n else None,
        "ledger": ledger,
        "note": ("Synthetic data. spread mult m prices entry AND exit at "
                 "mid +/- m*half-spread + fees. mid P&L ignores spread but "
                 "keeps fees; the gap to 1.0x is the execution drag."),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Day-level ATM call calendar walk-forward.")
    ap.add_argument("--dir", required=True, help="directory of per-day chain CSVs")
    ap.add_argument("--hold-days", type=int, default=5)
    a = ap.parse_args()
    r = run_day_loop(a.dir, hold_days=a.hold_days)
    print(f"days: {r['days']}  cohorts: {r['cohorts']}  skipped: {len(r['skipped'])}")
    print(f"mid P&L (fees only): {r['pnl_mid_usd']:+.2f} USD")
    for k in sorted(r["pnl_by_mult_usd"]):
        wr = r["win_rate_by_mult"][k]
        wrs = "n/a" if wr is None else f"{wr * 100:.1f}%"
        print(f"  {k} spread: {r['pnl_by_mult_usd'][k]:+.2f} USD   win rate {wrs}")
    print(f"avg cost drag / cohort (mid -> 1x): {r['avg_cost_drag_per_cohort_usd']} USD")
    print(f"avg entry premium / cohort: {r['avg_entry_premium_usd']} USD")
    if r["skipped"]:
        for d, why in r["skipped"][:5]:
            print(f"  skipped {d}: {why}")
    print(f"note: {r['note']}")


if __name__ == "__main__":
    main()
