"""Phase 2, Task 2: why did the edge die after 2022?

Diagnoses the decay by measuring, day by day over 2006-2026:
  - term-structure shape: contango steepness (front vs back spread),
    fitted model params (A = mean-reversion speed, B = long-run mean),
    spot VIX level, fit quality (RMSE)
  - signal the model actually detects: mean |forecast - settle| / settle
    over tradeable contracts (the gross mispricing the strategy harvests)
  - microstructure proxies available in the data: median volume by
    maturity bucket, turnover (from Phase 1 daily ledgers)
  - gross P&L attribution by maturity bucket (re-run capturing positions)

Outputs (results/, phase2_ prefix):
    phase2_diag_daily.csv        per-day term-structure + signal diagnostics
    phase2_diag_yearly.csv       annual aggregates + t=1,f=1 yearly returns
    phase2_diag_pnl_bucket.csv   yearly gross P&L by dtm bucket
    phase2_decay_curve.png       annual steepness / spot / signal magnitude
    phase2_decay_rolling.png     252d rolling steepness + spot, 2006-2026
    phase2_decay_pnl_maturity.png yearly gross P&L by maturity bucket
    phase2_decay.md              written diagnosis

Educational research only — not financial advice.
"""
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE / "src"))

import numpy as np
import pandas as pd

from model import fit_params, forecast_next_day  # noqa: E402
from backtest import run_backtest  # noqa: E402
from slippage import tier_slippage_points  # noqa: E402

RESULTS = BASE / "results"
MULT = 1000.0
VOL_MIN = 10


def daily_diagnostics(panel):
    dates = sorted(panel["date"].unique())
    settle = panel.pivot_table(index="date", columns="contract",
                               values="settle", aggfunc="last")
    volume = panel.pivot_table(index="date", columns="contract",
                               values="volume", aggfunc="last")
    dtm = panel.pivot_table(index="date", columns="contract",
                            values="dtm", aggfunc="last")
    spot = panel.groupby("date")["spot"].last()

    rows = []
    for i, d in enumerate(dates):
        if i % 1000 == 0:
            print(f"  fit day {i}/{len(dates)} ...", flush=True)
        s_t = spot.loc[d]
        if pd.isna(s_t) or s_t <= 0:
            continue
        sub = panel[panel["date"] == d][["spot", "settle", "dtm"]]
        fit = fit_params([sub], t=1)
        if fit is None:
            continue
        A, B = fit
        v = volume.loc[d]; st = settle.loc[d]; dd = dtm.loc[d]
        trad = v.notna() & (v >= VOL_MIN) & st.notna() & dd.notna() & (dd > 0)
        if trad.sum() < 2:
            continue
        stt, ddd = st[trad], dd[trad]
        # front / second / back
        order = ddd.sort_values()
        c_front, c_back = order.index[0], order.index[-1]
        f_set, f_dtm = stt[c_front], ddd[c_front]
        b_set, b_dtm = stt[c_back], ddd[c_back]
        steep_rel = (b_set - f_set) / f_set
        slope30 = (b_set - f_set) / max(b_dtm - f_dtm, 1) * 30.0
        # model fit RMSE (all contracts, same as calibration)
        allc = st.notna() & dd.notna()
        pred = st[allc] * 0 + np.nan
        from model import price
        pred = price(s_t, A, B, dd[allc].to_numpy())
        resid = st[allc].to_numpy() - pred
        rmse = float(np.sqrt(np.mean(resid ** 2)) / st[allc].mean())
        # signal magnitude: mean |forecast - settle| / settle over tradeables
        fc = forecast_next_day(s_t, A, B, ddd.to_numpy())
        sig = np.abs(fc - stt.to_numpy()) / stt.to_numpy()
        sig_mag = float(np.mean(sig))
        # volume by bucket
        vmed_front = float(v[dd.notna() & (dd < 60)].median())
        vmed_back = float(v[dd.notna() & (dd > 210)].median())
        rows.append(dict(
            date=d, spot=s_t, A=A, B=B, rmse=rmse,
            front_settle=f_set, front_dtm=f_dtm,
            back_settle=b_set, back_dtm=b_dtm,
            steep_rel=steep_rel, slope30=slope30,
            contango=int(b_set > f_set),
            front_backwardation=int(f_set < s_t),
            richness=B / s_t, sig_mag=sig_mag,
            n_tradable=int(trad.sum()),
            vol_med_front=vmed_front, vol_med_back=vmed_back,
        ))
    return pd.DataFrame(rows)


