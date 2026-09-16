"""Demo for the options-track harness.

1. Generate a synthetic VIX chain fixture (deterministic seed).
2. Ingest it (canonical format) and print the validation report.
3. Price an example calendar spread — short near-expiry call / long
   far-expiry call, same strike — under 0.5x / 1x / 2x spread assumptions.
4. Do the same on the real ORATS Strikes-sample day (VIX rows), if present
   at ../hidden_files/options/orats_strikes_sample_vix.csv.

Run:  python test_harness.py [--orats PATH] [--out DIR]
"""

from __future__ import annotations

import argparse
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fixture_gen import generate_chain        # noqa: E402
from ingest import ingest_chain               # noqa: E402
from costs import price_trade, calendar_spread_legs  # noqa: E402


def _show(title: str, chain: pd.DataFrame, report: dict,
          near_exp, far_exp, strike: float) -> None:
    print(f"\n===== {title} =====")
    print(f"chain rows: {len(chain)}  report: {report}")
    print(f"underlying: {chain['underlying'].iloc[0]}  "
          f"expirations: {sorted(chain['expiration'].unique())}")
    legs = calendar_spread_legs(near_exp, far_exp, strike, right="C", qty=1)
    priced = price_trade(chain, legs)
    for leg in priced["legs"]:
        print(f"  {leg['side']:4s} {leg['qty']}x {leg['expiration']} "
              f"{leg['strike']:5.1f}{leg['right']}  bid {leg['bid']:.2f} / "
              f"ask {leg['ask']:.2f} (spread {leg['spread']:.2f})")
    print(f"  mid net credit: ${priced['mid_net_credit_usd']:.2f}  "
          f"fees: ${priced['fees_usd']:.2f}")
    print("  execution scenarios:")
    for name, s in priced["scenarios"].items():
        print(f"    {name:12s} net credit ${s['net_credit_usd']:8.2f}  "
              f"cost ${s['cost_vs_mid_usd']:7.2f}  "
              f"{s['cost_pct_of_premium']}% of premium")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--orats", default="",
                    help="path to ORATS strikes CSV (full sample ok)")
    ap.add_argument("--out", default="",
                    help="dir for fixture + VIX-only ORATS CSV artifacts")
    args = ap.parse_args()
    out_dir = args.out or os.path.dirname(os.path.abspath(__file__))

    # 1+2. synthetic fixture
    chain = generate_chain("2026-09-16", spot=16.97, seed=7)
    fx_path = os.path.join(out_dir, "vix_chain_synth_2026-09-16.csv")
    chain.to_csv(fx_path, index=False)
    ingested, report = ingest_chain(fx_path, format="canonical", ticker_filter=None)
    # 3. calendar on synthetic data: near=first exp, far=third exp, strike near spot
    exps = sorted(ingested["expiration"].unique())
    spot = float(ingested["underlying"].iloc[0])
    strike = float(ingested.loc[ingested["right"] == "C"]
                   .assign(d=lambda d: (d["strike"] - spot).abs())
                   .sort_values("d").iloc[0]["strike"])
    _show(f"synthetic fixture (seed=7, {fx_path})", ingested, report,
          exps[0], exps[2], strike)

    # 4. real ORATS sample, if available
    orats_src = args.orats
    if orats_src and os.path.exists(orats_src):
        vix_csv = os.path.join(out_dir, "orats_vix_2024-01-03.csv")
        df = pd.read_csv(orats_src)
        vix = df[df["ticker"] == "VIX"]
        vix.to_csv(vix_csv, index=False)
        print(f"\nwrote {len(vix)} VIX rows -> {vix_csv}")
        o_chain, o_report = ingest_chain(vix_csv, format="orats", ticker_filter="VIX")
        o_exps = sorted(o_chain["expiration"].unique())
        o_spot = float(o_chain["underlying"].iloc[0])
        o_strike = float(o_chain.loc[o_chain["right"] == "C"]
                         .assign(d=lambda d: (d["strike"] - o_spot).abs())
                         .sort_values("d").iloc[0]["strike"])
        _show("real ORATS strikes sample (2024-01-03, VIX)", o_chain, o_report,
              o_exps[0], o_exps[2], o_strike)
    else:
        print("\n(no --orats path given or file missing; skipping real-sample demo)")


if __name__ == "__main__":
    main()
