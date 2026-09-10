# Panel build notes

Built 2026-09-10 by src/panel.py from data/raw/ (see PROVENANCE.md).

- Rows: 44879 | contracts: 257 | dates: 2005-05-23 -> 2026-09-08
- Price: CBOE daily settlement. Archive files (2006-2012): `Settle` column.
  Current files changed layout over time: 2013-era files carry the settlement in
  `Close` (`Settle`=0); recent files carry it in `Settle` (`Close`=last trade).
  Unified rule: use `Settle` when > 0, else `Close` when > 0. Rows with no
  positive price dropped (pre-listing placeholders).
- 2007-03-26 rescale: settles with Trade Date < 2007-03-26 divided by 10
  (CFE changed multiplier $100->$1000 that day; archive keeps original prints).
  Post-rescale median settle/spot = 1.19.
- Archive expiries derived as (3rd Friday of following month) - 30 days;
  verified against all 12 monthly 2006 final-settlement dates (Brenner et al. 2009, Table 2)
  and against CBOE's own expire_date list for 2013+ contracts.
- Spot VIX = Yahoo Finance ^VIX daily close (Cboe indices feed). Rows without spot: 308.
- `dtm` = calendar days from date to expiry (thesis uses days in the exponent).
