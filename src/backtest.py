"""Backtest engine for the thesis strategy (t, f).

Rules (from the thesis, Section 5.2):
- At each close, estimate (A, B) by NLS on the last t days of closes
  (re-estimated every f days; the estimated params are used until the next
  estimation day).
- Forecast next-day futures prices with today's params:
      F~_{t+1} = V_t * A^(dtm-1) + B * (1 - A^(dtm-1))
- Position for tomorrow: +1 if forecast > market close, -1 if < .
- Phase 2c (dead band): if |forecast - market| <= band(dtm) points,
  HOLD the existing position (no flatten, no reversal). Band scales wider
  further out the curve (tiers aligned with the slippage tier structure).
- Skip contracts with Total Volume < 10.
- Flatten ALL positions the day before the front-month contract expires.
- P&L: $1000 multiplier x position x (settle_{t+1} - settle_t).
- Fee: $2 per contract bought or sold (each unit of |dP|).
- Slippage (first-class param): adverse ticks per fill, tiered by
  days-to-maturity (see src/slippage.py; ASSUMPTION tiers, placeholder for
  measured IBKR spreads), or TAS mode (fills at official settle +/- diff).
- Start equity $100,000.
"""
import numpy as np
import pandas as pd

from model import fit_params, forecast_next_day
from slippage import tier_slippage_points

MULT = 1000.0
FEE_PER_CONTRACT = 2.0
VOL_MIN = 10
TICK = 0.05  # one VX outright tick in index points

# Default dead-band tier factors by days-to-maturity (mirrors the slippage
# tier structure: back months get wider bands so they trade less often).
# (exclusive upper dtm bound, factor multiplying the base band in ticks)
DEADBAND_TIERS = [
    (60, 1),
    (120, 2),
    (210, 3),
    (float("inf"), 5),
]


def deadband_points(dtm, base_ticks=0.0, front_ticks=None, tiers=None):
    """No-trade half-band in index points for a contract `dtm` days to expiry.

    base_ticks  : band width in ticks, multiplied by the tier factor for the
                  contract's dtm bucket (wider further out the curve).
    front_ticks : optional override (in ticks) applied to contracts with
                  dtm < 60, used to keep the front month as a narrow/zero-band
                  control while back months get the wide band. When None, the
                  front uses base_ticks like everything else.
    tiers       : drop-in replacement tier-factor table, same
                  [(dtm_bound_exclusive, factor), ...] format.
    """
    tiers = DEADBAND_TIERS if tiers is None else tiers
    if front_ticks is not None and (dtm == dtm) and dtm < 60:
        return front_ticks * TICK
    factor = 1
    if dtm == dtm:  # NaN -> front factor
        for bound, f in tiers:
            if dtm < bound:
                factor = f
                break
        else:
            factor = tiers[-1][1]
    return base_ticks * factor * TICK


