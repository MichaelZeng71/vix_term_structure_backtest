"""Phase 2, variant 3b — persistent low-turnover short + stylized call hedge.

(a) Persistent structural short: hold -1 contract in a fixed maturity slot,
    roll ONLY on expiry approach (no daily signal). Sub-variants:
      second_month : hold 2nd monthly; roll 5d before expiry
      month5       : hold 5th monthly (~4-6m zone); roll when dtm<=90
    6 execution scenarios x full window 2006-01-03 -> present.
    Roll costs (fees/slippage) are the only trades -> reported separately
    from daily mark-to-market (pl_gross).

(b) Hedge design sketch + stylized hedge: rolling 3-month 30-strike VIX
    calls, 0.25-0.5 units per short future, monthly purchase ladder, hold to
    expiry. No free long-history VIX options price data exists (checked), so
    the premium is a FIXED $ assumption (labeled ASSUMPTION); expiry payoff
    uses the panel's spot VIX as the settlement proxy (also flagged).

Outputs (results/, phase2_3b_ prefix):
    phase2_3b_persistent_daily_<variant>_<scenario>.csv
    phase2_3b_persistent_stats.json        (incl. n_rolls, rolls/yr, roll costs)
    phase2_3b_hedge_daily_<variant>_u<units>_p<premium>.csv
    phase2_3b_hedge_stats.json
    phase2_3b_equity.png
    phase2_3b_persistent_short.md         (written separately)

Educational backtest only — not financial advice, not a live track record.
"""
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE / "src"))

import numpy as np
import pandas as pd  # noqa: E402

from short_back import (run_persistent_short, stylized_call_hedge,  # noqa: E402
                        PERSISTENT_VARIANTS)
from report import compute_stats  # noqa: E402
from run_backtest import SLIP_SCENARIOS  # noqa: E402

RESULTS = BASE / "results"
FULL_START = "2006-01-03"
VARIANTS = ["second_month", "month5"]


def worst_month(daily):
    d = daily.copy()
    d["date"] = pd.to_datetime(d["date"])
    d["ym"] = d["date"].dt.to_period("M").astype(str)
    rows = []
    for ym, g in d.groupby("ym"):
        rows.append((ym, g["pv_net"].iloc[-1] / g["pv_net"].iloc[0] - 1))
    return min(rows, key=lambda r: r[1])


def episode_stats(daily):
    d = daily.copy()
    d["date"] = pd.to_datetime(d["date"])
    d = d.set_index("date")
    out = {}
    for lbl, a, b in [("2008", "2008-09-01", "2008-12-31"),
                      ("volmageddon", "2018-01-25", "2018-03-31"),
                      ("covid", "2020-02-20", "2020-04-30"),
                      ("2022", "2022-01-01", "2022-12-31")]:
        s = d.loc[a:b]
        if len(s) < 2:
            continue
        r = s["pv_net"].iloc[-1] / s["pv_net"].iloc[0] - 1
        dd = (s["pv_net"] / s["pv_net"].cummax() - 1).min()
        out[lbl] = {"return": round(float(r), 4), "max_dd": round(float(dd), 4)}
    return out


def run_persistent(panel, ext_end):
    stats = {}
    for v in VARIANTS:
        stats[v] = {}
        for name, kwargs, desc in SLIP_SCENARIOS:
            out = RESULTS / f"phase2_3b_persistent_daily_{v}_{name}.csv"
            if out.exists():
                print(f"[3b:{v}] {name} cached, skipping")
                daily = pd.read_csv(out)
                # recompute roll count from the cached frame
                n_rolls = int(daily["roll_event"].sum()) if "roll_event" in daily else -1
            else:
                print(f"[3b:{v}] {name} ({desc}) ...", flush=True)
                daily, pos, roll_dates, n = run_persistent_short(
                    panel, variant=v, start=FULL_START, end=ext_end, **kwargs)
                n_rolls = len(roll_dates)
                daily.to_csv(out, index=False)
            s = compute_stats(daily, label=f"persistent-{v} {name}")
            s["scenario"] = name
            s["variant"] = v
            yrs = (pd.to_datetime(daily["date"]).iloc[-1]
                   - pd.to_datetime(daily["date"]).iloc[0]).days / 365.25
            s["n_rolls"] = n_rolls
            s["rolls_per_year"] = round(n_rolls / yrs, 2) if yrs > 0 else None
            # roll costs == all costs (only trades are rolls + initial entry)
            s["total_roll_slippage"] = float(daily["slippage"].sum())
            s["total_roll_fees"] = float(daily["fees"].sum())
            s["total_mtm_pnl"] = float(daily["pl_gross"].sum())
            wm, wr = worst_month(daily)
            s["worst_month"] = wm
            s["worst_month_return"] = round(float(wr), 4)
            wd = daily.loc[daily["pl_net"].idxmin()]
            s["worst_day"] = str(wd["date"])
            s["worst_day_pl"] = round(float(wd["pl_net"]), 1)
            s["worst_day_pl_pct_equity"] = round(
                float(wd["pl_net"] / wd["pv_net"]), 4)
            s["episodes"] = episode_stats(daily)
            stats[v][name] = s
    (RESULTS / "phase2_3b_persistent_stats.json").write_text(
        json.dumps(stats, indent=2))
    print("wrote phase2_3b_persistent_stats.json")
    return stats


