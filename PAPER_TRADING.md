# Paper trading the VIX term-structure strategy (Mac Mini)

Live simulated execution of the dead-band variant **b5f1** (the winning Phase 2c
config): daily model refit, no-trade band at base 5 ticks (10/15/25 by tenor),
1 tick on the front month. Fake money on real prices — nothing here can touch
the real account.

## What `run_paper.py` does, once a day

1. Connects to IB Gateway on `127.0.0.1:4002` and **asserts the account ID starts
   with `DU`** (paper). Anything else → hard stop, no orders.
2. Pulls the VX futures chain from CFE (monthly contracts only — weeklies are
   filtered out) plus spot VIX, using **delayed** market data (free, no paid
   subscriptions needed).
3. Refits the term-structure model `(A, B)` on today's cross-section with the
   same code as the backtest (`src/model.py`, t=1).
4. Forecasts next-day prices, applies the b5f1 dead band, holds positions that
   sit inside the band, flattens everything the day before front-month expiry.
5. Reconciles target vs current paper positions and emits **marketable limit
   orders at the observed bid/ask** (never naked market orders). One contract
   per signal leg, max 10 per order, max 20 open total.
6. Appends the full audit trail to `paper_ledger.db` (SQLite, next to the script).

## Mac Mini setup (one time)

The Gateway itself is already configured: logged into the paper account,
paper mode, API enabled at port 4002, read-only API unticked.

```bash
cd ~/vix_term_structure_backtest   # wherever you pulled the repo
pip install ib_insync
```

That's it. No market-data subscriptions — the runner requests delayed data.

## Running it

```bash
python3 run_paper.py            # DRY-RUN: prints intended orders, places nothing
python3 run_paper.py --submit   # actually transmit orders to the PAPER account
```

Run **after the VX daily settlement** (~4:30pm CT / 2:30pm PT) so delayed quotes
reflect final settlement prices, matching the backtest's use of closes.

Suggested cadence: once daily via `launchd` or cron, e.g. 3:00pm PT on weekdays.
Keep the Gateway logged into the paper account (it needs a manual login each
day — IBKR sessions expire).

## Safety recap

- Dry-run is the default. `--submit` is the only way orders transmit.
- The DU-account assertion and the live-port rejection run on every start.
- Caps: 10 contracts per order, 20 total open. A book that would exceed the cap
  aborts instead of partially trading.

## Reading the ledger

`paper_ledger.db` lives next to the script. Tables:

| table | contents |
|---|---|
| `runs` | timestamp, mode (dry-run/submit), account, spot, A, B, flatten-day flag |
| `contract_signals` | per-contract bid/ask/last/observed, forecast, band, deviation, in-band flag, current → target position, skip reasons |
| `orders` | action, qty, limit price, dry-run flag, IB order ID, status |
| `fills` | fill callbacks: qty, price, timestamp |

Example:

```bash
sqlite3 paper_ledger.db "SELECT ts, mode, account, printf('%.4f', a), printf('%.2f', b) FROM runs ORDER BY id DESC LIMIT 5;"
sqlite3 paper_ledger.db "SELECT label, observed, forecast, band_points, dev_points, current_pos, target_pos FROM contract_signals WHERE run_id = (SELECT MAX(id) FROM runs);"
```

## Testing

- `python3 -m py_compile run_paper.py` — syntax.
- `python3 test_paper_logic.py` — 33 unit tests on the dead-band math (checked
  against `src/backtest.py`), reconcile/cap logic, and limit-price construction.
  Needs no IB connection.
- **The live IB connection path has NOT been tested from the development VM**
  (no Gateway there). First run on the Mac Mini should be a dry-run; compare
  its printed signals against the backtest's expectations before ever using
  `--submit`.

## Still manual (not automated)

- **Paper-equity reset to $100k** (Client Portal → Settings → Account Settings →
  Paper Trading Account → reset). The backtests assume $100k; the paper account
  defaults to $1M.
- Daily Gateway login (IBKR expires sessions).
- Any manual overrides — log them; the track record must disclose deviations.
