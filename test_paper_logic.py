"""Unit tests for the paper runner's pure logic (no IB connection needed).

Covers the b5f1 dead-band semantics against src/backtest.py (the exact
backtest rules), the reconcile/cap logic, and limit-price construction.

Run:  python3 test_paper_logic.py
"""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "src"))

from backtest import deadband_points, TICK  # noqa: E402
from run_paper import (  # noqa: E402
    decide_target_position,
    reconcile_positions,
    limit_price_for,
    month_code_label,
    is_standard_monthly_vx,
    MAX_CONTRACTS_PER_ORDER,
    MAX_TOTAL_OPEN,
)
from datetime import date  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


# --- b5f1 dead-band tiers (base 5 ticks, front 1 tick; tick = 0.05) ---
check("front dtm=30 -> 1 tick = 0.05",
      abs(deadband_points(30, base_ticks=5.0, front_ticks=1.0) - 0.05) < 1e-12)
check("mid dtm=90 -> 5*2 ticks = 0.50",
      abs(deadband_points(90, base_ticks=5.0, front_ticks=1.0) - 0.50) < 1e-12)
check("back dtm=150 -> 5*3 ticks = 0.75",
      abs(deadband_points(150, base_ticks=5.0, front_ticks=1.0) - 0.75) < 1e-12)
check("far dtm=300 -> 5*5 ticks = 1.25",
      abs(deadband_points(300, base_ticks=5.0, front_ticks=1.0) - 1.25) < 1e-12)
check("boundary dtm=60 -> mid tier (0.50)",
      abs(deadband_points(60, base_ticks=5.0, front_ticks=1.0) - 0.50) < 1e-12)
check("TICK is 0.05", TICK == 0.05)

# --- dead-band decision: hold inside, act outside (matches backtest) ---
for cur in (-1, 0, 1):
    check(f"inside band holds (cur={cur:+d})",
          decide_target_position(0.20, 0.50, cur) == cur)
    check(f"exactly on band edge holds (cur={cur:+d})",
          decide_target_position(0.50, 0.50, cur) == cur,
          "backtest rule is |dev| <= band -> hold")
check("above band -> long", decide_target_position(0.51, 0.50, -1) == 1)
check("below band -> short", decide_target_position(-0.51, 0.50, 1) == -1)
check("above band from flat -> long", decide_target_position(2.0, 0.05, 0) == 1)

# --- reconcile: diffs become BUY/SELL, no-ops dropped ---
cur_pos = {"A": 1, "B": -1, "C": 0}
tgt_pos = {"A": 1, "B": 1, "C": -1, "D": 1}
orders = reconcile_positions(cur_pos, tgt_pos)
o = {k: (a, q) for k, a, q in orders}
check("reversal B -1->+1 is BUY 2", o.get("B") == ("BUY", 2), str(o))
check("new short C is SELL 1", o.get("C") == ("SELL", 1), str(o))
check("new long D is BUY 1", o.get("D") == ("BUY", 1), str(o))
check("unchanged A emits no order", "A" not in o, str(o))
check("flatten emits SELL",
      reconcile_positions({"A": 1}, {"A": 0}) == [("A", "SELL", 1)])

# --- caps ---
try:
    reconcile_positions({}, {"A": 11}, max_per_order=10)
    check("per-order cap enforced", False, "no ValueError raised")
except ValueError:
    check("per-order cap enforced", True)
try:
    reconcile_positions({}, {f"C{i}": 1 for i in range(21)}, max_total_open=20)
    check("total-open cap enforced", False, "no ValueError raised")
except ValueError:
    check("total-open cap enforced", True)
check("caps are 10 / 20",
      MAX_CONTRACTS_PER_ORDER == 10 and MAX_TOTAL_OPEN == 20)

# --- limit prices: join the touch, never market ---
check("BUY lifts the ask", limit_price_for("BUY", 16.90, 17.10, 17.00) == 17.10)
check("SELL hits the bid", limit_price_for("SELL", 16.90, 17.10, 17.00) == 16.90)
check("BUY w/o ask falls back to observed",
      limit_price_for("BUY", 16.90, None, 17.03) == 17.05)  # 17.03 -> tick
check("SELL w/o bid falls back to observed",
      limit_price_for("SELL", None, 17.10, 16.97) == 16.95)
check("tick rounding", limit_price_for("BUY", None, None, 17.031) == 17.05)

# --- labels / monthly detection ---
check("label Oct 2026 = VXV26", month_code_label(2026, 10) == "VXV26")
check("label Jan 2027 = VXF27", month_code_label(2027, 1) == "VXF27")
check("Sep26 monthly expiry 2026-09-16",
      is_standard_monthly_vx("202609", date(2026, 9, 16)))
check("Oct26 monthly expiry 2026-10-21",
      is_standard_monthly_vx("202610", date(2026, 10, 21)))
check("weekly expiry rejected",
      not is_standard_monthly_vx("202610", date(2026, 10, 14)))

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURES: {FAILURES}")
    sys.exit(1)
print("All paper-logic tests passed.")
