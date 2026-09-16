#!/usr/bin/env python3
"""Paper-trading runner: VIX futures term-structure strategy, dead-band variant b5f1.

Pipeline (mirrors the backtest in src/backtest.py, one live run = one day):
  1. Connect to IB Gateway (paper) at 127.0.0.1:4002.
  2. Pull the VX futures chain from CFE (monthly contracts, all listed tenors)
     plus the spot VIX index, using DELAYED market data (free, no subscriptions).
  3. Refit the term-structure model (A, B) on today's cross-section (t=1, f=1)
     using the existing src/model.py code.
  4. Forecast next-day prices, compare with observed prices, apply the b5f1
     no-trade dead band (base 5 ticks, tier factors 1/2/3/5 by dtm; 1 tick on
     the front month). Inside the band: HOLD the existing position.
  5. Reconcile target positions vs current paper positions; emit marketable
     LIMIT orders (never naked market orders) at the observed bid/ask.
  6. Append everything to a local SQLite ledger (paper_ledger.db).

Safety (non-negotiable):
  * Refuses to run unless the connected account ID starts with 'DU'
    (IBKR paper-trading accounts). The account is auto-detected, never hardcoded.
  * Refuses known LIVE API ports (7496/4001).
  * Default mode is DRY-RUN: prints intended orders, places nothing.
    Real submission only with --submit.
  * Caps: max 10 contracts per order, max 20 total open contracts.

Usage:
  python3 run_paper.py               # dry-run (default)
  python3 run_paper.py --submit      # place paper orders for real

Run after the VX daily settlement (~4:30pm CT / 2:30pm PT) so delayed quotes
reflect final settlement prices, matching the backtest's use of closes.

Educational research tool only — not financial advice, not a live track record.
The live IB connection path has NOT been tested from the development VM
(no Gateway there); first live run should be a dry-run on the Mac Mini.
"""

import argparse
import calendar
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE / "src"))

from backtest import deadband_points, TICK  # noqa: E402
from model import fit_params, forecast_next_day  # noqa: E402

# ---------------------------------------------------------------------------
# Strategy + safety configuration (b5f1 = winning Phase 2c variant)
# ---------------------------------------------------------------------------
DEADBAND_BASE_TICKS = 5.0    # back-month base band; tiers 1/2/3/5 by dtm
DEADBAND_FRONT_TICKS = 1.0   # front-month (dtm < 60) override
VOL_MIN = 10                 # skip contracts with volume below this (thesis)
MULTIPLIER = 1000.0          # VX contract multiplier ($)

PAPER_PORTS = {4002, 7497}   # Gateway paper, TWS paper
LIVE_PORTS = {4001, 7496}    # rejected outright

MAX_CONTRACTS_PER_ORDER = 10
MAX_TOTAL_OPEN = 20

MONTH_CODES = "FGHJKMNQUVXZ"


# ---------------------------------------------------------------------------
# Pure logic (no ib_insync needed — unit-testable)
# ---------------------------------------------------------------------------
def month_code_label(year: int, month: int) -> str:
    """'VXV26' style label for a YYYYMM contract (matches backtest panel)."""
    return f"VX{MONTH_CODES[month - 1]}{year % 100:02d}"


def decide_target_position(dev_points: float, band_points: float,
                           current_pos: int) -> int:
    """b5f1 dead-band decision for one contract.

    dev_points  = forecast - observed (index points).
    band_points = no-trade half-band (index points).
    Returns +1 / -1 / current_pos. Inside the band (|dev| <= band) the
    existing position is HELD (no flatten, no reversal) — this is the
    exact backtest rule from src/backtest.py.
    """
    if abs(dev_points) <= band_points:
        return current_pos
    return 1 if dev_points > 0 else -1


def reconcile_positions(current: dict, target: dict,
                        max_per_order: int = MAX_CONTRACTS_PER_ORDER,
                        max_total_open: int = MAX_TOTAL_OPEN) -> list:
    """Diff current vs target positions into orders.

    current/target: dict key -> signed qty (e.g. conId -> -1/0/+1).
    Returns list of (key, action, qty) with action in {'BUY','SELL'}.
    Raises ValueError if caps would be violated.
    """
    total_open = sum(abs(q) for q in target.values())
    if total_open > max_total_open:
        raise ValueError(
            f"Target book has {total_open} open contracts > cap {max_total_open}; "
            "aborting instead of partially trading.")
    orders = []
    for key in sorted(set(current) | set(target), key=str):
        diff = target.get(key, 0) - current.get(key, 0)
        if diff == 0:
            continue
        if abs(diff) > max_per_order:
            raise ValueError(
                f"Order for {key} needs {abs(diff)} contracts > cap "
                f"{max_per_order}; aborting.")
        orders.append((key, "BUY" if diff > 0 else "SELL", abs(diff)))
    return orders


