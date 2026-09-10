# VIX Term-Structure Backtest

Rebuild of a 2013 UW–Madison master's thesis strategy: a dynamic trading
strategy for VIX futures based on daily re-estimation of term-structure
parameters (Dupoyet–Daigler–Chen model), trading next-day model-vs-market
mispricings. Rebuilt as a modern Python backtester with honest execution-cost
modeling, toward a paper-traded track record and a potential volatility
newsletter.

## Headline results

- **Thesis reproduced** (2006-01-03 → 2013-02-06, t=1, f=1): net 21.3%/yr vs
  thesis 19.6%/yr; vol 12.1% vs 12.5%; Sharpe 1.52 vs 1.44; win rate 51.4% vs
  51.2%. See `results/validation.md`.
- **Extension to 2026** (2013-02-07 → 2026-09-08): edge decayed — 11.2%/yr net
  overall, crisis alpha through 2021, roughly flat since ~2022.
  See `results/extension_2013_2026.md`.
- **Execution costs (featured finding): the edge does NOT survive realistic
  slippage.** Gross edge ≈ $169/day (≈ $56/contract) vs slippage ≈ $205/day at
  optimistic 0.5× tiers, $409/day at 1× — the signal trades wide-spread back
  months. Only TAS fills at exactly the settlement price preserve the edge;
  +1 adverse tick cuts validation to +3.1%/yr and ruins the extension.
  See `results/equity_slippage_extension.png`.

The thesis assumed tradeable closing prices. This rebuild replaces that with
slippage modeled from recent market data (DTM-bucketed tiers, labeled
ASSUMPTION until measured against live spreads) plus a TAS (trade-at-settlement)
fill scenario as the guaranteed-close-price model.

## Phases

| Phase | What | Status |
|---|---|---|
| 1 | Backtest infra: data pipeline, model fitting, engine, thesis validation, extension to 2026 | Done |
| 1b | Execution-cost sweep: slippage tiers 0/0.5×/1×/2×, TAS at settle+0.00/+0.05 | Done |
| 2a | Front-month-only variant | Done (`results/phase2_front_month.md`) |
| 2b | Back-month persistent short + long-option hedge variants | Done (`results/phase2_short_backmonth.md`, `results/phase2_3b_persistent_short.md`) |
| 2c | Dead-band (no-trade band) variant | **Placeholder** — results pending, see `results/phase2_3c_deadband.md` |
| 3 | Daily signal generator + paper-trade ledger | In progress (`src/daily_run.py`, `src/ledger.py`) |
| 4 | IBKR paper execution via API | Planned (paper account required) |
| 5 | Newsletter reporting | Planned (`reports/`, `docs/paper_outline.md`) |

## Layout

- `data/raw/` — downloaded archives (CBOE per-contract settles/volume/expiries) + `PROVENANCE.md`
- `data/processed/` — clean daily panel (`vx_panel.csv`) + `BUILD_NOTES.md`
- `src/` — `fetch.py`, `panel.py`, `model.py`, `signals.py`, `backtest.py`, `slippage.py`, `report.py`, `daily_run.py`, `ledger.py`, `ibkr_paper.py`, `short_back.py`
- `run_backtest.py` — top-level runner (validation + extension + slippage sweep)
- `run_phase2.py` / `run_phase2b.py` / `run_phase2c.py` — phase variant runners
- `results/` — summary write-ups + charts (bulk per-day CSVs are regenerable and not committed)
- `docs/` — `paper_outline.md` (paper section plan), `ibkr_paper_setup.md`
- `reports/` — newsletter-ready summaries with disclaimers

## Reproduce

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python3 src/fetch.py        # download raw data (CBOE + Yahoo ^VIX); ~5 min, polite 0.4s/request
python3 run_backtest.py     # build panel, validation 2006–2013 + extension + slippage sweep
# faster reruns:
python3 run_backtest.py --skip-panel --only validation
python3 run_backtest.py --skip-panel --only slippage
```

Data notes:
- CBOE `cdn.cboe.com` bulk-year URLs returned 403 at build time; per-contract URLs used instead — see `data/raw/PROVENANCE.md`.
- Price rule: CBOE file layouts changed over time. 2006–2012 archive: `Settle` is the settlement. 2013-era files: `Settle`=0, settlement is in `Close`. Recent files: `Settle`=settlement, `Close`=last trade. Panel uses `Settle` when > 0 else `Close` when > 0.
- Pre-2007-03-26 archive settles are 10× (old quotation); divided by 10.

## Disclaimer

Research and educational use only. Nothing here is financial advice. No
real-money trading — paper trading only, and no broker credentials are stored
in this repo at any phase.
