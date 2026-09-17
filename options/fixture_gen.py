"""Synthetic VIX options chain fixture generator.

Produces one trading day's VIX options chain with realistic structure:
- Strikes in 0.5 steps around the spot (wider grid for longer tenors).
- European-style calls/puts (VIX options are European, cash-settled).
- Theoretical value from a simple lognormal (Black76-like) pricing on the
  forward; bid/ask spreads modeled as a function of strike distance from
  spot (OTM = wider) and tenor (longer = wider), roughly matching known
  wide VIX option spreads.
- Deterministic given `seed`: reproducible fixtures for unit tests.

Canonical chain schema (one row per strike/expiration/right):
    date, expiration, strike, right, bid, ask, volume, open_interest, underlying

No bid may exceed ask by construction; a small fraction of near-zero-bid
far-OTM rows are included to mirror real quote reality.
"""

from __future__ import annotations

import datetime as dt
import math

import numpy as np
import pandas as pd

TICK = 0.01


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _black76(option_type: str, forward: float, strike: float, tte: float,
             sigma: float) -> float:
    """Black-76 price for a futures/forward option (VIX options settle on the
    VX futures settlement, so forward-based pricing is the right skeleton)."""
    if tte <= 0:
        return max(0.0, (forward - strike) if option_type == "C" else (strike - forward))
    vol = sigma * math.sqrt(tte)
    d1 = (math.log(forward / strike) + 0.5 * vol * vol) / vol
    d2 = d1 - vol
    disc = 1.0  # forward already discounts; keep explicit
    if option_type == "C":
        return disc * (forward * _norm_cdf(d1) - strike * _norm_cdf(d2))
    return disc * (strike * _norm_cdf(-d2) - forward * _norm_cdf(-d1))


def _spread_frac(strike: float, spot: float, tte: float, premium: float) -> float:
    """Spread as a fraction of premium.

    - Base ~8% of premium near ATM short-dated.
    - Grows with |moneyness| (OTM quotes widen; ITM less so).
    - Grows with tenor (~sqrt(tte)) — long-dated VIX options quote wide.
    - Tiny premiums get a tick floor applied later.
    """
    moneyness = abs(math.log(strike / spot))
    frac = 0.08 + 1.6 * moneyness * (1.0 if strike >= spot else 0.55)
    frac *= 1.0 + 0.55 * math.sqrt(max(tte, 0.0)) * 6.0 / 6.0
    return frac


def generate_chain(asof: dt.date | str, spot: float, seed: int = 7,
                   expirations_days: tuple[int, ...] = (7, 14, 30, 60, 90, 150),
                   expirations: list | None = None,
                   rng_scale: float = 1.0) -> pd.DataFrame:
    """Generate one day's synthetic VIX option chain.

    Parameters
    ----------
    asof : date or 'YYYY-MM-DD'
    spot : VIX spot quote for the day
    seed : determinism seed
    expirations_days : days to expiry per expiration (relative to `asof`);
        ignored when `expirations` (absolute dates) is given.
    expirations : optional list of absolute expiration dates; the same list
        across days models a real monthly cycle whose DTE shrinks day by day.
    """
    asof = pd.Timestamp(asof).date()
    rng = np.random.default_rng(seed)

    if expirations is None:
        expirations = [asof + dt.timedelta(days=d) for d in expirations_days]

    rows = []
    for exp in expirations:
        dte = (exp - asof).days
        if dte <= 0:
            continue  # expired
        tte = dte / 365.0
        # Forward premium over spot (contango): term structure of VIX
        fwd_mult = 1.0 + 0.35 * (1.0 - math.exp(-tte * 4.0))
        forward = spot * fwd_mult
        # IV term structure: high near-term, flattening out
        sigma = 0.95 * (1.0 - 0.35 * (1.0 - math.exp(-tte * 3.0)))

        # Strike grid: denser near spot, sparser in tails, wider for long tenor
        half_width = spot * (0.55 + 0.45 * math.sqrt(dte / 30.0)) * rng_scale
        lo = max(5.0, spot - half_width)
        hi = spot + half_width * 1.4
        step = 0.5 if dte <= 30 else 1.0
        strikes = np.arange(round(lo * 2) / 2.0, hi + 1e-9, step)

        for k in strikes:
            k = float(round(k, 2))
            for right in ("C", "P"):
                theo = _black76(right, forward, k, tte, sigma)
                frac = _spread_frac(k, spot, tte, theo)
                spread = max(2 * TICK, frac * max(theo, 0.02))
                half = spread / 2.0
                bid = max(0.0, theo - half)
                ask = theo + half
                # Round to tick
                bid = math.floor(bid / TICK + 1e-9) * TICK
                ask = math.ceil(ask / TICK - 1e-9) * TICK
                bid = round(max(0.0, bid), 2)
                ask = round(max(bid, ask), 2)
                if theo < 0.02:
                    bid = 0.0  # far OTM prints no-bid sometimes
                # Volume/OI: ATM and near-dated are the active contracts
                activity = math.exp(-((math.log(k / spot)) ** 2) / (2 * 0.25 ** 2))
                activity *= (30.0 / dte) ** 0.7
                lam_vol = max(0.3, 250.0 * activity)
                vol = int(rng.poisson(lam_vol)) if activity > 0.02 else int(rng.poisson(0.4))
                oi = int(max(0, rng.normal(vol * 3.0 + 40 * activity, 60)))
                rows.append({
                    "date": str(asof),
                    "expiration": str(exp),
                    "strike": k,
                    "right": right,
                    "bid": bid,
                    "ask": ask,
                    "volume": vol,
                    "open_interest": oi,
                    "underlying": round(spot, 2),
                })
    chain = pd.DataFrame(rows)
    chain = chain.sort_values(["expiration", "right", "strike"]).reset_index(drop=True)
    return chain