def limit_price_for(action: str, bid: Optional[float], ask: Optional[float],
                    fallback: float) -> float:
    """Marketable limit price: join the touch (BUY at ask, SELL at bid).

    Never returns a naked market order price; falls back to the observed
    price when the touch is missing. Rounded to the VX tick (0.05).
    """
    px = None
    if action == "BUY" and ask and ask > 0:
        px = ask
    elif action == "SELL" and bid and bid > 0:
        px = bid
    if px is None or px <= 0:
        px = fallback
    # snap to the VX tick (0.05); the extra round() kills binary float dust
    # (e.g. 16.90/0.05 -> 337.999... -> 16.900000000000002)
    return round(round(px / TICK) * TICK, 2)


# ---------------------------------------------------------------------------
# VX monthly-expiry helpers (filter weeklies out of the chain)
# ---------------------------------------------------------------------------
def _third_friday(year: int, month: int) -> date:
    weeks = calendar.monthcalendar(year, month)
    fridays = [w[calendar.FRIDAY] for w in weeks if w[calendar.FRIDAY] != 0]
    return date(year, month, fridays[2])


def is_standard_monthly_vx(yyyymm: str, real_expiry: date) -> bool:
    """True if real_expiry matches the standard VX monthly settlement.

    Rule: the Wednesday 30 days before the third Friday of the calendar month
    immediately following the contract month. (Verified: Sep'26 -> 2026-09-16,
    Oct'26 -> 2026-10-21.)
    """
    y, m = int(yyyymm[:4]), int(yyyymm[4:6])
    ny, nm = (y, m + 1) if m < 12 else (y + 1, 1)
    return real_expiry == _third_friday(ny, nm) - timedelta(days=30)


# ---------------------------------------------------------------------------
# IB connection (ib_insync imported lazily — pure logic stays importable)
# ---------------------------------------------------------------------------
def _ib():
    try:
        import ib_insync
    except ImportError as e:
        raise RuntimeError(
            "ib_insync is not installed. On the Mac Mini run: "
            "pip install ib_insync") from e
    return ib_insync


def connect_ib(host: str, port: int, client_id: int):
    """Connect to Gateway/TWS and assert we are on a PAPER account.

    Raises RuntimeError with a clear message on any safety violation.
    Returns (ib, account_id).
    """
    if port in LIVE_PORTS:
        raise RuntimeError(
            f"Port {port} is a LIVE trading port. This runner only talks to "
            f"paper (Gateway 4002 / TWS 7497). Refusing to run.")
    if port not in PAPER_PORTS:
        raise RuntimeError(
            f"Port {port} is not a known paper-trading port {sorted(PAPER_PORTS)}. "
            "Refusing to run.")
    ibmod = _ib()
    ib = ibmod.IB()
    ib.connect(host, port, clientId=client_id)
    ib.reqMarketDataType(3)  # delayed data — free, no subscriptions needed
    accounts = ib.managedAccounts()
    if not accounts:
        ib.disconnect()
        raise RuntimeError(
            "Gateway reported no managed accounts. Refusing to run.")
    bad = [a for a in accounts if not a.startswith("DU")]
    if bad:
        ib.disconnect()
        raise RuntimeError(
            f"SAFETY STOP: connected account(s) {accounts} are NOT paper-trading "
            "accounts (paper IDs start with 'DU'). Disconnect and log the Gateway "
            "into the paper account before running.")
    account = accounts[0]
    print(f"Connected to paper account {account} via {host}:{port}")
    return ib, account


