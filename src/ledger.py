"""Paper-trade ledger: hypothetical fills at the close, daily NAV, auditable CSVs.

Design matches the thesis bookkeeping:
  * 1 contract per signal, positions in {-1, 0, +1}
  * fills assumed at the closing settle
  * fee = $2 per unit change in position  (thesis: PVbar_t = PV_t - 2*|P_t - P_{t-1}|)
  * contract multiplier $1000

Files written under <root>/:
  trades.csv  — date, contract, old_pos, new_pos, price, fee
  nav.csv     — date, equity, n_positions, gross_exposure, net_exposure

Fully reproducible: given the same signal stream, the ledger is deterministic.
This is the track record shown to newsletter subscribers (hypothetical).
"""
from __future__ import annotations

import csv
import os

from .signals import CONTRACT_MULTIPLIER, FEE_PER_CONTRACT_SIDE


class PaperLedger:
    def __init__(self, root: str, starting_equity: float = 100_000.0):
        self.root = root
        os.makedirs(root, exist_ok=True)
        self.trades_path = os.path.join(root, "trades.csv")
        self.nav_path = os.path.join(root, "nav.csv")
        self.equity = starting_equity
        self.positions: dict[str, tuple[int, float]] = {}  # contract -> (direction, entry_price)
        if not os.path.exists(self.trades_path):
            with open(self.trades_path, "w", newline="") as f:
                csv.writer(f).writerow(
                    ["date", "contract", "old_pos", "new_pos", "price", "fee"])
        if not os.path.exists(self.nav_path):
            with open(self.nav_path, "w", newline="") as f:
                csv.writer(f).writerow(
                    ["date", "equity", "n_positions", "gross_exposure", "net_exposure"])

    def apply_signals(self, date: str, signals) -> float:
        """Book signal trades at closing prices. Returns total fees paid."""
        total_fee = 0.0
        with open(self.trades_path, "a", newline="") as f:
            w = csv.writer(f)
            for s in signals:
                old_pos, _ = self.positions.get(s.contract, (0, 0.0))
                new_pos = s.direction
                if new_pos != old_pos:
                    fee = FEE_PER_CONTRACT_SIDE * abs(new_pos - old_pos)
                    total_fee += fee
                    w.writerow([date, s.contract, old_pos, new_pos,
                                f"{s.market_price:.2f}", f"{fee:.2f}"])
                    if new_pos == 0:
                        self.positions.pop(s.contract, None)
                    else:
                        self.positions[s.contract] = (new_pos, s.market_price)
        self.equity -= total_fee
        return total_fee

    def mark_to_market(self, date: str, settle_map: dict[str, float]) -> dict:
        """settle_map: contract -> today's closing settle. Realizes daily P&L
        on open positions and appends a NAV row. Returns the NAV row dict."""
        gross = 0.0
        net = 0.0
        for contract, (direction, entry) in list(self.positions.items()):
            if contract not in settle_map:
                continue  # contract gone (expired) — treated as closed at last price
            settle = settle_map[contract]
            pnl = direction * (settle - entry) * CONTRACT_MULTIPLIER
            self.equity += pnl
            # reset cost basis daily (futures settle daily)
            self.positions[contract] = (direction, settle)
            mv = direction * settle * CONTRACT_MULTIPLIER
            gross += abs(mv)
            net += mv
        row = {
            "date": date,
            "equity": round(self.equity, 2),
            "n_positions": len(self.positions),
            "gross_exposure": round(gross / self.equity, 4) if self.equity else 0.0,
            "net_exposure": round(net / self.equity, 4) if self.equity else 0.0,
        }
        with open(self.nav_path, "a", newline="") as f:
            w = csv.writer(f)
            w.writerow([row["date"], f"{row['equity']:.2f}", row["n_positions"],
                        f"{row['gross_exposure']:.4f}", f"{row['net_exposure']:.4f}"])
        return row
