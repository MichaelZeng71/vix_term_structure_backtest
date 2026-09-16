"""Chain ingestion: read a CSV chain, normalize to the canonical schema, validate.

Canonical schema (long form, one row per contract):
    date         YYYY-MM-DD
    expiration   YYYY-MM-DD
    strike       float
    right        'C' or 'P'
    bid          float >= 0
    ask          float >= 0
    volume       int >= 0
    open_interest int >= 0
    underlying   float  (VIX spot)

Two input formats are accepted:
1. Canonical CSV (columns above).
2. ORATS near-EOD Strikes sample CSV (ticker,cOpra,pOpra,stkPx,expirDate,yte,
   strike,cVolu,cOi,pVolu,pOi,cBidPx,cValue,cAskPx,pBidPx,pValue,pAskPx,...,
   spot_px,trade_date). Pass format='orats' or let it auto-detect from the
   header. Optional `ticker_filter='VIX'` keeps only that underlying
   (ORATS sample files contain the whole market).

Validation is non-fatal: problems are collected into a report dict and rows
that cannot be salvaged are dropped; crossed markets (bid > ask) are flagged
in the report but never crash ingestion. Use `strict=True` to raise on any
validation error instead.
"""

from __future__ import annotations

import pandas as pd

CANONICAL_COLS = ["date", "expiration", "strike", "right", "bid", "ask",
                  "volume", "open_interest", "underlying"]

ORATS_MAP = {
    "trade_date": "date", "expirDate": "expiration", "strike": "strike",
    "spot_px": "underlying",
    "cBidPx": "bid", "cAskPx": "ask", "cVolu": "volume", "cOi": "open_interest",
    "pBidPx": "bid", "pAskPx": "ask", "pVolu": "volume", "pOi": "open_interest",
}

_ORATS_RIGHT_COLS = {
    "C": ("cOpra", "cBidPx", "cAskPx", "cVolu", "cOi"),
    "P": ("pOpra", "pBidPx", "pAskPx", "pVolu", "pOi"),
}


class ChainValidationError(Exception):
    pass


def _from_orats(df: pd.DataFrame, ticker_filter: str | None) -> pd.DataFrame:
    out = df.copy()
    if ticker_filter is not None:
        out = out[out["ticker"] == ticker_filter]
        if out.empty:
            raise ChainValidationError(f"ORATS: no rows for ticker {ticker_filter!r}")
    frames = []
    for right, cols in _ORATS_RIGHT_COLS.items():
        opra_col, bid_col, ask_col, vol_col, oi_col = cols
        sub = pd.DataFrame({
            "date": out["trade_date"],
            "expiration": out["expirDate"],
            "strike": out["strike"],
            "right": right,
            "bid": out[bid_col],
            "ask": out[ask_col],
            "volume": out[vol_col],
            "open_interest": out[oi_col],
            "underlying": out["spot_px"],
        })
        # ORATS strikes file carries both legs even when one side is junk
        # (e.g. same-day-expiry puts with zero quotes); drop rows with
        # non-positive ask only if bid is also zero and volume is zero —
        # handled by validation; keep everything here.
        frames.append(sub)
    return pd.concat(frames, ignore_index=True)


def _detect_format(df: pd.DataFrame, format: str) -> str:
    if format != "auto":
        return format
    if "ticker" in df.columns and "cOpra" in df.columns:
        return "orats"
    return "canonical"


def ingest_chain(path: str, format: str = "auto", ticker_filter: str | None = "VIX",
                 strict: bool = False) -> tuple[pd.DataFrame, dict]:
    """Read, normalize, validate. Returns (chain, report).

    `report` keys: rows_in, rows_out, dropped, crossed_markets, negatives,
    bad_dtypes, notes.
    """
    raw = pd.read_csv(path)
    fmt = _detect_format(raw, format)
    if fmt == "orats":
        chain = _from_orats(raw, ticker_filter)
    elif fmt == "canonical":
        missing = [c for c in CANONICAL_COLS if c not in raw.columns]
        if missing:
            raise ChainValidationError(f"canonical CSV missing columns: {missing}")
        chain = raw[CANONICAL_COLS].copy()
    else:
        raise ChainValidationError(f"unknown format {format!r}")

    report = {"rows_in": len(chain), "rows_out": 0, "dropped": 0,
              "crossed_markets": 0, "negatives": 0, "bad_dtypes": 0, "notes": []}
    rows_in = len(chain)

    # --- normalize dtypes ---
    for col in ("date", "expiration"):
        chain[col] = pd.to_datetime(chain[col], errors="coerce").dt.date
    bad_dates = int(chain["date"].isna().sum() + chain["expiration"].isna().sum())
    for col in ("strike", "bid", "ask", "underlying"):
        chain[col] = pd.to_numeric(chain[col], errors="coerce")
    for col in ("volume", "open_interest"):
        chain[col] = pd.to_numeric(chain[col], errors="coerce").fillna(0).astype(int)
    bad_num = int(chain[["strike", "bid", "ask", "underlying"]].isna().any(axis=1).sum())
    report["bad_dtypes"] = bad_dates + bad_num

    chain["right"] = chain["right"].astype(str).str.strip().str.upper().str[0]
    bad_right = int((~chain["right"].isin(["C", "P"])).sum())

    drop_mask = chain["date"].isna() | chain["expiration"].isna() | \
        chain[["strike", "bid", "ask", "underlying"]].isna().any(axis=1) | \
        ~chain["right"].isin(["C", "P"])
    report["dropped"] = int(drop_mask.sum())
    chain = chain.loc[~drop_mask].copy()

    # --- value validation (flags, not fatal) ---
    crossed = chain["bid"] > chain["ask"]
    report["crossed_markets"] = int(crossed.sum())
    if report["crossed_markets"]:
        report["notes"].append(
            f"{report['crossed_markets']} crossed markets (bid>ask) flagged; "
            "kept as-is, mid set to ask for costing.")
        # Defensive: for costing, a crossed market's mid is unreliable —
        # execution model treats mid as ask in these rows.
        chain.loc[crossed, "bid"] = chain.loc[crossed, "ask"]

    neg = (chain[["bid", "ask", "volume", "open_interest"]] < 0).any(axis=1)
    report["negatives"] = int(neg.sum())
    if report["negatives"]:
        report["notes"].append(f"{report['negatives']} rows with negative "
                               "price/volume; clamped to 0.")
        for col in ("bid", "ask", "volume", "open_interest"):
            chain[col] = chain[col].clip(lower=0)

    chain["mid"] = (chain["bid"] + chain["ask"]) / 2.0
    chain["spread"] = chain["ask"] - chain["bid"]
    chain = chain.sort_values(["expiration", "right", "strike"]).reset_index(drop=True)

    report["rows_out"] = len(chain)
    if strict and (report["dropped"] or report["crossed_markets"]
                   or report["negatives"] or report["bad_dtypes"]):
        raise ChainValidationError(f"strict validation failed: {report}")
    return chain, report