def fetch_chain(ib, today: date):
    """All listed VX monthly futures on CFE with dtm > 0.

    Returns list of dicts: conId, yyyymm, label, expiry, dtm, contract (ib obj).
    Weeklies are filtered out (strategy universe = monthlies, like the backtest).
    """
    ibmod = _ib()
    details = ib.reqContractDetails(
        ibmod.Future(symbol="VX", exchange="CFE"))
    out = []
    for d in details:
        c = d.contract
        if c.secType != "FUT":
            continue
        yyyymm = (c.lastTradeDateOrContractMonth or "")[:6]
        if len(yyyymm) != 6 or not yyyymm.isdigit():
            continue
        exp_str = d.realExpirationDate or ""
        if len(exp_str) != 8 or not exp_str.isdigit():
            continue
        expiry = date(int(exp_str[:4]), int(exp_str[4:6]), int(exp_str[6:8]))
        dtm = (expiry - today).days
        if dtm <= 0:
            continue
        out.append({"conId": c.conId, "yyyymm": yyyymm,
                    "label": month_code_label(int(yyyymm[:4]), int(yyyymm[4:6])),
                    "expiry": expiry, "dtm": dtm, "contract": c,
                    "monthly": is_standard_monthly_vx(yyyymm, expiry)})
    # keep monthlies; if a contract month has no recognizable monthly, keep
    # everything for that month but flag it (fail-open with a loud warning)
    by_month = {}
    for r in out:
        by_month.setdefault(r["yyyymm"], []).append(r)
    final = []
    for yyyymm, rows in sorted(by_month.items()):
        monthlies = [r for r in rows if r["monthly"]]
        if monthlies:
            final.extend(monthlies)
        else:
            print(f"WARNING: no standard monthly expiry found for {yyyymm}; "
                  f"keeping {len(rows)} listed contract(s) as-is")
            final.extend(rows)
    final.sort(key=lambda r: r["expiry"])
    return final


def fetch_quotes(ib, chain, timeout_s: int = 15):
    """Delayed snapshot quotes for each contract. Returns conId -> quote dict."""
    tickers = {}
    for r in chain:
        t = ib.reqMktData(r["contract"], "", True, False)  # snapshot
        tickers[r["conId"]] = (r, t)
    # wait for snapshots to populate
    for _ in range(timeout_s):
        if all((t.bid and t.bid > 0) or (t.last and t.last > 0)
               for _, t in tickers.values()):
            break
        ib.sleep(1)
    quotes = {}
    for conId, (r, t) in tickers.items():
        bid = t.bid if t.bid and t.bid > 0 else None
        ask = t.ask if t.ask and t.ask > 0 else None
        last = t.last if t.last and t.last > 0 else None
        mid = (bid + ask) / 2 if bid and ask else None
        observed = last if last else mid
        vol = t.volume if t.volume and t.volume >= 0 else None
        quotes[conId] = {"bid": bid, "ask": ask, "last": last,
                         "observed": observed, "volume": vol}
        ib.cancelMktData(r["contract"])
    return quotes


def fetch_spot_vix(ib, timeout_s: int = 10) -> float:
    """Spot VIX index level (delayed). Needed for the model fit."""
    ibmod = _ib()
    c = ibmod.Index("VIX", "CBOE")
    t = ib.reqMktData(c, "", True, False)
    for _ in range(timeout_s):
        if t.last and t.last > 0:
            break
        ib.sleep(1)
    px = t.last if t.last and t.last > 0 else None
    ib.cancelMktData(c)
    if px is None or px <= 0:
        raise RuntimeError(
            "Could not get a spot VIX quote (delayed). Aborting: the model "
            "cannot be fit without spot.")
    return float(px)


def fetch_positions(ib, account: str) -> dict:
    """Current paper positions keyed by conId -> signed qty (futures only)."""
    pos = {}
    for p in ib.positions():
        if p.contract.secType != "FUT" or p.contract.symbol != "VX":
            continue
        pos[p.contract.conId] = int(p.position)
    return pos


