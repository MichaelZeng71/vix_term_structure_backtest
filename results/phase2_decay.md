# Phase 2, Task 2 — Why did the edge die after 2022?

> Educational research only — not financial advice. This is a diagnosis of a
> backtest, not a claim about market causation. Microstructure conclusions are
> limited by the data: the panel has volume but **no bid/ask history**, so
> spread/efficiency claims are inference, flagged as such.

## Method

Per trading day, 2006–2026 (5,195 days with spot VIX), I fit the thesis
(A, B) model on that day's cross-section and measured:

- **Curve shape:** steepness = (back − front)/front where back = farthest
  listed monthly with volume ≥ 10; per-30-day slope; contango frequency;
  spot VIX level; fitted A (mean-reversion speed) and B (long-run mean);
  fit RMSE.
- **Signal the model detects:** mean |forecast − settle|/settle over
  tradeable contracts (the gross mispricing the strategy tries to harvest).
- **Microstructure proxies available:** median daily volume by maturity
  bucket; strategy turnover (from Phase 1 ledgers).
- **P&L attribution:** re-ran t=1,f=1 (no slippage) capturing positions;
  every contract-day's gross P&L bucketed by fill-day DTM, plus directional
  hit rate and win/loss payoff asymmetry per bucket × era.

Script: `phase2_diagnose.py`. Charts: `phase2_decay_curve.png`,
`phase2_decay_rolling.png`, `phase2_decay_pnl_maturity.png`.

## Finding 1: the term structure did NOT get flatter

| Era | Spot VIX | Steepness (back−front)/front | Slope /30d | Contango freq | Fitted A |
|---|---|---|---|---|---|
| 2006–2013 | 21.7 | 15.7% | 0.29 | 77% | 0.947 |
| 2013–2021 | 17.3 | 19.3% | 0.32 | 84% | 0.959 |
| 2022–2026 | 19.2 | 16.9% | 0.36 | 86% | 0.972 |

The post-2022 curve is, if anything, *steeper* per 30 days and in contango
*more* often (86%). Spot VIX averaged 19.2 — not a dead-vol regime. The
"flatter/richer curve" hypothesis is **rejected** by these measures.

## Finding 2: the decay is concentrated in the FRONT of the curve

Gross P&L ($k/year) by fill-day maturity bucket, t=1,f=1, no slippage:

| Era | <60d | 60–120d | 120–210d | >210d |
|---|---|---|---|---|
| 2006–2013 | +199 | +40 | +37 | +43 |
| 2014–2021 | +219 | +61 | +8 | +39 |
| 2022–2026 | **−84** | **+72** | +6 | +4 |

The front bucket — the strategy's profit engine in both earlier eras —
flipped to a large loser after 2022, while the 60–120d bucket actually
*improved*. The edge didn't vanish uniformly; it migrated/failed at the
front.

## Finding 3: the front-month signal lost its payoff asymmetry

Per-contract-day economics, front (<60d) bucket:

| Era | Hit rate | Avg win | Avg loss | Payoff ratio | Mean $/contract-day |
|---|---|---|---|---|---|
| 2014–2021 | 47.6% | +$703 | −$700 | 1.00 | +$64.5 |
| 2022–2026 | 45.4% | +$607 | −$686 | **0.89** | **−$40.9** |

vs 60–120d bucket, 2022–2026: hit 47.4%, payoff ratio **1.05**, +$33.8/cd.

Post-2022 the model still *detects* mispricings at the front (signal
magnitude 1.03% vs 1.10–1.16% earlier — only slightly down), but they no
longer predict next-day moves: wins got smaller, losses got bigger, and the
hit rate slipped below its earlier level. The smoking gun is **2024**: the
highest detected mispricing of any year (1.83%) paired with a −4.8% strategy
return — the "mispricing" had become noise.

Supporting evidence for faster daily repricing at the front:

- Fitted **A rose to 0.972** (from 0.947/0.959): the estimator sees slower
  mean reversion — the curve's daily dynamics look more like a random walk.
  (Yearly strategy return vs annual mean A: correlation −0.67, n=21 —
  suggestive, not proof.)
- Front-median daily volume stayed ~79k contracts (vs 85k in 2013–2021):
  liquidity did **not** dry up — the market got more competitive, not
  thinner.
- Median turnover drifted down over time: fewer position changes as fewer
  detected deviations were worth trading.

## Diagnosis (honest version)

The post-2022 flatness is **not** about a flatter or richer term structure —
measured steepness, contango frequency, and spot VIX all reject that. It is
about the **predictable component of daily front-month moves decaying**:
small term-structure deviations at the front are now repriced before the
next close, leaving the t=1 signal trading noise with negative payoff
asymmetry (0.89). This is *consistent with* a more efficient daily market —
tighter HFT market-making and post-2018 vol-ETP arbitrage crowding — but the
data here (volume only, no spreads/quotes) cannot prove a microstructure
cause; treat that as the leading hypothesis, not a finding. The slower-
parameter reversal (t=10 beating t=1 out-of-sample, Phase 1) fits the same
story: the daily signal became noise while slower-moving premia persisted.

## Caveats

- Yearly correlations use n=21 — noisy; the A correlation (−0.67) is a hint.
- "Back" = farthest *liquid* (vol ≥ 10) monthly; definitions are documented
  in the script.
- Attribution is gross of slippage by construction (the question is about
  the gross edge's decay).

## Files

- `results/phase2_diag_daily.csv`, `phase2_diag_yearly.csv`,
  `results/phase2_diag_era.csv`, `results/phase2_diag_pnl_bucket.csv`,
  `results/phase2_diag_contractdays_bucket.csv`, `results/phase2_diag_corr.json`
- `results/phase2_decay_curve.png`, `results/phase2_decay_rolling.png`,
  `results/phase2_decay_pnl_maturity.png`
- Reproduce: `python3 phase2_diagnose.py`
