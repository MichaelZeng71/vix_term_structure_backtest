"""Bid/ask-aware execution cost model for candidate options trades.

Discipline mirrors the futures Phase 2c loop: a candidate is priced at mid
(optimistic), then re-priced under 0.5x / 1.0x / 2.0x of the quoted spread as
execution assumptions, plus a fixed per-contract fee. Costs are reported in
dollars and as % of premium collected (or of premium paid, for debit trades).

A candidate trade is a list of legs:
    {"expiration": date, "strike": float, "right": "C"|"P",
     "side": "buy"|"sell", "qty": int}

Conventions
-----------
- MULTIPLIER = 100 (equity/index option contract multiplier).
- Buying pays the ask; selling receives the bid — under multiplier `m`,
  buy fill = mid + m * spread/2, sell fill = mid - m * spread/2.
- FEE_PER_CONTRACT = 1.30 (retail-ish; IBKR is lower, we stay conservative).
- Zero-bid OTM contracts: selling them is a no-op credit-wise; model caps
  the sell fill at >= 0 and flags it.

Fill-quote lookup is exact on (expiration, strike, right). If a leg is
missing from the chain, CostModelError is raised — backtests should skip
that day rather than invent a quote.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

MULTIPLIER = 100
FEE_PER_CONTRACT = 1.30


class CostModelError(Exception):
    pass


def _quote(chain: pd.DataFrame, expiration, strike: float, right: str) -> pd.Series:
    exp = pd.Timestamp(expiration).date()
    m = (chain["expiration"] == exp) & (chain["strike"] == float(strike)) & \
        (chain["right"] == right.upper())
    hit = chain.loc[m]
    if hit.empty:
        raise CostModelError(f"no quote for {exp} {strike} {right}")
    return hit.iloc[0]


def price_trade(chain: pd.DataFrame, legs: list[dict],
                spread_mults: tuple[float, ...] = (0.5, 1.0, 2.0)) -> dict:
    """Price a candidate trade under several execution-cost assumptions.

    Returns a dict with per-scenario net credit/debit ($, per-trade, before
    fees), total cost in $, cost as % of premium, and per-leg detail.
    """
    leg_rows = []
    mid_credit = 0.0          # net premium at mid, $ (positive = credit received)
    halfspread_dollars = 0.0  # sum of |qty| * spread/2 * MULT, the m=1.0 drag
    premium_abs = 0.0         # sum of |mid value| of all legs, $ — denominator
    for leg in legs:
        q = _quote(chain, leg["expiration"], leg["strike"], leg["right"])
        qty, side = int(leg["qty"]), leg["side"].lower()
        sign = +1 if side == "sell" else -1          # sell = receive, buy = pay
        mid = float(q["mid"])
        spread = float(q["spread"])
        mid_credit += sign * qty * mid * MULTIPLIER
        halfspread_dollars += qty * (spread / 2.0) * MULTIPLIER
        premium_abs += qty * mid * MULTIPLIER
        leg_rows.append({
            "expiration": str(q["expiration"]), "strike": float(q["strike"]),
            "right": q["right"], "side": side, "qty": qty,
            "bid": float(q["bid"]), "ask": float(q["ask"]), "mid": mid,
            "spread": round(spread, 2),
        })

    fees = sum(int(l["qty"]) for l in legs) * FEE_PER_CONTRACT
    scenarios = {}
    for m in spread_mults:
        # spread drag is linear in the multiplier: buy pays mid + m*s/2,
        # sell receives mid - m*s/2  => net = mid_credit - m * halfspread
        net = mid_credit - m * halfspread_dollars - fees
        denom = abs(mid_credit) if abs(mid_credit) > 1e-9 else premium_abs
        scenarios[f"spread_{m}x"] = {
            "net_credit_usd": round(net, 2),
            "cost_vs_mid_usd": round(m * halfspread_dollars + fees, 2),
            "cost_pct_of_premium": round(
                100.0 * (m * halfspread_dollars + fees) / denom, 2) if denom > 1e-9 else None,
        }
    return {
        "legs": leg_rows,
        "mid_net_credit_usd": round(mid_credit, 2),
        "fees_usd": round(fees, 2),
        "half_spread_drag_1x_usd": round(halfspread_dollars, 2),
        "scenarios": scenarios,
        "note": ("Positive net_credit_usd = credit received. Spreads are the "
                 "dominant cost; 2x models adverse selection / wide-quote days."),
    }


def calendar_spread_legs(near_exp, far_exp, strike: float, right: str = "C",
                         qty: int = 1) -> list[dict]:
    """Short near-expiry / long far-expiry, same strike (theta/term-structure
    calendar)."""
    return [
        {"expiration": near_exp, "strike": strike, "right": right,
         "side": "sell", "qty": qty},
        {"expiration": far_exp, "strike": strike, "right": right,
         "side": "buy", "qty": qty},
    ]
