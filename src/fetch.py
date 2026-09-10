"""Download raw VIX futures + spot VIX data from CBOE and Yahoo Finance.

Sources (verified Sep 2026):
- Monthly VX futures, 2006-2012: CBOE settlement archive,
  https://cdn.cboe.com/resources/futures/archive/volume-and-price/CFE_<M><YY>_VX.csv
  (M = F,G,H,J,K,M,N,Q,U,V,X,Z futures month code)
- Monthly VX futures, 2013-present: CBOE historical-data index API
  https://www-api.cboe.com/us/futures/market_statistics/historical_data/product/list/VX/
  -> per-contract CSVs at
  https://cdn.cboe.com/data/us/futures/market_statistics/historical_data/VX/VX_<expiry-YYYY-MM-DD>.csv
- Spot VIX daily closes: Yahoo Finance chart API for ^VIX.

Writes: data/raw/archive/*.csv, data/raw/current/*.csv,
        data/raw/vx_index.json, data/raw/spot_vix.csv,
        data/raw/PROVENANCE.md
"""
import csv
import datetime as dt
import json
import time
import urllib.request
from pathlib import Path

RAW = Path(__file__).resolve().parent.parent / "data" / "raw"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "Chrome/126.0 Safari/537.36"}

MONTH_CODES = list("FGHJKMNQUVXZ")
ARCHIVE_BASE = "https://cdn.cboe.com/resources/futures/archive/volume-and-price/"
INDEX_URL = ("https://www-api.cboe.com/us/futures/market_statistics/"
             "historical_data/product/list/VX/")
CURRENT_BASE = "https://cdn.cboe.com/"


def _get(url, timeout=40):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


def download(url, dest: Path, sleep=0.4):
    """Download url -> dest. Returns (ok, status, size). Skips if dest exists and non-empty."""
    if dest.exists() and dest.stat().st_size > 0:
        return True, "cached", dest.stat().st_size
    try:
        status, data = _get(url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        time.sleep(sleep)
        return True, status, len(data)
    except Exception as e:  # noqa: BLE001 - record and continue
        print(f"  FAIL {url}: {e}")
        time.sleep(sleep)
        return False, str(e), 0


def fetch_archive_contracts(years=range(6, 13)):
    """2006-2012 monthly contracts from the CBOE settlement archive."""
    log = []
    for yy in years:
        for m in MONTH_CODES:
            fname = f"CFE_{m}{yy:02d}_VX.csv"
            ok, status, size = download(ARCHIVE_BASE + fname, RAW / "archive" / fname)
            log.append((fname, ok, status, size))
    n_ok = sum(1 for _, ok, _, _ in log if ok)
    print(f"archive contracts: {n_ok}/{len(log)} downloaded")
    return log


def fetch_current_index():
    status, data = _get(INDEX_URL)
    idx = json.loads(data.decode("utf-8"))
    (RAW / "vx_index.json").write_text(json.dumps(idx, indent=1))
    monthly = []
    for year_key in sorted(idx):
        for item in idx[year_key]:
            if item.get("futures_root") == "VX" and item.get("duration_type") == "M":
                monthly.append(item)
    print(f"index: {len(monthly)} monthly VX contracts listed (2013->present)")
    return monthly


def fetch_current_contracts(monthly):
    log = []
    for item in monthly:
        path = item["path"]  # data/us/futures/.../VX/VX_<expiry>.csv
        fname = path.rsplit("/", 1)[-1]
        ok, status, size = download(CURRENT_BASE + path, RAW / "current" / fname)
        log.append((fname, ok, status, size, item.get("expire_date")))
    n_ok = sum(1 for _, ok, _, _, _ in log if ok)
    print(f"current contracts: {n_ok}/{len(log)} downloaded")
    return log


def fetch_spot_vix():
    """Daily ^VIX closes via Yahoo Finance chart API (no yfinance dependency)."""
    import datetime as dtm
    p1 = int(dtm.datetime(2006, 1, 1, tzinfo=dtm.timezone.utc).timestamp())
    p2 = int(dtm.datetime.now(dtm.timezone.utc).timestamp())
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX"
           f"?interval=1d&period1={p1}&period2={p2}")
    status, data = _get(url)
    payload = json.loads(data.decode("utf-8"))["chart"]["result"][0]
    ts = payload["timestamp"]
    q = payload["indicators"]["quote"][0]
    rows = [("date", "open", "high", "low", "close", "volume")]
    for i, t in enumerate(ts):
        d = dtm.datetime.fromtimestamp(t, tz=dtm.timezone.utc).date().isoformat()
        rows.append((d, q["open"][i], q["high"][i], q["low"][i], q["close"][i], q["volume"][i]))
    dest = RAW / "spot_vix.csv"
    with open(dest, "w", newline="") as f:
        csv.writer(f).writerows(rows)
    print(f"spot ^VIX: {len(rows)-1} rows -> {dest} (status {status})")
    return dest


def write_provenance(archive_log, current_log):
    today = dt.date.today().isoformat()
    lines = [
        "# Data provenance",
        "",
        f"Downloaded: {today} (UTC). Downloader: src/fetch.py",
        "",
        "## Sources",
        "- Monthly VX futures 2006-2012: CBOE settlement archive",
        f"  `{ARCHIVE_BASE}CFE_<M><YY>_VX.csv`",
        "- Monthly VX futures 2013-present: CBOE historical data index API",
        f"  `{INDEX_URL}`",
        "  per-contract files: `https://cdn.cboe.com/<path>` where `<path>` =",
        "  `data/us/futures/market_statistics/historical_data/VX/VX_<expiry-date>.csv`",
        "- Spot VIX daily: Yahoo Finance v8 chart API, symbol `^VIX`",
        "  (Cboe indices feed; close prices, splits/dividends n/a for an index)",
        "",
        "## Notes",
        "- CBOE `cdn.cboe.com` bulk yearly CSV URL pattern was tried and returned",
        "  HTTP 403 AccessDenied; per-contract URLs above verified working Sep 2026.",
        "- `Settle` column used as the daily futures price; `Total Volume` for the",
        "  volume>=10 liquidity filter.",
        "- Pre-2007-03-26 prices: CBOE rescaled VX futures x1/10 on 2007-03-26",
        "  (multiplier $100 -> $1000). Archive values checked during panel build;",
        "  see data/processed/BUILD_NOTES.md.",
        "",
        "## Files",
        f"- archive: {sum(1 for l in archive_log if l[1])} contracts, "
        "data/raw/archive/CFE_<M><YY>_VX.csv",
        f"- current: {sum(1 for l in current_log if l[1])} contracts, "
        "data/raw/current/VX_<expiry>.csv",
        "- data/raw/vx_index.json (CBOE index API snapshot)",
        "- data/raw/spot_vix.csv",
    ]
    (RAW / "PROVENANCE.md").write_text("\n".join(lines) + "\n")
    print("wrote data/raw/PROVENANCE.md")


def main():
    RAW.mkdir(parents=True, exist_ok=True)
    archive_log = fetch_archive_contracts()
    monthly = fetch_current_index()
    current_log = fetch_current_contracts(monthly)
    fetch_spot_vix()
    write_provenance(archive_log, current_log)


if __name__ == "__main__":
    main()
