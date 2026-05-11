"""
Live market data via yfinance. Returns small dicts (no DataFrames) so we can
hand them straight to Claude without burning tokens on noise.
"""
from __future__ import annotations

import yfinance as yf


def gold_snapshot() -> dict:
    """Spot, day change, 5-day range, plus 1-2 recent headlines for gold (GC=F)."""
    t = yf.Ticker("GC=F")

    hist = t.history(period="5d", interval="1d")
    if hist.empty:
        raise RuntimeError("No gold price data returned by yfinance")

    last = hist.iloc[-1]
    prev_close = float(hist.iloc[-2]["Close"]) if len(hist) >= 2 else float(last["Open"])
    spot = float(last["Close"])
    change = spot - prev_close
    change_pct = (change / prev_close * 100) if prev_close else 0.0

    headlines = []
    try:
        for item in (t.news or [])[:2]:
            title = item.get("title") or item.get("content", {}).get("title")
            publisher = item.get("publisher") or item.get("content", {}).get("provider", {}).get("displayName")
            if title:
                headlines.append({"title": title, "publisher": publisher or ""})
    except Exception:
        pass

    return {
        "symbol": "GOLD (XAU/USD futures, GC=F)",
        "spot": round(spot, 2),
        "prev_close": round(prev_close, 2),
        "day_change": round(change, 2),
        "day_change_pct": round(change_pct, 2),
        "day_high": round(float(last["High"]), 2),
        "day_low": round(float(last["Low"]), 2),
        "five_day_high": round(float(hist["High"].max()), 2),
        "five_day_low": round(float(hist["Low"].min()), 2),
        "headlines": headlines,
    }
