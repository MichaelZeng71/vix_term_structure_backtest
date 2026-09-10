# Phase 2c — No-trade band (dead zone) results

**Spec:** keep the daily term-structure refit (t=1, f=1); per tenor, trade only
when `|predicted_price - actual_price| > band(dtm)`; inside the band, **hold**
the existing position (no flatten, no reversal). Window: 2006-01-03 ->
END, full continuous window.

**Band design:** `band(dtm) = base_ticks x tier_factor(dtm) x 0.05 pts`,
tier factors (1, 2, 3, 5) for (<60, 60-120, 120-210, >210) days to maturity —
same bucketing as the slippage tier table. Back months get wider bands and
trade less often; the flatten-day-before-expiry rule is unchanged.

## Honest verdict (fill after run)

## Config summary (fill after run)

## Numbers (fill after run)

## Does the band fix the back-month slippage problem? (fill after run)

## Reproduction

```
cd ~/workspace/vol-strategy
python3 run_phase2c.py      # ~30-35 min, writes results/phase2_3c_daily_*.csv
```