def pnl_by_bucket(panel, start, end):
    """Gross P&L attributed by fill-day dtm bucket (t=1,f=1, no slippage)."""
    daily, pos, fits, nf = run_backtest(panel, t=1, f=1,
                                        start=start, end=end, slip_scale=0.0)
    dates = sorted(panel["date"].unique())
    sub = panel[(panel["date"] >= start) & (panel["date"] <= end)]
    settle = sub.pivot_table(index="date", columns="contract",
                             values="settle", aggfunc="last")
    dtm = sub.pivot_table(index="date", columns="contract",
                          values="dtm", aggfunc="last")
    dsettle = settle.diff()
    # contract-day contribution, bucketed by fill-day (decision-day) dtm
    dtm_fill = dtm.shift(1)
    contrib = pos * dsettle * MULT
    buckets = pd.cut(dtm_fill.stack(),
                     [0, 60, 120, 210, 1e9],
                     labels=["<60d", "60-120d", "120-210d", ">210d"])
    stacked = contrib.stack()
    df = pd.DataFrame({"pnl": stacked, "bucket": buckets})
    df = df.dropna(subset=["bucket"])
    df["date"] = df.index.get_level_values(0)
    df["year"] = pd.to_datetime(df["date"]).dt.year
    g = df.groupby(["year", "bucket"], observed=True)["pnl"].sum().unstack(fill_value=0)
    cnt = df.groupby(["year", "bucket"], observed=True).size().unstack(fill_value=0)
    return g, cnt