def run_hedge(panel, ext_end, pstats):
    dates_all = sorted(panel["date"].unique())
    spot_by_date = panel.groupby("date")["spot"].last().to_dict()
    hstats = {}
    for v in VARIANTS:
        hstats[v] = {}
        # unhedged baseline at slip1x
        base = pd.read_csv(
            RESULTS / f"phase2_3b_persistent_daily_{v}_slip1x.csv")
        base["date"] = base["date"].astype(str)
        for units in [0.25, 0.5]:
            for premium in [75.0, 150.0, 300.0]:
                tag = f"u{units}_p{int(premium)}"
                out = RESULTS / f"phase2_3b_hedge_daily_{v}_{tag}.csv"
                print(f"[3b hedge:{v}] units={units} premium=${premium:.0f} ...",
                      flush=True)
                h = stylized_call_hedge(base["date"].tolist(), spot_by_date,
                                        units=units, premium=premium)
                df = base[["date", "pv_net", "pl_net"]].copy()
                df = df.merge(h, left_on="date", right_index=True,
                              how="left").fillna(0.0)
                # hedged P&L: unhedged net + hedge payoff - hedge premium
                df["pl_hedged"] = (df["pl_net"] + df["hedge_payoff"]
                                   - df["hedge_premium"])
                df["pv_hedged"] = 100000.0 + df["pl_hedged"].cumsum()
                df.to_csv(out, index=False)
                s = compute_stats(
                    pd.DataFrame({
                        "date": df["date"],
                        "pv_net": df["pv_hedged"].to_numpy(),
                        "pv_gross": df["pv_hedged"].to_numpy(),
                        "pl_net": df["pl_hedged"].to_numpy(),
                        "pl_gross": df["pl_hedged"].to_numpy(),
                        "fees": np.zeros(len(df)),
                        "slippage": np.zeros(len(df)),
                    }),
                    label=f"persistent-{v} slip1x + hedge {tag}")
                s["units"] = units
                s["premium_assumption"] = premium
                s["total_hedge_premium"] = round(float(h["hedge_premium"].sum()), 1)
                s["total_hedge_payoff"] = round(float(h["hedge_payoff"].sum()), 1)
                s["n_call_lots"] = int((h["hedge_premium"] > 0).sum())
                hstats[v][tag] = s
    (RESULTS / "phase2_3b_hedge_stats.json").write_text(
        json.dumps(hstats, indent=2))
    print("wrote phase2_3b_hedge_stats.json")


def plot_hedge():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(11, 5))
    specs = [
        ("second_month", "slip1x", None, "2nd-month short, slip1x, unhedged"),
        ("second_month", "slip1x", "u0.25_p150",
         "2nd-month short + 0.25u 30-strike calls"),
        ("month5", "slip1x", None, "5th-month short, slip1x, unhedged"),
        ("month5", "slip1x", "u0.25_p150",
         "5th-month short + 0.25u 30-strike calls"),
    ]
    for v, scen, htag, label in specs:
        if htag is None:
            d = pd.read_csv(
                RESULTS / f"phase2_3b_persistent_daily_{v}_{scen}.csv")
            y = d["pv_net"]
        else:
            d = pd.read_csv(RESULTS / f"phase2_3b_hedge_daily_{v}_{htag}.csv")
            y = d["pv_hedged"]
        ax.plot(pd.to_datetime(d["date"]), y, label=label, lw=1.2)
    ax.set_title("Variant 3b: persistent short — unhedged vs stylized call hedge "
                 "(premium $150/call ASSUMPTION)")
    ax.set_ylabel("portfolio value ($)")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(RESULTS / "phase2_3b_equity.png", dpi=110)
    plt.close(fig)
    print("wrote phase2_3b_equity.png")


def main():
    panel = pd.read_csv(BASE / "data" / "processed" / "vx_panel.csv",
                        dtype={"contract": str})
    panel["date"] = panel["date"].astype(str)
    ext_end = panel["date"].max()
    print("persistent variants:", {k: v["desc"] for k, v in
                                   PERSISTENT_VARIANTS.items()})
    pstats = run_persistent(panel, ext_end)
    run_hedge(panel, ext_end, pstats)
    plot_hedge()
    print("PHASE2_3B_DONE")


if __name__ == "__main__":
    main()
