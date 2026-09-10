"""Daily runner: fetch -> fit -> signals -> paper ledger -> (optional) IBKR paper.

Usage:
    python -m src.daily_run --date 2026-09-10            # paper ledger only
    python -m src.daily_run --date 2026-09-10 --to-ibkr   # also route to IBKR paper
    python -m src.daily_run --date 2026-09-10 --to-ibkr --live-fire
        # actually transmit orders (default is dry-run print only)

Pipeline stages 1-2 come from phase 1 (src/fetch.py, src/model.py). Until those
land, this script fails fast with a clear message instead of guessing.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

LEDGER_ROOT = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "data", "paper")


def load_day_panel(date: str):
    """Return (spot, contracts) for `date` from the phase-1 data pipeline."""
    try:
        from src import fetch as fetch_mod  # noqa
    except ImportError as e:
        raise SystemExit(
            "Phase 1 data pipeline (src/fetch.py) is not built yet. "
            "Run the backtest phase first.") from e
    return fetch_mod.day_panel(date)
    # day_panel(date) -> (spot: float, contracts: list[dict]) with keys:
    #   contract, settle, volume, days_to_maturity, expires_tomorrow


def fit_params(spot: float, contracts: list[dict]):
    try:
        from src import model as model_mod  # noqa
    except ImportError as e:
        raise SystemExit(
            "Phase 1 model (src/model.py) is not built yet.") from e
    return model_mod.fit_daily_params(spot, contracts)  # -> (At, Bt)


def main() -> None:
    ap = argparse.ArgumentParser(description="Daily VIX futures paper-trading run")
    ap.add_argument("--date", required=True, help="Trading date YYYY-MM-DD")
    ap.add_argument("--to-ibkr", action="store_true",
                    help="Also route signals to IBKR paper account")
    ap.add_argument("--live-fire", action="store_true",
                    help="Actually transmit IBKR orders (default: dry-run)")
    ap.add_argument("--port", type=int, default=7497, help="TWS/Gateway API port")
    args = ap.parse_args()

    from src.signals import generate_signals
    from src.ledger import PaperLedger

    date = args.date
    spot, contracts = load_day_panel(date)
    At, Bt = fit_params(spot, contracts)
    print(f"{date}: spot={spot:.2f} At={At:.4f} Bt={Bt:.2f}")

    signals = generate_signals(date, spot, contracts, At, Bt)
    for s in signals:
        if s.direction != 0:
            print(f"  {'LONG ' if s.direction > 0 else 'SHORT'} {s.contract} "
                  f"mkt={s.market_price:.2f} fcst={s.forecast_price:.2f} "
                  f"edge={s.edge:.2f}")

    ledger = PaperLedger(LEDGER_ROOT)
    fees = ledger.apply_signals(date, signals)
    nav = ledger.mark_to_market(date, {c["contract"]: c["settle"] for c in contracts})
    print(f"  booked {sum(1 for s in signals if s.direction)} positions, "
          f"fees=${fees:.2f}, equity=${nav['equity']:,.2f}")

    if args.to_ibkr:
        from src.ibkr_paper import connect, place_signal_orders
        ib = connect(port=args.port)
        try:
            place_signal_orders(ib, signals, dry_run=not args.live_fire)
        finally:
            ib.disconnect()


if __name__ == "__main__":
    main()
