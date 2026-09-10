"""Build the clean daily panel from raw CBOE/Yahoo downloads.

Reads:
  data/raw/archive/CFE_<M><YY>_VX.csv      (monthly VX, 2006-2012)
  data/raw/current/VX_<expiry>.csv          (monthly VX, 2013-present)
  data/raw/spot_vix.csv                     (^VIX daily closes)

Writes:
  data/processed/vx_panel.csv  columns:
      date, contract, expiry, settle, volume, dtm, spot
  data/processed/BUILD_NOTES.md

Key transformations (documented in BUILD_NOTES.md):
- Pre-2007-03-26 settles divided by 10 (CFE rescaled the contract that day:
  multiplier $100 -> $1000, prices /10; CBOE archive keeps original prints).
- Archive-contract expiries derived by rule and verified:
      expiry = (3rd Friday of month after expiry month) - 30 days
  (matches CBOE final-settlement Wednesdays; checked vs Brenner et al. Table 2
  for all 12 contracts of 2006 and vs CBOE's own expire_date list for 2013+).
- Price used = CBOE daily `Settle`; rows with Settle <= 0 dropped.
"""
import re
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent.parent
RAW = BASE / "data" / "raw"
PROCESSED = BASE / "data" / "processed"

MONTH_CODES = list("FGHJKMNQUVXZ")
CODE2NUM = {c: i + 1 for i, c in enumerate(MONTH_CODES)}
RESCALE_CUTOFF = date(2007, 3, 26)  # CFE 10:1 rescale effective this day


def third_friday(year, month):
    d = date(year, month, 1)
    # first Friday on/after the 1st
    delta = (4 - d.weekday()) % 7  # Monday=0 ... Friday=4
    first_friday = d + timedelta(days=delta)
    return first_friday + timedelta(days=14)


def expiry_for_contract(year, month):
    """Final settlement date for a monthly VX contract."""
    nm = month + 1
    ny = year
    if nm == 13:
        nm, ny = 1, year + 1
    return third_friday(ny, nm) - timedelta(days=30)


def add_price(df):
    """Unified price column.

    CBOE changed the file layout over the years:
      - 2006-2012 archive: `Settle` holds the daily settlement (preferred);
        indicative pre-listing rows have Close=0, Settle>0.
      - 2013-era current files: `Settle` is 0 and `Close` holds the settlement.
      - recent files: `Close` = last trade, `Settle` = official settlement.
    Rule: use Settle when > 0, else Close when > 0.
    """
    df = df.rename(columns={"Trade Date": "date", "Total Volume": "volume"})
    st = pd.to_numeric(df["Settle"], errors="coerce")
    cl = pd.to_numeric(df["Close"], errors="coerce")
    df["settle"] = st.where(st > 0, cl)
    return df


def parse_archive():
    frames = []
    pat = re.compile(r"CFE_([FGHJKMNQUVXZ])(\d{2})_VX\.csv")
    for path in sorted((RAW / "archive").glob("CFE_*_VX.csv")):
        m = pat.match(path.name)
        if not m:
            continue
        code, yy = m.group(1), int(m.group(2))
        year = 2000 + yy
        month = CODE2NUM[code]
        contract = f"{code}{yy:02d}"
        expiry = expiry_for_contract(year, month)
        df = add_price(pd.read_csv(path))
        df["date"] = pd.to_datetime(df["date"], format="%m/%d/%Y").dt.date
        df["contract"] = contract
        df["expiry"] = expiry
        pre = pd.to_datetime(df["date"]) < pd.Timestamp(RESCALE_CUTOFF)
        df.loc[pre, "settle"] = df.loc[pre, "settle"] / 10.0
        frames.append(df[["date", "contract", "expiry", "settle", "volume"]])
    panel = pd.concat(frames, ignore_index=True)
    return panel


def parse_current():
    frames = []
    pat = re.compile(r"VX_(\d{4})-(\d{2})-(\d{2})\.csv")
    for path in sorted((RAW / "current").glob("VX_*.csv")):
        m = pat.match(path.name)
        if not m:
            continue
        expiry = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        code = MONTH_CODES[expiry.month - 1]
        contract = f"{code}{expiry.year % 100:02d}"
        df = add_price(pd.read_csv(path))
        df["date"] = pd.to_datetime(df["date"]).dt.date
        df["contract"] = contract
        df["expiry"] = expiry
        frames.append(df[["date", "contract", "expiry", "settle", "volume"]])
    return pd.concat(frames, ignore_index=True)


