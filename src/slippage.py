"""Execution-cost model: adverse slippage as a function of days-to-maturity.

Design notes
------------
- Fills are assumed to occur at/near the decision-day official daily
  settlement (the historical `settle` series IS the settlement price, so for
  TAS-style execution there is no price-guessing involved).
- Slippage is charged adverse to the trade direction, per fill (per side):
      slippage_cost = |dP| * slip_points * MULTIPLIER
  and deducted from portfolio value on the day the new position takes effect,
  alongside the $2/contract commission (kept as a separate ledger line).
- Tier table drop-in: replace SLIPPAGE_TIERS below (or pass `tiers=` into
  `tier_slippage_points` / `run_backtest`) — no other code changes needed.

ASSUMPTION — placeholder tiers (NOT measured)
---------------------------------------------
No free public source publishes VX bid/ask spreads (two sources checked —
both last-price only). The tiers below are labeled assumptions, to be replaced
with measured IBKR paper-trading spreads when available. Values are the
adverse HALF-spread per fill in index points (fills assumed vs printed settle
≈ mid):

    <60d:   1 tick  (0.05 pts)
    60-120d: 2 ticks (0.10 pts)
    120-210d: 3 ticks (0.15 pts)
    >210d:   5 ticks (0.25 pts)

Scenario convention used by the backtest:
    0.5x tiers  -> optimistic (quarter-spread adverse)
    1.0x tiers  -> base case (half-spread adverse)
    2.0x tiers  -> conservative (full-spread adverse)
    0           -> no slippage (thesis baseline)

TAS (Trade at Settlement) execution is modeled separately: fills at the
official daily settlement price. Variants: +0.00 (optimistic) and +0.05
adverse differential (conservative — one outright tick for crossing the TAS
spread). See TAS caveats in results/extension_2013_2026.md.
"""

TICK_POINTS = 0.05      # one VX outright tick = 0.05 index points
MULTIPLIER = 1000.0     # contract multiplier: $ per index point

# (exclusive upper dtm bound, adverse HALF-spread in index points)
# *** ASSUMPTION — placeholder; replace with measured IBKR spreads ***
SLIPPAGE_TIERS = [
    (60, 1 * TICK_POINTS),
    (120, 2 * TICK_POINTS),
    (210, 3 * TICK_POINTS),
    (float("inf"), 5 * TICK_POINTS),
]


def tier_slippage_points(dtm, scale=1.0, tiers=None):
    """Adverse slippage in index points for a contract `dtm` days to expiry.

    `scale`: 0.5 = optimistic, 1.0 = base (half-spread), 2.0 = conservative
    (full spread). `tiers`: drop-in replacement table, same
    [(dtm_bound_exclusive, half_spread_points), ...] format.
    """
    tiers = SLIPPAGE_TIERS if tiers is None else tiers
    if dtm != dtm:  # NaN -> front-tier default (unknown tenor treated as front)
        return scale * tiers[0][1]
    for bound, pts in tiers:
        if dtm < bound:
            return scale * pts
    return scale * tiers[-1][1]


def describe_tiers(tiers=None):
    tiers = SLIPPAGE_TIERS if tiers is None else tiers
    lines = []
    prev = 0
    for bound, pts in tiers:
        label = f">{prev}d" if bound == float("inf") else f"{prev}-{int(bound)}d"
        lines.append(f"{label}: {pts / TICK_POINTS:.0f} tick(s) half-spread "
                     f"({pts:.2f} pts = ${pts * MULTIPLIER:.0f}/contract/side)")
        prev = int(bound) if bound != float("inf") else prev
    return lines
