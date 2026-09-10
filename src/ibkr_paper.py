"""Route daily signals to an Interactive Brokers *paper* trading account.

Safety-first design:
  * DRY_RUN=True by default — prints orders instead of placing them.
  * Refuses to run unless connected to a paper-trading port
    (TWS paper: 7497, Gateway paper: 4002). Live ports are rejected.
  * One contract per signal, matching the paper ledger.

Prereqs (see docs/ibkr_paper_setup.md):
  pip install ib_insync
  TWS or IB Gateway running with API enabled, logged into the PAPER account.
  VIX futures: symbol VX on CFE (front months only; we never hold into expiry
  week since the strategy flattens the day before front-month expiry).

This module is a scaffold: fill routing is straightforward, but it has NOT been
tested against a live paper account yet. Run with dry_run=True first.
"""
from __future__ import annotations

PAPER_PORTS = {7497, 4002}   # TWS paper, Gateway paper
LIVE_PORTS = {7496, 4001}    # rejected outright


def _require_ib_insync():
    try:
        from ib_insync import IB, Future, MarketOrder
    except ImportError as e:
        raise RuntimeError(
            "ib_insync is not installed. Run: pip install ib_insync") from e
    from ib_insync import IB, Future, MarketOrder
    return IB, Future, MarketOrder


def connect(port: int = 7497, client_id: int = 7):
    """Connect to TWS/Gateway. Raises if the port is not a paper-trading port."""
    if port in LIVE_PORTS:
        raise RuntimeError(
            f"Port {port} is a LIVE trading port. This module only talks to paper.")
    if port not in PAPER_PORTS:
        raise RuntimeError(
            f"Port {port} is not a known paper-trading port {sorted(PAPER_PORTS)}.")
    IB, _, _ = _require_ib_insync()
    ib = IB()
    ib.connect("127.0.0.1", port, clientId=client_id)
    return ib


def vx_contract(symbol: str, exchange: str = "CFE"):
    """Build a CFE VIX futures contract, e.g. symbol='202610' for Oct 2026.

    Our signal contracts look like 'VXV26' (Oct 2026). This maps the month
    code + 2-digit year to IB's YYYYMM lastTradeDateOrContractMonth format.
    """
    _, Future, _ = _require_ib_insync()
    months = "FGHJKMNQUVXZ"
    code = symbol[2].upper()
    year = 2000 + int(symbol[3:5])
    month = months.index(code) + 1
    return Future("VX", f"{year}{month:02d}", exchange)


def place_signal_orders(ib, signals, dry_run: bool = True) -> list[dict]:
    """Turn signals into market orders. direction 0 = flatten (close position).

    Returns a list of order summaries. In dry_run mode nothing is transmitted.
    """
    _, _, MarketOrder = _require_ib_insync()
    results = []
    for s in getattr(signals, "__iter__", []):
        if s.direction == 0:
            # flatten: sell/buy back whatever we hold — resolve size from positions
            action, qty = "CLOSE", None
        else:
            action = "BUY" if s.direction > 0 else "SELL"
            qty = 1
        summary = {"contract": s.contract, "action": action, "qty": qty,
                   "dry_run": dry_run}
        if not dry_run:
            contract = vx_contract(s.contract)
            order = MarketOrder(action, qty or 0)
            trade = ib.placeOrder(contract, order)
            summary["order_id"] = trade.order.orderId
        results.append(summary)
        print(("DRY-RUN " if dry_run else "") +
              f"{summary['action']} {qty or '?'} x {s.contract} @ mkt")
    return results
