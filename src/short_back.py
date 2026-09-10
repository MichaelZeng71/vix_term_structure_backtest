"""Structural short back-month engine (Phase 2, Task 3).

Opposite of the thesis: no model, no long/short switching — always hold a
short position (-1 contract) in a defined back-month contract, harvesting
contango roll-down / the variance risk premium. The target contract is
re-selected daily at the close; a roll (target change) incurs the same
execution costs as the thesis engine (tiered slippage + $2/contract fee).

Universes (documented choice):
  "farthest" : farthest listed standard monthly with Total Volume >= 10
               at the decision close (the purest "back month").
  "farthest_sticky" : same target definition, but the position is sticky:
               keep the current contract while it stays tradable
               (volume >= 10) and still a back month (dtm >= 120);
               otherwise roll to the farthest tradable. This is the
               realistic implementation of "always short the back month"
               (monthly-ish rolls, no daily re-selection churn).
  "dtm120"   : nearest contract with dtm > 120 and volume >= 10
               (fixed-tenor back-month definition).
  "front"    : nearest contract (min dtm, volume >= 10) — included only as
               the conventional carry benchmark, not a proposal.

P&L: $1000 multiplier x position x (settle_{t+1} - settle_t).
Slippage: same tier table / TAS convention as the thesis engine; charged
per unit of |dP| on roll days at the fill-day (decision-day) dtm.
Start equity $100,000. No position scaling, no stops — same accounting
convention as Phase 1 (ruin flagged, not clipped).
"""
import numpy as np
import pandas as pd

MULT = 1000.0
FEE_PER_CONTRACT = 2.0
VOL_MIN = 10


def _select_target(vol_row, dtm_row, settle_row, universe):
    """Return the target contract label for a decision-day close, or None."""
    ok = vol_row.notna() & (vol_row >= VOL_MIN) & settle_row.notna() \
        & dtm_row.notna() & (dtm_row > 0)
    cands = vol_row[ok]
    if cands.empty:
        return None
    d = dtm_row[ok]
    if universe == "farthest":
        return d.idxmax()
    if universe == "dtm120":
        back = d[d > 120]
        return back.idxmin() if not back.empty else d.idxmax()
    if universe == "front":
        return d.idxmin()
    raise ValueError(f"unknown universe {universe!r}")


# Variant 3b — persistent (low-turnover) short.
# rank: which monthly to hold (2 = second month, 5 ~= 4-6 month bucket).
# roll_trigger_dtm: roll when the held contract's dtm falls to this level
# (or it becomes untradable). Documented choice per the spec.
PERSISTENT_VARIANTS = {
    "second_month": dict(rank=2, roll_trigger_dtm=5,
                         desc="hold 2nd monthly; roll 5d before expiry"),
    "month5": dict(rank=5, roll_trigger_dtm=90,
                   desc="hold 5th monthly (~4-6m zone); roll when dtm<=90"),
}


def _select_persistent(vol_row, dtm_row, settle_row, rank):
    ok = vol_row.notna() & (vol_row >= VOL_MIN) & settle_row.notna() \
        & dtm_row.notna() & (dtm_row > 0)
    if not ok.any():
        return None
    d = dtm_row[ok].sort_values()
    if len(d) >= rank:
        return d.index[rank - 1]
    return d.index[-1]  # fewer listed than rank: hold the farthest


