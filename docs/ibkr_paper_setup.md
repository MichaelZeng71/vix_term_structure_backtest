# IBKR paper trading setup (for live simulated execution)

You only need this when the backtest validates and you want to "click the
buttons yourself" in simulation mode. The paper ledger (layer 1) needs no
broker at all.

## 1. Open an Interactive Brokers account
- https://www.interactivebrokers.com — individual account, ~15 min.
- No funding required to use paper trading.

## 2. Enable the paper trading account
- In Client Portal: Manage Account → Settings → Paper Trading Account → create.
- IBKR gives the paper account **$1,000,000 in simulated money**.
- It mirrors real market prices; orders simulate fills without touching real money.

## 3. Install Trader Workstation (TWS) or IB Gateway
- Download from the IBKR site. Log in with your **paper trading** username
  (it ends in something like `xxxxxx_paper`, or choose "Paper Trading" at login).
- Enable API: in TWS → Global Configuration → API → Settings →
  check "Enable ActiveX and Socket Clients". Note the port:
  TWS paper = **7497**, Gateway paper = **4002**.

## 4. Market data
- VIX futures trade on CFE. For end-of-day signals, delayed data is fine
  (our runner uses daily settles fetched independently anyway).
- If you want live quotes in TWS, subscribe to US Futures data (a few $/mo,
  waived above commission thresholds — irrelevant on paper).

## 5. Python bridge
- `pip install ib_insync`
- Our `src/ibkr_paper.py` connects to the paper port, builds VX contracts on
  CFE, and routes the day's signals. It **refuses live ports** and defaults
  to dry-run (prints orders instead of placing them).

## 6. First run (dry)
1. Start TWS/Gateway, log into PAPER.
2. `python -m src.daily_run --date <today> --to-ibkr` → prints what it *would* do.
3. When comfortable: add `--live-fire` to actually transmit to the paper account.
4. Verify fills in TWS paper blotter; they mirror into the paper ledger's NAV.

## Notes
- The strategy flattens everything the day before front-month expiry, so you
  never hold into settlement week — no expiry-day surprises in the sim.
- Keep a written log of any manual overrides; the newsletter track record must
  disclose deviations from the system.
