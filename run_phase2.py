"""Phase 2 runner — front-month-only sweep (Task 1) + structural short (Task 3).

Task 2 (decay diagnosis) is a separate script: phase2_diagnose.py.

Usage:
    python3 run_phase2.py                      # both tasks
    python3 run_phase2.py --only front          # Task 1 only
    python3 run_phase2.py --only short          # Task 3 only

Task 1: t=1,f=1 restricted to contracts with dtm < 60 (front two monthlies —
the tier-1 slippage bucket and the most liquid part of the curve; see the
volume data in results/phase2_front_month.md). Estimation still uses the full
curve cross-section (thesis calibration); only position-taking is restricted.
6 execution scenarios x 2 windows.

Task 3: always-short back-month strategies (see src/short_back.py for the
documented universe choices), full window 2006-01-03 -> present, 6 scenarios.

Outputs (results/, phase2_ prefix):
    phase2_front_dtm60_stats.json
    phase2_front_dtm60_daily_<window>_<scenario>.csv
    phase2_front_equity.png
    phase2_front_exposure.json
    phase2_short_stats.json
    phase2_short_daily_<universe>_<scenario>.csv
    phase2_short_equity.png

Educational backtest only — not financial advice, not a live track record.
"""
import argparse
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE / "src"))

import pandas as pd  # noqa: E402

from backtest import run_backtest  # noqa: E402
from short_back import run_structural_short  # noqa: E402
from report import compute_stats, exposure_summary  # noqa: E402
from run_backtest import SLIP_SCENARIOS, VAL_START, VAL_END, EXT_START  # noqa: E402

RESULTS = BASE / "results"
FRONT_DTM = 60  # tradable universe cutoff: dtm < 60 (front two monthlies)
FULL_START = "2006-01-03"


def _plot_overlay(panels, path, title):
    """panels: list of (daily, label)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(11, 5))
    for daily, label in panels:
        d = pd.to_datetime(daily["date"])
        ax.plot(d, daily["pv_net"], label=label, lw=1.2)
    ax.set_title(title)
    ax.set_ylabel("portfolio value ($, net)")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def run_front(panel, ext_end):
    """Task 1: t=1,f=1 with tradable universe restricted to dtm < 60."""
    stats = {}
    for tag, start, end in [("validation", VAL_START, VAL_END),
                            ("extension", EXT_START, ext_end)]:
        stats[tag] = {}
        for name, kwargs, desc in SLIP_SCENARIOS:
            print(f"[front-dtm60:{tag}] {name} ({desc}) ...", flush=True)
            daily, pos, fits, n_fits = run_backtest(
                panel, t=1, f=1, start=start, end=end,
                max_dtm=FRONT_DTM, **kwargs)
            s = compute_stats(daily, label=f"t=1,f=1 dtm<60 {name}")
            s["n_fits"] = n_fits
            s["scenario"] = name
            s["max_dtm"] = FRONT_DTM
            stats[tag][name] = s
            daily.to_csv(RESULTS / f"phase2_front_dtm60_daily_{tag}_{name}.csv",
                         index=False)
    (RESULTS / "phase2_front_dtm60_stats.json").write_text(
        json.dumps(stats, indent=2))
    # exposure summary on the no-slippage runs
    for tag, sname in [("validation", "slip0"), ("extension", "slip0")]:
        d = pd.read_csv(RESULTS / f"phase2_front_dtm60_daily_{tag}_{sname}.csv")
        expo = exposure_summary(d)
        (RESULTS / f"phase2_front_dtm60_exposure_{tag}.json").write_text(
            json.dumps(expo, indent=1))
    print("wrote results/phase2_front_dtm60_stats.json")


def plot_front(ext_end):
    labels = {"slip0": "no slippage", "slip0.5x": "0.5x tiers",
              "slip1x": "1x tiers (base)", "slip2x": "2x tiers (conservative)",
              "tas005": "TAS +0.05"}
    panels = []
    for name in ["slip0", "slip0.5x", "slip1x", "slip2x", "tas005"]:
        d = pd.read_csv(RESULTS / f"phase2_front_dtm60_daily_extension_{name}.csv")
        panels.append((d, labels[name]))
    _plot_overlay(panels, RESULTS / "phase2_front_equity.png",
                  f"t=1,f=1 dtm<60 (front two monthlies) — extension {EXT_START} to "
                  f"{ext_end} — execution-cost sensitivity (net)")


def run_short(panel, ext_end):
    """Task 3: structural short back-month, full window, 6 scenarios."""
    universes = ["farthest", "dtm120", "front"]
    stats = {}
    for u in universes:
        stats[u] = {}
        for name, kwargs, desc in SLIP_SCENARIOS:
            print(f"[short:{u}] {name} ({desc}) ...", flush=True)
            daily, pos, targets, ndays = run_structural_short(
                panel, start=FULL_START, end=ext_end, universe=u, **kwargs)
            s = compute_stats(daily, label=f"short-{u} {name}")
            s["scenario"] = name
            s["universe"] = u
            s["n_contract_changes"] = int(
                (daily["n_short"] > 0).sum())  # days short held
            stats[u][name] = s
            daily.to_csv(RESULTS / f"phase2_short_daily_{u}_{name}.csv",
                         index=False)
    (RESULTS / "phase2_short_stats.json").write_text(json.dumps(stats, indent=2))
    print("wrote results/phase2_short_stats.json")


def plot_short(ext_end):
    panels = []
    d = pd.read_csv(RESULTS / "phase2_short_daily_farthest_slip0.csv")
    panels.append((d, "farthest short, no slippage"))
    d = pd.read_csv(RESULTS / "phase2_short_daily_farthest_slip1x.csv")
    panels.append((d, "farthest short, 1x tiers"))
    d = pd.read_csv(RESULTS / "phase2_short_daily_farthest_tas005.csv")
    panels.append((d, "farthest short, TAS +0.05"))
    d = pd.read_csv(RESULTS / "phase2_short_daily_front_slip1x.csv")
    panels.append((d, "front-month short benchmark, 1x tiers"))
    _plot_overlay(panels, RESULTS / "phase2_short_equity.png",
                  f"Structural short — {FULL_START} to {ext_end} (net)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["front", "short", "all"], default="all")
    args = ap.parse_args()

    RESULTS.mkdir(parents=True, exist_ok=True)
    panel = pd.read_csv(BASE / "data" / "processed" / "vx_panel.csv",
                        dtype={"contract": str})
    ext_end = panel["date"].max()
    print(f"panel: {len(panel)} rows, {panel['contract'].nunique()} contracts")

    if args.only in ("front", "all"):
        run_front(panel, ext_end)
        plot_front(ext_end)
        print("wrote results/phase2_front_equity.png")
    if args.only in ("short", "all"):
        run_short(panel, ext_end)
        plot_short(ext_end)
        print("wrote results/phase2_short_equity.png")


if __name__ == "__main__":
    main()