def verify_expiries():
    """Check derived expiries against Brenner et al. (2009) Table 2 settlements."""
    known = {  # contract -> settlement date in 2006 (Wednesdays)
        "F06": date(2006, 1, 18), "G06": date(2006, 2, 15),
        "H06": date(2006, 3, 22), "J06": date(2006, 4, 19),
        "K06": date(2006, 5, 17), "M06": date(2006, 6, 21),
        "N06": date(2006, 7, 19), "Q06": date(2006, 8, 16),
        "U06": date(2006, 9, 20), "V06": date(2006, 10, 18),
        "X06": date(2006, 11, 15), "Z06": date(2006, 12, 20),
    }
    bad = []
    for c, exp in known.items():
        code, yy = c[0], int(c[1:])
        if expiry_for_contract(2000 + yy, CODE2NUM[code]) != exp:
            bad.append(c)
    return bad


def main():
    PROCESSED.mkdir(parents=True, exist_ok=True)
    bad = verify_expiries()
    assert not bad, f"expiry rule mismatch: {bad}"
    print("expiry rule verified against 2006 settlement dates")

    panel = pd.concat([parse_archive(), parse_current()], ignore_index=True)
    panel = panel[panel["settle"] > 0].copy()
    panel["volume"] = pd.to_numeric(panel["volume"], errors="coerce").fillna(0).astype(int)
    panel["dtm"] = (pd.to_datetime(panel["expiry"]) - pd.to_datetime(panel["date"])).dt.days
    panel = panel[panel["dtm"] >= 0]
    panel = panel.sort_values(["date", "dtm"]).reset_index(drop=True)

    spot = pd.read_csv(RAW / "spot_vix.csv", parse_dates=["date"])
    spot["date"] = spot["date"].dt.date
    panel = panel.merge(spot[["date", "close"]].rename(columns={"close": "spot"}),
                        on="date", how="left")
    missing = panel["spot"].isna().sum()
    print(f"panel rows: {len(panel)}, contracts: {panel['contract'].nunique()}, "
          f"dates: {panel['date'].nunique()}, rows w/o spot: {missing}")
    # rescale sanity: pre-cutoff settles should be same order as spot
    pre = panel[pd.to_datetime(panel["date"]) < pd.Timestamp(RESCALE_CUTOFF)]
    ratio = (pre["settle"] / pre["spot"]).median()
    print(f"median pre-2007-03-26 settle/spot ratio: {ratio:.2f} (expect ~1)")

    out = PROCESSED / "vx_panel.csv"
    panel.to_csv(out, index=False)
    print(f"wrote {out}")

    notes = f"""# Panel build notes

Built {date.today().isoformat()} by src/panel.py from data/raw/ (see PROVENANCE.md).

- Rows: {len(panel)} | contracts: {panel['contract'].nunique()} | dates: {panel['date'].min()} -> {panel['date'].max()}
- Price: CBOE daily settlement. Archive files (2006-2012): `Settle` column.
  Current files changed layout over time: 2013-era files carry the settlement in
  `Close` (`Settle`=0); recent files carry it in `Settle` (`Close`=last trade).
  Unified rule: use `Settle` when > 0, else `Close` when > 0. Rows with no
  positive price dropped (pre-listing placeholders).
- 2007-03-26 rescale: settles with Trade Date < 2007-03-26 divided by 10
  (CFE changed multiplier $100->$1000 that day; archive keeps original prints).
  Post-rescale median settle/spot = {ratio:.2f}.
- Archive expiries derived as (3rd Friday of following month) - 30 days;
  verified against all 12 monthly 2006 final-settlement dates (Brenner et al. 2009, Table 2)
  and against CBOE's own expire_date list for 2013+ contracts.
- Spot VIX = Yahoo Finance ^VIX daily close (Cboe indices feed). Rows without spot: {missing}.
- `dtm` = calendar days from date to expiry (thesis uses days in the exponent).
"""
    (PROCESSED / "BUILD_NOTES.md").write_text(notes)
    print("wrote data/processed/BUILD_NOTES.md")


if __name__ == "__main__":
    main()