def _build_ledger(panel, pos, init_equity, fee_per_contract, multiplier,
                  slip_scale, tas_diff, tiers):
    """Shared P&L / slippage / exposure ledger from a position frame."""
    from slippage import tier_slippage_points  # local import: same as engine
    dates = list(pos.index)
    contracts = list(pos.columns)
    settle = panel.pivot_table(index="date", columns="contract",
                               values="settle", aggfunc="last")
    dtm = panel.pivot_table(index="date", columns="contract",
                            values="dtm", aggfunc="last")
    # align to the position frame's dates/columns
    settle = settle.reindex(index=dates, columns=contracts)
    dtm = dtm.reindex(index=dates, columns=contracts)

    dsettle = settle.diff()
    valid = settle.notna() & settle.shift(1).notna()
    pl_gross = (pos.where(valid, 0.0) * dsettle.where(valid, 0.0)).sum(axis=1) * multiplier
    dpos = pos.diff().fillna(pos.iloc[0]).abs()
    fees = dpos.sum(axis=1) * fee_per_contract

    if tas_diff is not None:
        slip_pts = pd.DataFrame(tas_diff, index=dtm.index, columns=dtm.columns)
    elif slip_scale > 0:
        dtm_fill = dtm.shift(1).fillna(-1.0)
        vtier = np.vectorize(
            lambda x: tier_slippage_points(x, scale=slip_scale, tiers=tiers))
        slip_pts = pd.DataFrame(vtier(dtm_fill.values),
                                index=dtm.index, columns=dtm.columns)
    else:
        slip_pts = pd.DataFrame(0.0, index=dtm.index, columns=dtm.columns)
    slippage = (dpos * slip_pts * multiplier).sum(axis=1)
    pl_net = pl_gross - fees - slippage

    pv_gross = init_equity + pl_gross.cumsum()
    pv_net = init_equity + (pl_gross - fees - slippage).cumsum()

    mv = pos * settle * multiplier
    pv_safe = pv_net.replace(0, np.nan)
    ge = mv.abs().sum(axis=1) / pv_safe
    ne = mv.sum(axis=1) / pv_safe
    turnover = (dpos * settle.shift(1)).sum(axis=1) / (2.0 * pv_safe)

    n_short = (pos < 0).sum(axis=1)
    daily = pd.DataFrame({
        "date": dates, "pv_net": pv_net.values, "pv_gross": pv_gross.values,
        "pl_net": pl_net.values, "pl_gross": pl_gross.values,
        "fees": fees.values, "slippage": slippage.values,
        "n_long": np.zeros(len(dates), dtype=int), "n_short": n_short.values,
        "gross_exposure": ge.values, "net_exposure": ne.values,
        "turnover": turnover.values,
    })
    return daily, dpos


def run_structural_short(panel, start=None, end=None, universe="farthest",
                         init_equity=100000.0, fee_per_contract=FEE_PER_CONTRACT,
                         multiplier=MULT, slip_scale=0.0, tas_diff=None,
                         tiers=None, vol_min=VOL_MIN):
    """Backtest the always-short back-month strategy.

    Returns (daily, pos, targets, n_days) where daily has the same columns
    as backtest.run_backtest, targets maps date -> contract label held.
    """
    from slippage import tier_slippage_points  # local import: same as engine
    panel = panel.copy()
    if start:
        panel = panel[panel["date"] >= start]
    if end:
        panel = panel[panel["date"] <= end]

    dates = sorted(panel["date"].unique())
    contracts = sorted(panel["contract"].unique())
    settle = panel.pivot_table(index="date", columns="contract",
                               values="settle", aggfunc="last")
    volume = panel.pivot_table(index="date", columns="contract",
                               values="volume", aggfunc="last")
    dtm = panel.pivot_table(index="date", columns="contract",
                            values="dtm", aggfunc="last")

    n = len(dates)
    pos = pd.DataFrame(0.0, index=dates, columns=contracts)  # held on day k
    targets = {}          # decision date -> contract chosen at that close
    held_contract = {}    # date -> contract actually held that day
    current = None        # sticky state for farthest_sticky

    for k, d in enumerate(dates):
        if k + 1 >= n:
            break
        if universe == "farthest_sticky":
            vr, dr, sr = volume.loc[d], dtm.loc[d], settle.loc[d]
            ok = vr.notna() & (vr >= vol_min) & sr.notna() & dr.notna() \
                & (dr > 0)
            if (current is not None and current in vr.index[ok]
                    and dr[current] >= 120):
                tgt = current  # keep the structural position
            else:
                tgt = _select_target(vr, dr, sr, "farthest")
                current = tgt
        else:
            tgt = _select_target(volume.loc[d], dtm.loc[d], settle.loc[d],
                                 universe)
        targets[d] = tgt
        if tgt is None:
            continue  # stay flat; nothing tradable
        # hold -1 of tgt on day k+1, but only if it still has a settle then
        if pd.notna(settle.loc[dates[k + 1], tgt]):
            pos.loc[dates[k + 1], tgt] = -1.0
            held_contract[dates[k + 1]] = tgt

    # --- P&L via shared ledger ---
    daily, dpos = _build_ledger(panel, pos, init_equity, fee_per_contract,
                                multiplier, slip_scale, tas_diff, tiers)
    # contract held per day (for attribution)
    daily["contract"] = [held_contract.get(d, "") for d in dates]
    daily["contract_dtm"] = [dtm.loc[d, held_contract[d]] if d in held_contract
                             else np.nan for d in dates]
    return daily, pos, targets, n