# ---------------------------------------------------------------------------
# Ledger (SQLite — fully auditable after the fact)
# ---------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id          INTEGER PRIMARY KEY,
  ts          TEXT NOT NULL,
  mode        TEXT NOT NULL,          -- 'dry-run' or 'submit'
  account     TEXT NOT NULL,
  spot        REAL, a REAL, b REAL,
  n_contracts INTEGER,
  flatten_day INTEGER,                -- 1 if day-before-front-expiry flatten
  notes       TEXT
);
CREATE TABLE IF NOT EXISTS contract_signals (
  run_id      INTEGER NOT NULL,
  label       TEXT NOT NULL,          -- e.g. VXV26
  expiry      TEXT NOT NULL,
  dtm         REAL,
  bid REAL, ask REAL, last REAL, observed REAL, volume REAL,
  forecast    REAL,
  band_points REAL,
  dev_points  REAL,
  in_band     INTEGER,                -- 1 if |dev| <= band (held)
  current_pos INTEGER,
  target_pos  INTEGER,
  skipped     TEXT                    -- reason if skipped (low volume etc.)
);
CREATE TABLE IF NOT EXISTS orders (
  run_id      INTEGER NOT NULL,
  label       TEXT NOT NULL,
  action      TEXT NOT NULL,          -- BUY / SELL
  qty         INTEGER NOT NULL,
  limit_price REAL,
  dry_run     INTEGER NOT NULL,
  ib_order_id INTEGER,
  status      TEXT,
  ts          TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fills (
  run_id      INTEGER NOT NULL,
  ib_order_id INTEGER,
  label       TEXT NOT NULL,
  qty         REAL,
  price       REAL,
  ts          TEXT NOT NULL
);
"""


def init_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------
def run(host: str, port: int, client_id: int, submit: bool,
        db_path: Path) -> int:
    mode = "submit" if submit else "dry-run"
    today = date.today()
    conn = init_db(db_path)
    cur = conn.cursor()

    ib, account = connect_ib(host, port, client_id)
    try:
        chain = fetch_chain(ib, today)
        if not chain:
            raise RuntimeError("No VX contracts listed — nothing to do.")
        print(f"Chain: {len(chain)} monthly VX contracts, "
              f"front={chain[0]['label']} dtm={chain[0]['dtm']}")

        spot = fetch_spot_vix(ib)
        print(f"Spot VIX (delayed): {spot:.2f}")
        quotes = fetch_quotes(ib, chain)
        current = fetch_positions(ib, account)
        print(f"Current paper positions: "
              f"{ {c['label']: current.get(c['conId'], 0) for c in chain if current.get(c['conId'], 0)} }")

        # --- fit (A, B) on today's cross-section (t=1), like the backtest ---
        rows = [{"spot": spot, "settle": spot, "dtm": 0.0}]
        usable = []
        for r in chain:
            q = quotes[r["conId"]]
            if q["observed"] is None or q["observed"] <= 0:
                print(f"  SKIP {r['label']}: no usable quote")
                continue
            rows.append({"spot": spot, "settle": q["observed"],
                         "dtm": float(r["dtm"])})
            usable.append(r)
        import pandas as pd
        fit = fit_params([pd.DataFrame(rows)], t=1)
        if fit is None:
            raise RuntimeError("Model fit failed on today's cross-section.")
        A, B = fit
        print(f"Fit: A={A:.6f} B={B:.4f}")

        # --- signals with b5f1 dead band ---
        flatten_day = chain[0]["dtm"] <= 1
        if flatten_day:
            print("FLATTEN DAY: front month expires within 1 day — "
                  "targeting flat everywhere.")
        target = {}
        sig_rows = []
        for r in chain:
            conId = r["conId"]
            q = quotes[conId]
            cur_pos = current.get(conId, 0)
            skipped, tgt = None, cur_pos
            if r not in usable:
                skipped = "no quote"
                tgt = cur_pos
            elif flatten_day or r["dtm"] <= 1:
                skipped = "flatten/expiry" if flatten_day else "expiring"
                tgt = 0
            elif q["volume"] is not None and q["volume"] < VOL_MIN:
                skipped = f"volume {q['volume']} < {VOL_MIN}"
                tgt = cur_pos  # hold (thesis: skip low-volume contracts)
            else:
                fc = float(forecast_next_day(spot, A, B, float(r["dtm"])))
                band = deadband_points(float(r["dtm"]),
                                       base_ticks=DEADBAND_BASE_TICKS,
                                       front_ticks=DEADBAND_FRONT_TICKS)
                dev = fc - q["observed"]
                in_band = abs(dev) <= band
                tgt = decide_target_position(dev, band, cur_pos)
                sig_rows.append((r, q, fc, band, dev, in_band, cur_pos, tgt,
                                 skipped))
                print(f"  {r['label']} dtm={r['dtm']:3d} obs={q['observed']:.2f} "
                      f"fc={fc:.2f} dev={dev:+.2f} band={band:.2f} "
                      f"{'HOLD ' if in_band else ''}"
                      f"cur={cur_pos:+d} -> tgt={tgt:+d}")
            if r in usable and skipped:
                # still log skipped rows for the audit trail
                sig_rows.append((r, q, None, None, None, None, cur_pos, tgt,
                                 skipped))
            target[conId] = tgt

        orders = reconcile_positions(current, target)
        print(f"Orders to reconcile ({len(orders)}):")
        for conId, action, qty in orders:
            r = next(x for x in chain if x["conId"] == conId)
            q = quotes[conId]
            px = limit_price_for(action, q["bid"], q["ask"], q["observed"])
            print(f"  {action} {qty} x {r['label']} @ limit {px:.2f} "
                  f"(bid {q['bid']} / ask {q['ask']})")

        # --- ledger: run + signals ---
        ts = datetime.now().isoformat(timespec="seconds")
        cur.execute(
            "INSERT INTO runs (ts, mode, account, spot, a, b, n_contracts, "
            "flatten_day, notes) VALUES (?,?,?,?,?,?,?,?,?)",
            (ts, mode, account, spot, A, B, len(usable),
             int(flatten_day),
             f"b5f1 deadband base={DEADBAND_BASE_TICKS} "
             f"front={DEADBAND_FRONT_TICKS}; delayed data"))
        run_id = cur.lastrowid
        for (r, q, fc, band, dev, in_band, cur_pos, tgt,
             skipped) in sig_rows:
            cur.execute(
                "INSERT INTO contract_signals (run_id, label, expiry, dtm, bid, "
                "ask, last, observed, volume, forecast, band_points, dev_points, "
                "in_band, current_pos, target_pos, skipped) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, r["label"], r["expiry"].isoformat(), r["dtm"],
                 q["bid"], q["ask"], q["last"], q["observed"], q["volume"],
                 fc, band, dev,
                 None if in_band is None else int(in_band),
                 cur_pos, tgt, skipped))
        conn.commit()

        # --- orders ---
        ibmod = _ib()
        fills_log = []

        def on_fill(trade, fill):
            c = trade.contract
            lbl = next((x["label"] for x in chain
                        if x["conId"] == c.conId), str(c.conId))
            fills_log.append((run_id, trade.order.orderId, lbl,
                              fill.execution.shares, fill.execution.price,
                              datetime.now().isoformat(timespec="seconds")))

        for conId, action, qty in orders:
            r = next(x for x in chain if x["conId"] == conId)
            q = quotes[conId]
            px = limit_price_for(action, q["bid"], q["ask"], q["observed"])
            ots = datetime.now().isoformat(timespec="seconds")
            if not submit:
                print(f"  DRY-RUN: would {action} {qty} x {r['label']} "
                      f"@ {px:.2f}")
                cur.execute(
                    "INSERT INTO orders (run_id, label, action, qty, "
                    "limit_price, dry_run, status, ts) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (run_id, r["label"], action, qty, px, 1,
                     "dry-run", ots))
                continue
            order = ibmod.LimitOrder(action, qty, px)
            trade = ib.placeOrder(r["contract"], order)
            trade.fillEvent += on_fill
            # wait briefly for the fill (paper fills are simulated)
            for _ in range(60):
                if trade.isDone():
                    break
                ib.sleep(1)
            status = trade.orderStatus.status
            cur.execute(
                "INSERT INTO orders (run_id, label, action, qty, limit_price, "
                "dry_run, ib_order_id, status, ts) VALUES (?,?,?,?,?,?,?,?,?)",
                (run_id, r["label"], action, qty, px, 0,
                 trade.order.orderId, status, ots))
            print(f"  SUBMITTED {action} {qty} x {r['label']} @ {px:.2f} "
                  f"-> orderId {trade.order.orderId} status {status}")
        for f in fills_log:
            cur.execute(
                "INSERT INTO fills (run_id, ib_order_id, label, qty, price, ts)"
                " VALUES (?,?,?,?,?,?)", f)
        conn.commit()
        print(f"Ledger: {db_path} (run_id={run_id}, {len(fills_log)} fills)")
        return 0
    finally:
        ib.disconnect()
        conn.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Paper-trade the VIX term-structure dead-band (b5f1) strategy.")
    ap.add_argument("--submit", action="store_true",
                    help="Actually place paper orders. Default: dry-run only.")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=4002,
                    help="Gateway paper=4002, TWS paper=7497")
    ap.add_argument("--client-id", type=int, default=7)
    ap.add_argument("--db", default=str(BASE / "paper_ledger.db"),
                    help="SQLite ledger path")
    args = ap.parse_args(argv)
    if args.submit:
        print("*** SUBMIT MODE: orders will be placed on the PAPER account ***")
    else:
        print("*** DRY-RUN MODE: no orders will be placed "
              "(use --submit to transmit) ***")
    return run(args.host, args.port, args.client_id, args.submit,
               Path(args.db))


if __name__ == "__main__":
    sys.exit(main())
