# Options track — harness (run 1, Sep 16 2026)

Bid/ask-aware tooling for the options side of the volatility-strategy goal.
Pure harness: no live trading, no real-money advice, no paid data.

## What's here

| File | Purpose |
|---|---|
| `fixture_gen.py` | Deterministic synthetic VIX option-chain generator (seed=7). Black76 skeleton on a contango'd forward; spreads widen with strike distance from spot and with tenor, roughly matching known wide VIX option spreads. Writes canonical-schema CSV. |
| `ingest.py` | `ingest_chain(path, format='auto'|'canonical'|'orats', ticker_filter='VIX')` → `(chain, report)`. Normalizes dtypes, drops unparseable rows, **flags** crossed markets (bid>ask) and negatives instead of crashing; `strict=True` raises instead. Auto-detects the real ORATS Strikes column format and pivots call/put legs into canonical long form. |
| `costs.py` | `price_trade(chain, legs, spread_mults=(0.5, 1.0, 2.0))`. Prices any multi-leg candidate at mid, then under 0.5x/1x/2x spread execution assumptions + $1.30/contract fee. Reports net credit/debit in $ and cost as % of premium. Buy pays ask-side, sell receives bid-side; same cost-sensitivity discipline as the futures Phase 2c loop. Includes `calendar_spread_legs()` helper (short near call / long far call). |
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

## Recommended next step

Wire a day-level loop: ingest one canonical chain per date, evaluate the same
calendar (or vertical) candidate mechanically each day, and accumulate net
P&L-at-1x-spread in a ledger — the options analog of the futures walk-forward
sim. Needs a real multi-day chain dataset; free paths: IBKR delayed/live
chain pulls once approved, or re-check Databento pay-as-you-go cost for one
daily VIX snapshot. No purchases without Miao's explicit approval.