def run_persistent_short(panel, variant="second_month", start=None, end=None,
                         init_equity=100000.0, fee_per_contract=FEE_PER_CONTRACT,
                         multiplier=MULT, slip_scale=0.0, tas_diff=None,
                         tiers=None, vol_min=VOL_MIN):
    """Variant 3b: persistent low-turnover short.

    Hold -1 contract in the `variant` maturity slot; roll ONLY when the held
    contract approaches expiry (dtm <= roll_trigger_dtm) or becomes
    untradable. No daily signal — the position is structural. Trades happen
    only on roll dates (+ initial entry), so `fees`/`slippage` in the ledger
    ARE the roll costs, reported separately from daily mark-to-market
    (`pl_gross`).

    Returns (daily, pos, roll_dates, n) where roll_dates lists decision dates
    on which a roll (or the initial entry) occurred.
    """
    if variant not in PERSISTENT_VARIANTS:
        raise ValueError(f"unknown persistent variant {variant!r}")
    spec = PERSISTENT_VARIANTS[variant]
    rank, trigger = spec["rank"], spec["roll_trigger_dtm"]

    panel = panel.copy()
    if start:
        panel = panel[panel["date"] >= start]
    if end:
        panel = panel[panel["date"] <= end]

    dates = sorted(panel["date"].unique())
    contracts = sorted(panel["contract"].unique())
    settle = panel.pivot_table(index="date", columns="contract",
                               values="settle", aggfunc="last")
    volume = panel.pivot_table(index="date", columns="contract",
                               values="volume", aggfunc="last")
    dtm = panel.pivot_table(index="date", columns="contract",
                            values="dtm", aggfunc="last")

    n = len(dates)
    pos = pd.DataFrame(0.0, index=dates, columns=contracts)
    held_contract = {}
    roll_dates = []
    current = None

    def tradable(d, c):
        return (c is not None and c in volume.columns
                and pd.notna(volume.loc[d, c]) and volume.loc[d, c] >= vol_min
                and pd.notna(settle.loc[d, c]) and pd.notna(dtm.loc[d, c]))

    for k, d in enumerate(dates):
        if k + 1 >= n:
            break
        d_next = dates[k + 1]
        need_roll = (current is None or not tradable(d, current)
                     or dtm.loc[d, current] <= trigger)
        if need_roll:
            tgt = _select_persistent(volume.loc[d], dtm.loc[d], settle.loc[d],
                                     rank)
            if tgt != current:
                roll_dates.append(d)  # decision date of the roll/entry
            current = tgt
        if current is None:
            continue
        if pd.notna(settle.loc[d_next, current]):
            pos.loc[d_next, current] = -1.0
            held_contract[d_next] = current

    daily, dpos = _build_ledger(panel, pos, init_equity, fee_per_contract,
                                multiplier, slip_scale, tas_diff, tiers)
    daily["contract"] = [held_contract.get(d, "") for d in dates]
    daily["contract_dtm"] = [dtm.loc[d, held_contract[d]] if d in held_contract
                             else np.nan for d in dates]
    daily["roll_event"] = daily["date"].isin(roll_dates).astype(int)
    return daily, pos, roll_dates, n


def stylized_call_hedge(dates, spot_by_date, units=0.25, premium=150.0,
                        strike=30.0, tenor_trading_days=63, opt_mult=100.0):
    """ASSUMPTION stylized tail hedge: long VIX calls.

    On the first trading day of each month, buy `units` 3-month `strike`-
    strike VIX calls at a FIXED $`premium` per call (ASSUMPTION — real premia
    vary with vol-of-vol; no free long-history VIX options price data exists
    to calibrate this). Hold each lot to expiry (tenor_trading_days later);
    expiry payoff = units * opt_mult * max(0, spot_VIX(expiry) - strike).

    Notes: VIX options have a $100 multiplier (vs $1000 for futures).
    Expiry uses the daily spot VIX close as a proxy for the AM settlement
    (VRO) — approximation, flagged.

    Returns DataFrame indexed by date with hedge_premium, hedge_payoff.
    """
    dates = list(dates)
    idx = {d: i for i, d in enumerate(dates)}
    # first trading day of each month
    months = {}
    for d in dates:
        key = d[:7]
        if key not in months:
            months[key] = d
    buys = sorted(months.values())

    prem = pd.Series(0.0, index=dates)
    pay = pd.Series(0.0, index=dates)
    for b in buys:
        i = idx[b]
        prem.loc[b] += units * premium
        j = min(i + tenor_trading_days, len(dates) - 1)
        e = dates[j]
        s = spot_by_date.get(e, float("nan"))
        if pd.notna(s):
            pay.loc[e] += units * opt_mult * max(0.0, s - strike)
    return pd.DataFrame({"hedge_premium": prem, "hedge_payoff": pay})
