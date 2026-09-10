"""Daily signal generation — VIX futures dynamic strategy (thesis t=1, f=1).

Each day at the close:
  1. fit (At, Bt) to today's spot VIX + futures settles  (phase-1 model.fit_daily_params)
  2. forecast next-day futures prices assuming parameters persist
  3. long where forecast > market, short where forecast < market
  4. skip contracts with volume < MIN_VOLUME
  5. on the day before front-month expiry: flatten everything

Position sizing matches the thesis: one contract per signal, P in {-1, 0, +1}.
"""
from __future__ import annotations

from dataclasses import dataclass

CONTRACT_MULTIPLIER = 1000
FEE_PER_CONTRACT_SIDE = 2.0
MIN_VOLUME = 10


@dataclass
class Signal:
    date: str          # YYYY-MM-DD
    contract: str      # e.g. "VXQ26"
    direction: int     # +1 long, -1 short, 0 flatten/no-trade
    market_price: float
    forecast_price: float
    edge: float        # |forecast - market|, always >= 0
    volume: int


def forecast_price(spot: float, At: float, Bt: float, days_to_maturity: float) -> float:
    """Closed-form thesis pricing: F = Vt * At^dt + Bt * (1 - At^dt), dt in years."""
    dt = days_to_maturity / 365.0
    return spot * (At ** dt) + Bt * (1.0 - At ** dt)


def generate_signals(date: str, spot: float, contracts: list[dict],
                     At: float, Bt: float, min_volume: int = MIN_VOLUME) -> list[Signal]:
    """contracts: dicts with keys contract, settle, volume, days_to_maturity,
    expires_tomorrow (bool). Returns one Signal per contract."""
    signals: list[Signal] = []
    flatten_day = any(c.get("expires_tomorrow", False) for c in contracts)
    for c in contracts:
        if flatten_day or c["volume"] < min_volume:
            # flatten_day -> close out; low volume -> no new position (existing
            # positions in that contract are left to the flatten logic)
            direction = 0
            f = c["settle"]
        else:
            f = forecast_price(spot, At, Bt, c["days_to_maturity"])
            direction = 1 if f > c["settle"] else -1
        signals.append(Signal(
            date=date,
            contract=c["contract"],
            direction=direction,
            market_price=c["settle"],
            forecast_price=f,
            edge=abs(f - c["settle"]),
            volume=c["volume"],
        ))
    return signals
