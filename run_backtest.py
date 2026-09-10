"""Top-level runner: validation (2006-2013) + extension (2013-present)
+ slippage/TAS sensitivity (t=1,f=1, both windows).

Usage:
    python3 run_backtest.py                 # full: panel -> validation -> extension -> slippage
    python3 run_backtest.py --skip-panel     # reuse existing data/processed/vx_panel.csv
    python3 run_backtest.py --only validation
    python3 run_backtest.py --only slippage  # t=1,f=1 execution-cost scenarios only

Slippage scenarios (t=1,f=1; see src/slippage.py for the ASSUMPTION tier table):
    slip0    no slippage (thesis baseline; $2/contract fee only)
    slip0.5x tiered adverse slippage at 0.5x (optimistic)
    slip1x   tiered adverse slippage at 1.0x (base case: half-spread)
    slip2x   tiered adverse slippage at 2.0x (conservative: full spread)
    tas0     TAS fills at official settle + 0.00 (optimistic)
    tas005   TAS fills at official settle + 0.05 adverse (conservative)

Outputs (results/):
    daily_<tag>_t<t>_f<f>.csv   per-day equity / P&L / exposures
    stats_validation.json, stats_extension.json
    equity_validation_t1_f1.png, equity_extension_t1_f1.png
    extension_exposure.json
    stats_slippage.json, daily_slippage_<window>_<scenario>.csv,
    equity_slippage_extension.png
"""
import argparse
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE / "src"))

import pandas as pd  # noqa: E402

from backtest import run_backtest  # noqa: E402
from report import compute_stats, exposure_summary, plot_equity  # noqa: E402
from slippage import describe_tiers  # noqa: E402

RESULTS = BASE / "results"

VAL_START, VAL_END = "2006-01-03", "2013-02-06"
EXT_START = "2013-02-07"

# (scenario name, run_backtest kwargs, description)
SLIP_SCENARIOS = [
    ("slip0",    dict(slip_scale=0.0), "no slippage (thesis baseline)"),
    ("slip0.5x", dict(slip_scale=0.5), "0.5x tiers (optimistic)"),
    ("slip1x",   dict(slip_scale=1.0), "1.0x tiers (base: half-spread adverse)"),
    ("slip2x",   dict(slip_scale=2.0), "2.0x tiers (conservative: full spread)"),
    ("tas0",     dict(tas_diff=0.00),  "TAS at settle + 0.00 (optimistic)"),
    ("tas005",   dict(tas_diff=0.05),  "TAS at settle + 0.05 adverse (conservative)"),
]


def run_window(panel, start, end, tag):
    out = {}
    for (t, f) in [(1, 1), (5, 1), (5, 5), (10, 1), (10, 10)]:
        print(f"[{tag}] t={t} f={f} {start}->{end} ...", flush=True)
        daily, pos, fits, n_fits = run_backtest(panel, t=t, f=f,
                                                start=start, end=end)
        s = compute_stats(daily, label=f"t={t},f={f}")
        s["n_fits"] = n_fits
        out[(t, f)] = (daily, s)
        daily.to_csv(RESULTS / f"daily_{tag}_t{t}_f{f}.csv", index=False)
    return out


def run_slippage(panel, ext_end):
    """t=1,f=1 execution-cost sensitivity for both windows."""
    print("slippage tier table (ASSUMPTION — placeholder for measured spreads):")
    for line in describe_tiers():
        print("   ", line)
    out = {}
    for tag, start, end in [("validation", VAL_START, VAL_END),
                            ("extension", EXT_START, ext_end)]:
        out[tag] = {}
        for name, kwargs, desc in SLIP_SCENARIOS:
            print(f"[slippage:{tag}] {name} ({desc}) ...", flush=True)
            daily, pos, fits, n_fits = run_backtest(
                panel, t=1, f=1, start=start, end=end, **kwargs)
            s = compute_stats(daily, label=f"t=1,f=1 {name}")
            s["n_fits"] = n_fits
            s["scenario"] = name
            out[tag][name] = (daily, s)
            daily.to_csv(RESULTS / f"daily_slippage_{tag}_{name}.csv",
                         index=False)
    return out


def plot_slippage_extension(slip_out, ext_end):
    """Overlay extension equity curves across execution scenarios."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(11, 5))
    order = ["slip0", "slip0.5x", "slip1x", "slip2x", "tas005"]
    labels = {"slip0": "no slippage", "slip0.5x": "0.5x tiers",
              "slip1x": "1x tiers (base)", "slip2x": "2x tiers (conservative)",
              "tas005": "TAS +0.05"}
    for name in order:
        daily, _ = slip_out["extension"][name]
        d = pd.to_datetime(daily["date"])
        ax.plot(d, daily["pv_net"], label=labels[name], lw=1.2)
    ax.set_title(f"t=1,f=1 extension 2013-02-07 to {ext_end} — execution-cost sensitivity (net)")
    ax.set_ylabel("portfolio value ($)")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(RESULTS / "equity_slippage_extension.png", dpi=110)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-panel", action="store_true")
    ap.add_argument("--only", choices=["validation", "extension", "slippage", "all"],
                    default="all")
    args = ap.parse_args()

    RESULTS.mkdir(parents=True, exist_ok=True)
    if not args.skip_panel:
        import panel as panel_mod
        panel_mod.main()
    panel = pd.read_csv(BASE / "data" / "processed" / "vx_panel.csv",
                        dtype={"contract": str})
    print(f"panel: {len(panel)} rows, {panel['contract'].nunique()} contracts")

    if args.only in ("validation", "all"):
        val = run_window(panel, VAL_START, VAL_END, "validation")
        daily11, _ = val[(1, 1)]
        plot_equity(daily11, RESULTS / "equity_validation_t1_f1.png",
                    "Thesis strategy t=1,f=1 — validation 2006-01-03 to 2013-02-06")
        stats = {f"t{t}_f{f}": s for (t, f), (_, s) in val.items()}
        (RESULTS / "stats_validation.json").write_text(json.dumps(stats, indent=2))
        print("wrote results/stats_validation.json")

    if args.only in ("extension", "all", "slippage"):
        ext_end = panel["date"].max()
    else:
        ext_end = None

    if args.only in ("extension", "all"):
        ext = run_window(panel, EXT_START, ext_end, "extension")
        daily11, _ = ext[(1, 1)]
        plot_equity(daily11, RESULTS / "equity_extension_t1_f1.png",
                    f"Thesis strategy t=1,f=1 — extension 2013-02-07 to {ext_end}")
        stats = {f"t{t}_f{f}": s for (t, f), (_, s) in ext.items()}
        (RESULTS / "stats_extension.json").write_text(json.dumps(stats, indent=2))
        expo = exposure_summary(daily11)
        (RESULTS / "extension_exposure.json").write_text(json.dumps(expo, indent=1))
        print("wrote results/stats_extension.json + extension_exposure.json")

    if args.only in ("slippage", "all"):
        slip = run_slippage(panel, ext_end)
        stats = {tag: {name: s for name, (_, s) in sc.items()}
                 for tag, sc in slip.items()}
        (RESULTS / "stats_slippage.json").write_text(json.dumps(stats, indent=2))
        plot_slippage_extension(slip, ext_end)
        print("wrote results/stats_slippage.json + equity_slippage_extension.png")


if __name__ == "__main__":
    main()
