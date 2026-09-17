# Options track — harness (run 1, Sep 16 2026)

Bid/ask-aware tooling for the options side of the volatility-strategy goal.
Pure harness: no live trading, no real-money advice, no paid data.

## What's here

| File | Purpose |
|---|---|
| `fixture_gen.py` | Deterministic synthetic VIX option-chain generator (seed=7). Black76 skeleton on a contango'd forward; spreads widen with strike distance from spot and with tenor, roughly matching known wide VIX option spreads. Writes canonical-schema CSV. `generate_chain_series()` emits a multi-day series: mean-reverting spot walk + one shared absolute monthly expiration calendar (DTE shrinks realistically, expired dates drop out). |
| `ingest.py` | `ingest_chain(path, format='auto'|'canonical'|'orats', ticker_filter='VIX')` → `(chain, report)`. Normalizes dtypes, drops unparseable rows, **flags** crossed markets (bid>ask) and negatives instead of crashing; `strict=True` raises instead. Auto-detects the real ORATS Strikes column format and pivots call/put legs into canonical long form. |
| `costs.py` | `price_trade(chain, legs, spread_mults=(0.5, 1.0, 2.0))`. Prices any multi-leg candidate at mid, then under 0.5x/1x/2x spread execution assumptions + $1.30/contract fee. Reports net credit/debit in $ and cost as % of premium. Buy pays ask-side, sell receives bid-side; same cost-sensitivity discipline as the futures Phase 2c loop. Includes `calendar_spread_legs()` helper (short near call / long far call). |
| `day_loop.py` | Day-level walk-forward: each day enter one mechanical ATM call calendar (short near-exp call / long far-exp call, strike = closest-to-spot strike quoted on BOTH expirations), hold `--hold-days` (default 5), exit both legs. Entry AND exit priced at 0.5x/1x/2x spread + fees; cohorts with missing exit legs are skipped, never invented. Reports per-scenario P&L, win rate, avg cost drag vs mid-only. `python3 day_loop.py --dir <daily chain dir>`. |
| `test_harness.py` | Demo: generates fixtures → ingests → prices an example calendar spread on synthetic AND real ORATS data. `python test_harness.py --orats <strikes csv>`. |
| `vix_chain_synth_2026-09-16.csv` | 664-row synthetic fixture (spot 16.97, 6 expirations). |

Canonical chain schema: `date, expiration, strike, right(C/P), bid, ask, volume, open_interest, underlying`.

Real ORATS sample lives in `../hidden_files/options/`:
`orats_vix_2024-01-03.csv` (729 VIX rows, 13 expirations, real NBBO bid/ask,
Greeks/IVs, snapshot 3:46pm ET) — downloaded free, no signup, from the public
link at https://orats.com/near-eod-data (`orats_sample_source.txt` has provenance).

## Demo results (from test run, 2026-09-16)

Calendar 1x Jan-2024 16C, short 2024-01-03 / long 2024-01-17 (real ORATS data):

| assumption | net credit (1x) | cost vs mid | cost % of premium |
|---|---|---|---|
| mid (optimistic) | −$40.50 | — | — |
| 0.5x spread | −$52.35 | $11.85 | 29.3% |
| 1.0x spread | −$61.60 | $21.10 | 52.1% |
| 2.0x spread | −$80.10 | $39.60 | 97.8% |

Same structure on synthetic data costs only 13.4% at 1x — the real sample
shows how badly mid-pricing lies for VIX calendars. Cost modeling before any
signal work is mandatory, not optional.

## Day-level loop results (run 2, 2026-09-17 — synthetic 20-day series)

20 synthetic days (spot 14.80–17.32, seed=7), ATM call calendar entered daily,
held 5 calendar days, 15 complete cohorts:

| execution | P&L | win rate |
|---|---|---|
| mid (fees only) | +$32.00 | — |
| 0.5x spread | −$373.00 | 0% |
| 1.0x spread | −$778.00 | 0% |
| 2.0x spread | −$1,588.00 | 0% |

Avg cost drag (mid → 1x): $54/cohort against $131 avg entry premium (~41%).
Expected: the synthetic surface has no edge — mid-only P&L is noise around
zero, and the spread drag is the whole story. The loop's job is to quantify
that hurdle mechanically, so when real multi-day chains arrive any signal
must clear it.

## Databento cost check (run 2, research only — no purchase)

OPRA.PILLAR historical pay-as-you-go unit prices: ohlcv-1d $600/GB,
cbbo-1m $2/GB, definition $5/GB. A single daily VIX-options snapshot is
KB-scale after symbol filtering, so one EOD chain/day would likely cost a
few dollars total — far below the $599 ORATS archive. $125 free credit
covers exploration; exact cost via the free `metadata.get_cost` call before
any spend. Caveat: ohlcv-1d has no bid/ask; the bid/ask-aware loop needs a
quote schema (cbbo-1m minute bar) — still cheap at VIX-only scale. No
purchases without Miao's explicit approval.

## Recommended next step

Feed the loop real multi-day chains: either Databento (cost it exactly with
`metadata.get_cost` first) or IBKR chain pulls once Miao approves data
spend. Meanwhile keep the loop honest on synthetic fixtures.
