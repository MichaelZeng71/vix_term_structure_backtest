"""Stats + charts for backtest output."""
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

TRADING_DAYS = 252


def max_drawdown(pv):
    peak = np.maximum.accumulate(pv)
    dd = pv / peak - 1.0
    return dd.min()


def compute_stats(daily, label=""):
    """daily: DataFrame from backtest.run_backtest (net + gross PV)."""
    pv = daily["pv_net"].to_numpy(dtype=float)
    pvg = daily["pv_gross"].to_numpy(dtype=float)
    n = len(pv)
    rets = pv[1:] / pv[:-1] - 1.0
    rets_g = pvg[1:] / pvg[:-1] - 1.0

    def ann_vol(r):
        return float(np.std(r, ddof=1) * np.sqrt(TRADING_DAYS)) if len(r) > 1 else 0.0

    def sharpe(r, rf_annual=0.0):
        rf_d = (1 + rf_annual) ** (1 / TRADING_DAYS) - 1
        ex = r - rf_d
        sd = np.std(ex, ddof=1)
        return float(np.mean(ex) / sd * np.sqrt(TRADING_DAYS)) if sd > 0 else 0.0

    total = pv[-1] / pv[0] - 1.0
    total_g = pvg[-1] / pvg[0] - 1.0
    yrs = n / TRADING_DAYS
    # ruin guard: fixed-size accounting can take PV negative; annualization
    # and Sharpe are meaningless past a total loss — flag it instead of NaN.
    ruined = (1 + total) <= 0
    ruined_g = (1 + total_g) <= 0
    stats = {
        "label": label,
        "n_days": n,
        "start": str(daily["date"].iloc[0]),
        "end": str(daily["date"].iloc[-1]),
        "ruined_net": bool(ruined),
        "ruined_gross": bool(ruined_g),
        "total_return_net": float(total),
        "total_return_gross": float(total_g),
        "ann_return_net": None if ruined else float((1 + total) ** (1 / yrs) - 1),
        "ann_return_gross": None if ruined_g else float((1 + total_g) ** (1 / yrs) - 1),
        "ann_vol_net": None if ruined else ann_vol(rets),
        "ann_vol_gross": None if ruined_g else ann_vol(rets_g),
        "sharpe_rf0_net": None if ruined else sharpe(rets, 0.0),
        "sharpe_rf0_gross": None if ruined_g else sharpe(rets_g, 0.0),
        # thesis Sharpe 1.52 with 20.10%/12.18% implies rf ~= 1.6%; show for comparability
        "sharpe_rf1.6_net": None if ruined else sharpe(rets, 0.016),
        "sharpe_rf1.6_gross": None if ruined_g else sharpe(rets_g, 0.016),
        "max_dd_net": float(max_drawdown(pv)),
        "max_dd_gross": float(max_drawdown(pvg)),
        # win rate: fraction of days with positive net P&L (thesis "Winning Proportion")
        "win_rate_net": float((daily["pl_net"].to_numpy()[1:] > 0).mean()),
        "total_fees": float(daily["fees"].sum()),
        "total_slippage": float(daily["slippage"].sum()) if "slippage" in daily else 0.0,
        "final_pv_net": float(pv[-1]),
        "final_pv_gross": float(pvg[-1]),
    }
    return stats


def exposure_summary(daily):
    ge = daily["gross_exposure"].replace([np.inf, -np.inf], np.nan).dropna()
    ne = daily["net_exposure"].replace([np.inf, -np.inf], np.nan).dropna()
    to = daily["turnover"].replace([np.inf, -np.inf], np.nan).dropna()
    q = lambda s: {"min": float(s.min()), "p5": float(s.quantile(0.05)),
                   "median": float(s.median()), "mean": float(s.mean()),
                   "p95": float(s.quantile(0.95)), "max": float(s.max())}
    return {"gross_exposure": q(ge), "net_exposure": q(ne), "turnover": q(to)}


def plot_equity(daily, path, title):
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 7), sharex=True,
                                   gridspec_kw={"height_ratios": [3, 1]})
    d = pd.to_datetime(daily["date"])
    ax1.plot(d, daily["pv_net"], label="net of fees", lw=1.2)
    ax1.plot(d, daily["pv_gross"], label="gross", lw=1.0, alpha=0.7)
    ax1.set_title(title)
    ax1.set_ylabel("portfolio value ($)")
    ax1.legend()
    ax1.grid(alpha=0.3)
    ax2.bar(d, daily["pl_net"], width=2, color="gray", alpha=0.6)
    ax2.set_ylabel("daily net P&L ($)")
    ax2.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def stats_table(rows):
    """rows: list of (label, stats_dict). Returns markdown table string."""
    cols = [("Total ret.", "total_return_net"), ("Ann. ret.", "ann_return_net"),
            ("Ann. vol", "ann_vol_net"), ("Sharpe rf=0", "sharpe_rf0_net"),
            ("Sharpe rf=1.6%", "sharpe_rf1.6_net"), ("Max DD", "max_dd_net"),
            ("Win rate", "win_rate_net")]
    head = "| spec | " + " | ".join(c[0] for c in cols) + " |"
    sep = "|" + "---|" * (len(cols) + 1)
    lines = [head, sep]
    for label, s in rows:
        vals = []
        for _, k in cols:
            v = s[k]
            vals.append(f"{v * 100:.2f}%" if k in
                        ("total_return_net", "ann_return_net", "ann_vol_net",
                         "max_dd_net", "win_rate_net") else f"{v:.2f}")
        lines.append("| " + label + " | " + " | ".join(vals) + " |")
    return "\n".join(lines)
