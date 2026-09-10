"""Finish the Phase 2 sweeps that an interrupted session left incomplete.

Runs only what is missing:
  Task 1: front-dtm60 extension slip1x / slip2x / tas0 / tas005
  Task 1: phase2_front_dtm60_stats.json + exposure JSONs (from CSVs)
  Task 1: phase2_front_equity.png
  Task 3: structural short sweep (3 universes x 6 scenarios, full window)
  Task 3: phase2_short_stats.json + phase2_short_equity.png
"""
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
from run_phase2 import _plot_overlay, FRONT_DTM, FULL_START, RESULTS  # noqa: E402

SLIP_BY_NAME = {n: kw for n, kw, _ in SLIP_SCENARIOS}


def finish_front(panel, ext_end):
    for name in ["slip1x", "slip2x", "tas0", "tas005"]:
        out = RESULTS / f"phase2_front_dtm60_daily_extension_{name}.csv"
        if out.exists():
            print(f"[front:extension] {name} already done, skipping")
            continue
        print(f"[front:extension] {name} ...", flush=True)
        daily, pos, fits, n_fits = run_backtest(
            panel, t=1, f=1, start=EXT_START, end=ext_end,
            max_dtm=FRONT_DTM, **SLIP_BY_NAME[name])
        daily.to_csv(out, index=False)

    stats = {}
    for tag, start, end in [("validation", VAL_START, VAL_END),
                            ("extension", EXT_START, ext_end)]:
        stats[tag] = {}
        for name, kwargs, desc in SLIP_SCENARIOS:
            d = pd.read_csv(
                RESULTS / f"phase2_front_dtm60_daily_{tag}_{name}.csv")
            s = compute_stats(d, label=f"t=1,f=1 dtm<60 {name}")
            s["scenario"] = name
            s["max_dtm"] = FRONT_DTM
            stats[tag][name] = s
        # exposure summary on slip0
        d0 = pd.read_csv(RESULTS / f"phase2_front_dtm60_daily_{tag}_slip0.csv")
        (RESULTS / f"phase2_front_dtm60_exposure_{tag}.json").write_text(
            json.dumps(exposure_summary(d0), indent=1))
    (RESULTS / "phase2_front_dtm60_stats.json").write_text(
        json.dumps(stats, indent=2))
    print("wrote phase2_front_dtm60_stats.json + exposure JSONs")

    labels = {"slip0": "no slippage", "slip0.5x": "0.5x tiers",
              "slip1x": "1x tiers (base)", "slip2x": "2x tiers (conservative)",
              "tas005": "TAS +0.05"}
    panels = [(pd.read_csv(
        RESULTS / f"phase2_front_dtm60_daily_extension_{n}.csv"), labels[n])
        for n in ["slip0", "slip0.5x", "slip1x", "slip2x", "tas005"]]
    _plot_overlay(panels, RESULTS / "phase2_front_equity.png",
                  f"t=1,f=1 dtm<60 (front two monthlies) — extension {EXT_START} "
                  f"to {ext_end} — execution-cost sensitivity (net)")
    print("wrote phase2_front_equity.png")


def run_short(panel, ext_end):
    stats = {}
    for u in ["farthest", "dtm120", "front"]:
        stats[u] = {}
        for name, kwargs, desc in SLIP_SCENARIOS:
            out = RESULTS / f"phase2_short_daily_{u}_{name}.csv"
            if out.exists():
                print(f"[short:{u}] {name} already done, skipping")
            else:
                print(f"[short:{u}] {name} ({desc}) ...", flush=True)
                daily, pos, targets, ndays = run_structural_short(
                    panel, start=FULL_START, end=ext_end, universe=u, **kwargs)
                daily.to_csv(out, index=False)
            d = pd.read_csv(out)
            s = compute_stats(d, label=f"short-{u} {name}")
            s["scenario"] = name
            s["universe"] = u
            s["n_short_days"] = int((d["n_short"] > 0).sum())
            stats[u][name] = s
    (RESULTS / "phase2_short_stats.json").write_text(json.dumps(stats, indent=2))
    print("wrote phase2_short_stats.json")

    panels = [
        (pd.read_csv(RESULTS / "phase2_short_daily_farthest_slip0.csv"),
         "farthest short, no slippage"),
        (pd.read_csv(RESULTS / "phase2_short_daily_farthest_slip1x.csv"),
         "farthest short, 1x tiers"),
        (pd.read_csv(RESULTS / "phase2_short_daily_farthest_tas005.csv"),
         "farthest short, TAS +0.05"),
        (pd.read_csv(RESULTS / "phase2_short_daily_front_slip1x.csv"),
         "front-month short benchmark, 1x tiers"),
    ]
    _plot_overlay(panels, RESULTS / "phase2_short_equity.png",
                  f"Structural short — {FULL_START} to {ext_end} (net)")
    print("wrote phase2_short_equity.png")


def main():
    panel = pd.read_csv(BASE / "data" / "processed" / "vx_panel.csv",
                        dtype={"contract": str})
    ext_end = panel["date"].max()
    finish_front(panel, ext_end)
    run_short(panel, ext_end)
    print("PHASE2_FINISH_DONE")


if __name__ == "__main__":
    main()