def generate_chain_series(start: dt.date | str, n_days: int, spot0: float,
                        seed: int = 7,
                        expirations_days: tuple[int, ...] = (7, 14, 30, 60, 90, 150),
                        rng_scale: float = 1.0,
                        daily_vol: float = 0.045,
                        mean_reversion: float = 0.06,
                        long_run_spot: float = 18.0) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Generate a deterministic multi-day series of synthetic VIX chains.

    Spot follows a mean-reverting lognormal-ish walk (VIX-like: daily_vol ~
    4.5%, pulls back toward long_run_spot). Each day's chain is generated
    with seed + day_index so the series is fully reproducible.

    Returns (spots, chains): `spots` has columns date/spot; `chains` is the
    concatenated canonical chain rows across all days.

    HONEST LIMITS (harness, not edge discovery): the option surface has no
    vol-of-vol smile dynamics, no spot/surface correlation, and expirations
    are fixed day offsets rather than real monthly cycles. Use this to test
    loop mechanics, cost sensitivity, and kill criteria — never to claim an
    edge exists.
    """
    start = pd.Timestamp(start).date()
    rng = np.random.default_rng(seed)
    log_spot = math.log(spot0)
    dates, spots = [], []
    for i in range(n_days):
        dates.append(start + dt.timedelta(days=i))
        spots.append(math.exp(log_spot))
        shock = rng.normal(0.0, daily_vol)
        log_spot = (log_spot + mean_reversion * (math.log(long_run_spot) - log_spot)
                    + shock)
        log_spot = max(log_spot, math.log(5.0))
    spot_df = pd.DataFrame({"date": [str(d) for d in dates],
                            "spot": [round(s, 2) for s in spots]})
    # Absolute monthly-cycle expirations, shared across days: DTE shrinks
    # realistically as the series progresses; expired dates drop out of the
    # daily chain (generate_chain skips dte <= 0).
    expirations = [start + dt.timedelta(days=d) for d in expirations_days]
    frames = [generate_chain(d, s, seed=seed + 1000 + i,
                             expirations=expirations,
                             rng_scale=rng_scale)
              for i, (d, s) in enumerate(zip(dates, spots))]
    return spot_df, pd.concat(frames, ignore_index=True)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="2026-09-16")
    ap.add_argument("--spot", type=float, default=16.97)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    chain = generate_chain(args.date, args.spot, seed=args.seed)
    out = args.out or f"vix_chain_synth_{args.date}.csv"
    chain.to_csv(out, index=False)
    print(f"wrote {len(chain)} rows -> {out}")
    print(chain.groupby("right")[["bid", "ask"]].describe().round(2).to_string())