def main():
    RESULTS.mkdir(parents=True, exist_ok=True)
    panel = pd.read_csv(BASE / "data" / "processed" / "vx_panel.csv",
                        dtype={"contract": str})
    print(f"panel: {len(panel)} rows")
    panel["date"] = panel["date"].astype(str)

    # --- 1. daily diagnostics ---
    print("computing daily diagnostics ...")
    diag = daily_diagnostics(panel)
    diag.to_csv(RESULTS / "phase2_diag_daily.csv", index=False)
    print(f"  {len(diag)} days")

    # --- 2. yearly aggregates + t=1,f=1 returns ---
    ret_frames = []
    for tag in ["validation", "extension"]:
        d = pd.read_csv(RESULTS / f"daily_{tag}_t1_f1.csv")
        d["date"] = pd.to_datetime(d["date"])
        d["year"] = d["date"].dt.year
        # yearly total return from pv_net path within year
        for y, g in d.groupby("year"):
            r = g["pv_net"].iloc[-1] / g["pv_net"].iloc[0] - 1
            ret_frames.append({"year": y, "ret_net": r,
                               "pnl_gross": g["pl_gross"].sum(),
                               "turnover_med": g["turnover"].median(),
                               "fees": g["fees"].sum()})
    rets = pd.DataFrame(ret_frames)
    diag["date"] = pd.to_datetime(diag["date"])
    diag["year"] = diag["date"].dt.year
    agg = diag.groupby("year").agg(
        spot_mean=("spot", "mean"), A_mean=("A", "mean"), B_mean=("B", "mean"),
        rmse_mean=("rmse", "mean"), steep_rel_mean=("steep_rel", "mean"),
        slope30_mean=("slope30", "mean"), contango_freq=("contango", "mean"),
        front_bw_freq=("front_backwardation", "mean"),
        richness_mean=("richness", "mean"), sig_mag_mean=("sig_mag", "mean"),
        vol_front_med=("vol_med_front", "median"),
        vol_back_med=("vol_med_back", "median"),
        n_days=("date", "count")).reset_index()
    yearly = agg.merge(rets, on="year", how="left")
    yearly.to_csv(RESULTS / "phase2_diag_yearly.csv", index=False)

    # era table
    diag["era"] = pd.cut(diag["year"], [2005, 2013, 2021, 2027],
                         labels=["2006-2013", "2013-2021", "2022-2026"])
    era = diag.groupby("era", observed=True).agg(
        spot_mean=("spot", "mean"), A_mean=("A", "mean"),
        steep_rel_mean=("steep_rel", "mean"),
        slope30_mean=("slope30", "mean"),
        contango_freq=("contango", "mean"),
        sig_mag_mean=("sig_mag", "mean"),
        rmse_mean=("rmse", "mean"),
        richness_mean=("richness", "mean"),
        vol_front_med=("vol_med_front", "median"),
        vol_back_med=("vol_med_back", "median")).round(4)
    era.to_csv(RESULTS / "phase2_diag_era.csv")

    # --- 3. P&L by maturity bucket ---
    print("P&L attribution by maturity bucket ...")
    g1, c1 = pnl_by_bucket(panel, "2006-01-03", "2013-02-06")
    g2, c2 = pnl_by_bucket(panel, "2013-02-07", panel["date"].max())
    g = pd.concat([g1, g2]).sort_index()
    cnt = pd.concat([c1, c2]).sort_index()
    g.to_csv(RESULTS / "phase2_diag_pnl_bucket.csv")
    cnt.to_csv(RESULTS / "phase2_diag_contractdays_bucket.csv")

    # --- 4. charts ---
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    y = yearly.sort_values("year")
    fig, axes = plt.subplots(3, 1, figsize=(12, 9), sharex=True)
    axes[0].bar(y["year"], y["steep_rel_mean"] * 100, color="steelblue")
    axes[0].set_ylabel("mean (back-front)/front (%)")
    axes[0].set_title("VIX futures term-structure steepness — annual mean (back = farthest listed)")
    axes[0].grid(alpha=0.3)
    axes[1].plot(y["year"], y["spot_mean"], color="darkred", marker="o", ms=3)
    axes[1].set_ylabel("mean spot VIX")
    axes[1].set_title("Average spot VIX level — annual mean")
    axes[1].grid(alpha=0.3)
    axes[2].bar(y["year"], y["sig_mag_mean"] * 100, color="seagreen", label="signal |fc-S|/S")
    axes[2].set_ylabel("mean |forecast-settle|/settle (%)")
    axes[2].set_title("Mispricing magnitude detected by the model — annual mean")
    axes[2].grid(alpha=0.3)
    for ax in axes:
        ax.axvline(2021.5, color="k", ls="--", lw=1)
        ax.text(2021.7, ax.get_ylim()[1] * 0.92, "2022 regime line", fontsize=8)
    fig.tight_layout()
    fig.savefig(RESULTS / "phase2_decay_curve.png", dpi=110)
    plt.close(fig)

    dd = diag.sort_values("date")
    dd["steep_roll"] = dd["steep_rel"].rolling(252, min_periods=60).mean()
    dd["spot_roll"] = dd["spot"].rolling(252, min_periods=60).mean()
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 7), sharex=True)
    ax1.plot(dd["date"], dd["steep_roll"] * 100, color="steelblue", lw=1.2)
    ax1.set_ylabel("252d mean (back-front)/front (%)")
    ax1.set_title("Term-structure steepness, 252-day rolling mean")
    ax1.grid(alpha=0.3)
    ax2.plot(dd["date"], dd["spot_roll"], color="darkred", lw=1.2)
    ax2.set_ylabel("252d mean spot VIX")
    ax2.set_title("Spot VIX, 252-day rolling mean")
    ax2.grid(alpha=0.3)
    for ax in (ax1, ax2):
        ax.axvline(pd.Timestamp("2022-01-01"), color="k", ls="--", lw=1)
    fig.tight_layout()
    fig.savefig(RESULTS / "phase2_decay_rolling.png", dpi=110)
    plt.close(fig)

    gg = g.copy()
    gg.index = gg.index.astype(int)
    fig, ax = plt.subplots(figsize=(12, 5))
    gg[["<60d", "60-120d", "120-210d", ">210d"]].plot(
        kind="bar", stacked=True, ax=ax,
        color=["#2ca02c", "#ffbb78", "#ff7f0e", "#d62728"])
    ax.axhline(0, color="k", lw=0.8)
    ax.set_title("t=1,f=1 gross P&L by fill-day maturity bucket ($/year, no slippage)")
    ax.set_ylabel("gross P&L ($)")
    ax.legend(fontsize=8, title="dtm bucket")
    fig.tight_layout()
    fig.savefig(RESULTS / "phase2_decay_pnl_maturity.png", dpi=110)
    plt.close(fig)

    # correlations: yearly gross return vs structure variables
    yy = y.dropna(subset=["ret_net"])
    corrs = {c: float(yy["ret_net"].corr(yy[c])) for c in
             ["steep_rel_mean", "spot_mean", "sig_mag_mean", "contango_freq",
              "A_mean", "richness_mean"]}
    (RESULTS / "phase2_diag_corr.json").write_text(json.dumps(corrs, indent=2))
    print("correlations(yearly net return, structure):", json.dumps(corrs, indent=2))
    print("era table:\n", era.to_string())
    print("wrote phase2_diag_* + phase2_decay_*.png")


if __name__ == "__main__":
    main()