def run_backtest(panel, t=1, f=1, start=None, end=None, init_equity=100000.0,
                 fee_per_contract=FEE_PER_CONTRACT, multiplier=MULT,
                 slip_scale=0.0, tas_diff=None, tiers=None, max_dtm=None,
                 deadband_base_ticks=0.0, deadband_front_ticks=None,
                 deadband_tiers=None):
    """Run the thesis backtest.

    Execution-cost params (new: slippage is first-class):
      slip_scale : 0 = no slippage (thesis baseline). >0 = tiered adverse
                   slippage = slip_scale x half-spread(dtm); 0.5 optimistic,
                   1.0 base, 2.0 conservative (full spread).
      tas_diff   : if not None, TAS mode — fills at official settle plus this
                   adverse differential in index points (0.00 optimistic,
                   0.05 conservative). Overrides slip_scale.
      tiers      : drop-in replacement for the slippage tier table.
      max_dtm    : Phase 2 — restrict the *tradable* universe to contracts
                   with dtm < max_dtm (e.g. 60 = front two monthlies).
                   Estimation still uses the full curve cross-section (thesis
                   calibration); only position-taking is restricted. The
                   flatten-day-before-expiry rule still uses the full-curve
                   front-month dtm.
      deadband_base_ticks  : Phase 2c — no-trade half-band width in ticks,
                   multiplied by the tenor tier factor (1/2/3/5 x for
                   <60/60-120/120-210/>210 d). 0.0 = off (thesis baseline).
      deadband_front_ticks : optional override band (ticks) for dtm < 60,
                   e.g. 0.0 keeps the front month bandless as a control.
    Slippage is charged per unit of |dP| at the fill (decision-day close),
    deducted on the day the new position takes effect, as a separate ledger
    line from the $2/contract commission.
    """
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
    spot = panel.groupby("date")["spot"].last()

    # day-level frames for estimation: list per date of (spot, settle, dtm)
    day_frames = {}
    for d in dates:
        sub = panel[panel["date"] == d][["spot", "settle", "dtm"]]
        day_frames[d] = sub

    n = len(dates)
    pos = pd.DataFrame(0.0, index=dates, columns=contracts)  # pos[k] held on day k
    params = {}  # date -> (A, B) estimated at that close

    last_fit = None
    for k, d in enumerate(dates):
        # --- estimation (every f days, needs t days of history) ---
        if k % f == 0 and k >= t - 1:
            frames = [day_frames[dates[k - i]] for i in range(t - 1, -1, -1)]
            fit = fit_params(frames, t=t)
            if fit is not None:
                last_fit = fit
                params[d] = fit
        # --- decide positions for day k+1 at close of day k ---
        if k + 1 >= n:
            break
        if last_fit is None:
            continue  # stay flat until first successful fit
        A, B = last_fit
        d_next = dates[k + 1]
        # flatten the day before front-month expiry
        front_dtm = dtm.loc[d].min()
        if pd.notna(front_dtm) and front_dtm <= 1:
            continue  # pos stays 0
        s_t = spot.loc[d]
        cur = pos.loc[d]
        row_pos = pos.loc[d_next]
        deadband_on = deadband_base_ticks > 0.0 or (
            deadband_front_ticks is not None and deadband_front_ticks > 0.0)
        for c in contracts:
            v = volume.loc[d, c]
            s = settle.loc[d, c]
            dd = dtm.loc[d, c]
            if pd.isna(v) or v < VOL_MIN or pd.isna(s) or pd.isna(dd):
                continue
            if max_dtm is not None and dd >= max_dtm:
                continue  # Phase 2: restrict tradable universe to liquid tenors
            fc = forecast_next_day(s_t, A, B, dd)
            if deadband_on:
                db = deadband_points(dd, base_ticks=deadband_base_ticks,
                                     front_ticks=deadband_front_ticks,
                                     tiers=deadband_tiers)
                dev = fc - s
                if abs(dev) <= db:
                    row_pos[c] = cur[c]  # inside band: hold, do not touch
                elif dev > 0:
                    row_pos[c] = 1.0
                else:
                    row_pos[c] = -1.0
            else:
                if fc > s:
                    row_pos[c] = 1.0
                elif fc < s:
                    row_pos[c] = -1.0
        pos.loc[d_next] = row_pos
    # expose the universe flag for downstream attribution
    pos.attrs["max_dtm"] = max_dtm
    pos.attrs["deadband_base_ticks"] = deadband_base_ticks
    pos.attrs["deadband_front_ticks"] = deadband_front_ticks

    # --- P&L ---
    dsettle = settle.diff()  # S_k - S_{k-1}
    # positions only count where we have settles on both days
    valid = settle.notna() & settle.shift(1).notna()
    pl_gross = (pos.where(valid, 0.0) * dsettle.where(valid, 0.0)).sum(axis=1) * MULT
    dpos = pos.diff().fillna(pos.iloc[0]).abs()  # |P_k - P_{k-1}|, P_{-1}=0
    fees = dpos.sum(axis=1) * fee_per_contract
    # --- slippage: fills at the decision-day (k-1) close; tier by dtm then ---
    if tas_diff is not None:
        slip_pts = pd.DataFrame(tas_diff, index=dtm.index, columns=dtm.columns)
    elif slip_scale > 0:
        # fill day = decision day (k-1); unknown tenor -> front tier (-1 < 60)
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

    # exposures & turnover (thesis definitions, on net PV)
    mv = pos * settle * MULT
    pv_safe = pv_net.replace(0, np.nan)
    ge = mv.abs().sum(axis=1) / pv_safe
    ne = mv.sum(axis=1) / pv_safe
    turnover = (dpos * settle.shift(1)).sum(axis=1) / (2.0 * pv_safe)

    n_long = (pos > 0).sum(axis=1)
    n_short = (pos < 0).sum(axis=1)

    daily = pd.DataFrame({
        "date": dates, "pv_net": pv_net.values, "pv_gross": pv_gross.values,
        "pl_net": pl_net.values, "pl_gross": pl_gross.values,
        "fees": fees.values, "slippage": slippage.values,
        "n_long": n_long.values, "n_short": n_short.values,
        "gross_exposure": ge.values, "net_exposure": ne.values,
        "turnover": turnover.values,
    })
    n_fits = len(params)
    fit_days = pd.Series({d: params[d] for d in params})
    return daily, pos, fit_days, n_fits
