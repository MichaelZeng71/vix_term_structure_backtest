"""Phase 2, variant 3c — no-trade band (dead zone) on the daily dynamic strategy.

Miao's spec: keep the daily model refit (t=1, f=1), but do NOT trade a tenor
when |predicted_price - actual_price| <= band(dtm). If the deviation is inside
the band, HOLD the existing position (no flatten, no reversal). Back months
get wider bands so they trade less often; the front month can keep a
narrower/zero band as a control.

Band design: band_points(dtm) = base_ticks * tier_factor(dtm) * 0.05 points,
with tier factors (1, 2, 3, 5) for (<60, 60-120, 120-210, >210) days to
maturity — same structure as the slippage tier table.

Configs (all t=1, f=1, full window 2006-01-03 -> present, x 4 execution
scenarios slip0 / slip0.5x / slip1x / slip2x):
    b0        base=0  front=None   (no-band control = thesis baseline)
    b1        base=1  front=None
    b2        base=2  front=None
    b3        base=3  front=None
    b5        base=5  front=None
    b3f0      base=3  front=0      (front-month control)
    b5f1      base=5  front=1      (back wide, front narrow)

Outputs (results/, phase2_3c_ prefix):
    phase2_3c_daily_<cfg>_<scenario>.csv
    phase2_3c_stats.json            (stats + turnover + per-tenor turnover + worst month/day)
    phase2_3c_equity.png            (net equity at slip1x for key configs)
    phase2_3c_deadband.md           (report, written separately)

Run:  python3 run_phase2c.py   (from the vol-strategy directory; ~30-35 min)

Educational backtest only — not financial advice, not a live track record.
"""
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE / "src"))

import numpy as np
import pandas as pd  # noqa: E402

from backtest import run_backtest, DEADBAND_TIERS  # noqa: E402
from report import compute_stats  # noqa: E402
from run_backtest import SLIP_SCENARIOS  # noqa: E402

RESULTS = BASE / "results"
FULL_START = "2006-01-03"

# (config name, deadband kwargs, description)
BAND_CONFIGS = [
    ("b0",   dict(deadband_base_ticks=0.0),
     "no band (thesis baseline control)"),
    ("b1",   dict(deadband_base_ticks=1.0),
     "base 1 tick -> 1/2/3/5 ticks by tenor"),
    ("b2",   dict(deadband_base_ticks=2.0),
     "base 2 ticks -> 2/4/6/10 ticks by tenor"),
    ("b3",   dict(deadband_base_ticks=3.0),
     "base 3 ticks -> 3/6/9/15 ticks by tenor"),
    ("b5",   dict(deadband_base_ticks=5.0),
     "base 5 ticks -> 5/10/15/25 ticks by tenor"),
    ("b3f0", dict(deadband_base_ticks=3.0, deadband_front_ticks=0.0),
     "back-month band 3 base (6/9/15), front month bandless control"),
    ("b5f1", dict(deadband_base_ticks=5.0, deadband_front_ticks=1.0),
     "back-month band 5 base (10/15/25), front month 1 tick"),
]

SCENARIOS = [s for s in SLIP_SCENARIOS if s[0] in
             ("slip0", "slip0.5x", "slip1x", "slip2x")]

DTM_BUCKETS = [("front(<60d)", 0, 60), ("mid(60-120d)", 60, 120),
               ("back(120-210d)", 120, 210), ("far(>210d)", 210, float("inf"))]


def worst_month(daily):
    d = daily.copy()
    d["date"] = pd.to_datetime(d["date"])
    d["ym"] = d["date"].dt.to_period("M").astype(str)
    rows = []
    for ym, g in d.groupby("ym"):
        rows.append((ym, g["pv_net"].iloc[-1] / g["pv_net"].iloc[0] - 1))
    return min(rows, key=lambda r: r[1])


def tenor_turnover(panel, pos):
    """Dollar turnover and flip counts per dtm bucket.

    Fill day = decision day (k-1), bucketed by contract dtm then.
    """
    settle = panel.pivot_table(index="date", columns="contract",
                               values="settle", aggfunc="last")
    dtm = panel.pivot_table(index="date", columns="contract",
                            values="dtm", aggfunc="last")
    idx = pos.index
    dpos = pos.diff().fillna(pos.iloc[0]).abs()
    px = settle.reindex(idx).shift(1)
    bucket_id = dtm.reindex(idx).shift(1)
    out = {}
    for name, lo, hi in DTM_BUCKETS:
        mask = (bucket_id >= lo) & (bucket_id < hi)
        # unknown dtm (NaN) treated as front bucket
        if lo == 0:
            mask = mask | bucket_id.isna()
        dollar = float((dpos.where(mask, 0.0) * px).sum().sum() * 1000.0)
        flips = float(dpos.where(mask, 0.0).sum().sum())
        out[name] = {"dollar_turnover": round(dollar, 1),
                     "contract_flips": round(flips, 1)}
    return out


