# Data provenance

Downloaded: 2026-09-10 (UTC). Downloader: src/fetch.py

## Sources
- Monthly VX futures 2006-2012: CBOE settlement archive
  `https://cdn.cboe.com/resources/futures/archive/volume-and-price/CFE_<M><YY>_VX.csv`
- Monthly VX futures 2013-present: CBOE historical data index API
  `https://www-api.cboe.com/us/futures/market_statistics/historical_data/product/list/VX/`
  per-contract files: `https://cdn.cboe.com/<path>` where `<path>` =
  `data/us/futures/market_statistics/historical_data/VX/VX_<expiry-date>.csv`
- Spot VIX daily: Yahoo Finance v8 chart API, symbol `^VIX`
  (Cboe indices feed; close prices, splits/dividends n/a for an index)

## Notes
- CBOE `cdn.cboe.com` bulk yearly CSV URL pattern was tried and returned
  HTTP 403 AccessDenied; per-contract URLs above verified working Sep 2026.
- **Access note (Sep 10, 2026): the cdn.cboe.com archive path above has since
  been observed returning HTTP 403 AccessDenied on direct fetches, even with a
  browser User-Agent — it appears bot-walled as of Sep 2026. Do NOT burn time
  retrying it with curl. The files already on disk (84/84 archive + 173/173
  current) were downloaded successfully on 2026-09-10 and are intact; future
  re-runs that need fresh downloads should prefer, in order: (1) vixcentral.com
  historical CSVs, (2) yfinance per-contract symbols, (3) Nasdaq Data Link
  CHRIS VX series, or (4) fetching via browser tools.**
- Only STANDARD MONTHLY VX contracts are used. Weekly VX contracts (launched
  ~2015, often zero volume) are excluded at the source: the CBOE index was
  filtered to `futures_root=="VX"` and `duration_type=="M"`. The 2006-2012
  archive predates weeklies and is monthly-only.
- `Settle` column used as the daily futures price; `Total Volume` for the
  volume>=10 liquidity filter.
- CBOE changed file layouts over time: 2013-era current files carry the
  settlement in `Close` (`Settle`=0); recent files carry it in `Settle`
  (`Close`=last trade). Panel rule: use `Settle` when > 0, else `Close`
  when > 0. See data/processed/BUILD_NOTES.md.
- Pre-2007-03-26 prices: CBOE rescaled VX futures x1/10 on 2007-03-26
  (multiplier $100 -> $1000). Archive values checked during panel build;
  see data/processed/BUILD_NOTES.md.

## Files
- archive: 84 contracts, data/raw/archive/CFE_<M><YY>_VX.csv
- current: 173 contracts, data/raw/current/VX_<expiry>.csv
- data/raw/vx_index.json (CBOE index API snapshot)
- data/raw/spot_vix.csv
