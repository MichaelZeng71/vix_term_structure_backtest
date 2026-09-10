# Validation — thesis period (2006-01-03 → 2013-02-06)

Reproduction of Miao (2013), Table II, using the t=1, f=1 specification
(daily re-estimation on 1 day of data), plus the four alternative (t, f)
specifications the thesis compares against.

## Headline comparison — t=1, f=1

| Metric | Thesis (gross) | Repro (gross) | Thesis (net) | Repro (net) |
|---|---|---|---|---|
| Total return | 260.47% | **301.02%** | 249.07% | **290.36%** |
| Annualized return | 20.10% | **21.74%** | 19.55% | **21.28%** |
| Annualized volatility | 12.18% | **11.89%** | 12.46% | **12.14%** |
| Sharpe | 1.52 | **1.58**¹ | 1.44 | **1.52**¹ |
| Maximum drawdown | −11.49% | **−10.72%** | −11.89% | **−11.09%** |
| Winning proportion (daily P&L > 0) | 51.23% | **—** | 51.18% | **51.41%** |

¹ Sharpe with a 1.6%/yr risk-free rate. The thesis does not state its risk-free
assumption; its Sharpe (1.52 gross / 1.44 net) is consistent with ≈1.6%/yr
(20.10 − 1.52×12.18 = 1.59). With a 0% risk-free rate the repro Sharpe is
1.83 gross / 1.75 net.

Sample: 1,779 trading days (thesis: 1,786), 94 listed monthly contracts
(thesis: 94 — exact match). The 7-day gap is CBOE archive trading days with no
usable settlement rows; it does not materially affect annualized statistics.

**Verdict: the thesis result reproduces.** Every risk and hit-rate statistic
lands within a few percent of the thesis; returns run ≈1.5–2 pp/yr higher,
well within the band explained by implementation choices below.

## Why the small return gap? (known, documented differences)

1. **Close vs settle.** The thesis says "closing prices"; the repro uses the
   CBOE official daily settlement (`Settle` column, falling back to `Close`
   when `Settle`=0 — required for the 2013+ file layout). On most days the two
   differ by a few cents.
2. **Optimizer.** Thesis used Excel Solver (GRG Nonlinear); repro uses
   `scipy.optimize.least_squares` with A∈(0,1), B∈(−100,300). Same objective,
   occasionally different local minima on chaotic days.
3. **Pre-2007 quotation rescale.** Archive settles before 2007-03-26 are 10×
   (verified: 120.20 → 12.02 on 2006-01-03); the repro divides by 10 and uses
   the $1,000 multiplier throughout, keeping economics identical.
4. **7 fewer trading days** (archive gaps).
5. **Estimation-day phasing for f=5/f=10 specs** (repro re-estimates on days
   k ≡ 0 mod f); the thesis does not specify its phase.

## Alternative (t, f) specifications — validation window

| Spec | Net total | Net ann. | Ann. vol | Sharpe¹ | Max DD |
|---|---|---|---|---|---|
| t=1, f=1 (thesis) | 290.36% | 21.28% | 12.14% | 1.52 | −11.09% |
| t=5, f=1 | 103.85% | 10.61% | 28.85% | 0.44 | −65.96% |
| t=5, f=5 | 79.23% | 8.62% | 40.17% | 0.37 | −68.77% |
| t=10, f=1 | −7.67% | −1.12% | 57.58% | 0.24 | −81.00% |
| t=10, f=10 | −8.31% | −1.22% | 165.11% | 0.64 | −96.01% |

The thesis's qualitative finding reproduces: **only the fast (t=1, f=1)
specification works in-sample; slower specifications are far worse**
(the thesis's Table III is partially garbled in the PDF, but its t=5,f=1
net ≈ 89.87%/9.59% is close to the repro's 103.85%/10.61%).

## Exposure & turnover — validation, t=1, f=1 (net)

| | Gross exposure | Net exposure | Turnover |
|---|---|---|---|
| Median | 64% | −2% | 0.012% |
| Mean | 62% | −5% | 0.016% |
| p95 | 109% | +27% / −41% (p5) | 0.047% |
| Max | 155% | +93% / −87% | 0.107% |

- Gross exposure "usually below 100%, max 177.5%" (thesis): repro usually below
  100%, max 155%. ✓
- Fees: $5.99/day average (≈3.0 contracts changed/day at $2/side).
- **Turnover discrepancy:** the thesis reports mean daily turnover of 10.43%,
  which is irreconcilable with ±1-contract positions on a $100k account
  (it would require ~1,000+ contracts/day). The repro's fee-implied trading
  intensity matches the thesis's gross-vs-net return gap (≈3–4% fee drag in
  both), so the P&L mechanics agree; the thesis's turnover table appears to
  have a units/scaling quirk. Noted, not chased.

## Execution-cost sensitivity — t=1, f=1 (new)

Slippage is a first-class backtest parameter (`src/slippage.py`):
adverse half-spread per fill, tiered by days-to-maturity
(<60d: 1 tick, 60–120d: 2, 120–210d: 3, >210d: 5 ticks; 1 tick = 0.05 pts =
$50/contract/side). **Tiers are labeled ASSUMPTIONS** — no free public
VX bid/ask source exists; replace with measured IBKR spreads later. The
0.5x/1x/2x sweep is the real output. TAS variants fill at the official
settle + 0.00 / + 0.05. Full analysis in `results/extension_2013_2026.md`.

| Scenario | Total (net) | Ann. | Sharpe¹ | Max DD | Final PV | Slippage paid |
|---|---|---|---|---|---|---|
| slip0 (baseline) | 290.36% | 21.28% | 1.52 | −11.09% | $390,358 | $0 |
| slip0.5x (optimistic) | −73.72% | −17.24% | −0.29 | <−100% | $26,283 | $364,075 |
| slip1x (base) | −437.79% | ruin | n/a | <−100% | −$337,792 | $728,150 |
| slip2x (conservative) | −1165.94% | ruin | n/a | <−100% | −$1,065,942 | $1,456,300 |
| tas0 | 290.36% | 21.28% | 1.52 | −11.09% | $390,358 | $0 (= slip0 ✓) |
| tas005 (+0.05) | 23.81% | 3.07% | 0.19 | −38.10% | $123,808 | $266,550 |

¹ Sharpe at ≈1.6%/yr risk-free. "ruin" = beyond −100% under fixed ±1-contract
sizing (a real account stops at zero).

**The strategy's gross edge (≈$169/day, ≈$56/contract) is smaller than its
execution cost at every tier multiple** (≈$205/day at 0.5x, $409/day at 1x;
mean $142/contract/side at 1x — the signal trades wide-spread back months).
The thesis's 20%/yr exists only in a frictionless backtest.

## Files

- `results/stats_validation.json` — full statistics (all specs, gross & net)
- `results/daily_validation_{t1_f1,t5_f1,t5_f5,t10_f1,t10_f10}.csv` — daily ledger
- `results/equity_validation_t1_f1.png` — equity curve vs thesis Figure 4
- `results/validation_exposure.json` — exposure/turnover summary above
- `results/stats_slippage.json` — execution-cost sensitivity (t=1,f=1, both windows)
- `data/processed/BUILD_NOTES.md`, `data/raw/PROVENANCE.md` — data lineage

## Reproduce

```bash
cd ~/workspace/vol-strategy
python3 src/fetch.py        # one-time download (~5 min)
python3 run_backtest.py --skip-panel --only validation
```