def main():
    panel = pd.read_csv(BASE / "data" / "processed" / "vx_panel.csv",
                        dtype={"contract": str})
    panel["date"] = panel["date"].astype(str)
    ext_end = panel["date"].max()

    stats = {}
    for cfg, db_kwargs, desc in BAND_CONFIGS:
        stats[cfg] = {"description": desc, "scenarios": {}}
        for name, kwargs, sdesc in SCENARIOS:
            out = RESULTS / f"phase2_3c_daily_{cfg}_{name}.csv"
            if out.exists():
                print(f"[3c:{cfg}] {name} cached, skipping run")
                daily = pd.read_csv(out)
                pos_path = RESULTS / f"phase2_3c_pos_{cfg}_{name}.csv.gz"
                pos = (pd.read_csv(pos_path, index_col=0, compression="gzip")
                       if pos_path.exists() else None)
            else:
                print(f"[3c:{cfg}] {name} ({sdesc}) ...", flush=True)
                daily, pos, fits, n_fits = run_backtest(
                    panel, t=1, f=1, start=FULL_START, end=ext_end,
                    **db_kwargs, **kwargs)
                daily.to_csv(out, index=False)
                pos.to_csv(RESULTS / f"phase2_3c_pos_{cfg}_{name}.csv.gz",
                           compression="gzip")
            s = compute_stats(daily, label=f"deadband-{cfg} {name}")
            s["scenario"] = name
            yrs = (pd.to_datetime(daily["date"]).iloc[-1]
                   - pd.to_datetime(daily["date"]).iloc[0]).days / 365.25
            if pos is not None:
                dpos = pos.diff().fillna(pos.iloc[0]).abs()
                n_flips = float(dpos.sum().sum())
                s["contract_flips_total"] = round(n_flips, 1)
                s["contract_flips_per_year"] = round(n_flips / yrs, 1)
                turn_dollar = float(
                    (dpos * settle_frame(panel, pos).shift(1)).sum().sum()
                    * 1000.0)
                s["dollar_turnover"] = round(turn_dollar, 1)
                s["tenor_turnover"] = tenor_turnover(panel, pos)
            s["total_slippage"] = float(daily["slippage"].sum())
            s["total_fees"] = float(daily["fees"].sum())
            s["total_mtm_gross"] = float(daily["pl_gross"].sum())
            wm, wr = worst_month(daily)
            s["worst_month"] = wm
            s["worst_month_return"] = round(float(wr), 4)
            wd = daily.loc[daily["pl_net"].idxmin()]
            s["worst_day"] = str(wd["date"])
            s["worst_day_pl"] = round(float(wd["pl_net"]), 1)
            stats[cfg]["scenarios"][name] = s
        print(f"[3c:{cfg}] done", flush=True)

    (RESULTS / "phase2_3c_stats.json").write_text(
        json.dumps(stats, indent=2))
    print("wrote results/phase2_3c_stats.json")
    plot_equity(RESULTS, ext_end)
    print("PHASE2_3C_DONE")


def settle_frame(panel, pos):
    return panel.pivot_table(index="date", columns="contract",
                             values="settle", aggfunc="last").reindex(pos.index)


def plot_equity(results_dir, ext_end):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(11, 5))
    specs = [
        ("b0", "slip1x", "no band (thesis baseline)"),
        ("b2", "slip1x", "band base 2 ticks"),
        ("b3", "slip1x", "band base 3 ticks"),
        ("b3f0", "slip1x", "band 3 back, front control"),
        ("b5", "slip1x", "band base 5 ticks"),
    ]
    for cfg, scen, label in specs:
        p = results_dir / f"phase2_3c_daily_{cfg}_{scen}.csv"
        if not p.exists():
            continue
        d = pd.read_csv(p)
        ax.plot(pd.to_datetime(d["date"]), d["pv_net"], label=label, lw=1.2)
    ax.set_title(f"Variant 3c: dead-band sweep, slip1x (base) net equity, "
                 f"2006-01-03 to {ext_end}")
    ax.set_ylabel("portfolio value ($) — net of fees + slippage")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(results_dir / "phase2_3c_equity.png", dpi=110)
    plt.close(fig)
    print("wrote results/phase2_3c_equity.png")


if __name__ == "__main__":
    main()
