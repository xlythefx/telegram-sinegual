"""
Live open-position exposure snapshot. Reads binance_positions + ig_positions
(both populated continuously by the upstream sync jobs) and returns a small
dict suitable for handing to Claude.

Notes on data shape:
- Binance positions table has no `strategy` column; only IG does. So
  by_strategy aggregates IG only (caller may surface this honestly).
- Notional: Binance exposes it directly; for IG we compute size * level.
- Unrealized: Binance `unrealized_profit`, IG `unrealized_pnl`.
"""
from __future__ import annotations

from stats import get_conn


def exposure_snapshot() -> dict:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT symbol AS ticker, position_side, ABS(position_amt) AS size,
                       entry_price, mark_price, ABS(notional) AS notional,
                       unrealized_profit AS upnl
                FROM binance_positions
                WHERE position_amt <> 0
            """)
            binance = cur.fetchall()

            cur.execute("""
                SELECT ticker, direction AS position_side, size,
                       level AS entry_price, level AS mark_price,
                       (size * level) AS notional,
                       unrealized_pnl AS upnl, strategy
                FROM ig_positions
                WHERE size > 0
            """)
            ig = cur.fetchall()
    finally:
        conn.close()

    bn = [dict(r, source="binance", strategy=None) for r in binance]
    ig = [dict(r, source="ig") for r in ig]
    rows = bn + ig

    if not rows:
        return {
            "open_count": 0, "total_notional": 0.0, "total_unrealized_pnl": 0.0,
            "by_strategy": [], "by_ticker": [], "biggest_position": None,
            "binance_count": 0, "ig_count": 0,
        }

    total_notional = round(sum(float(r["notional"] or 0) for r in rows), 2)
    total_upnl = round(sum(float(r["upnl"] or 0) for r in rows), 2)

    # by_ticker — sum notional per ticker, sort by absolute exposure
    ticker_agg: dict[str, dict] = {}
    for r in rows:
        t = r["ticker"]
        a = ticker_agg.setdefault(t, {"ticker": t, "notional": 0.0, "upnl": 0.0, "positions": 0})
        a["notional"] += float(r["notional"] or 0)
        a["upnl"] += float(r["upnl"] or 0)
        a["positions"] += 1
    by_ticker = sorted(ticker_agg.values(), key=lambda x: abs(x["notional"]), reverse=True)
    for t in by_ticker:
        t["notional"] = round(t["notional"], 2)
        t["upnl"] = round(t["upnl"], 2)

    # by_strategy — IG only (binance rows have strategy=None)
    strat_agg: dict[str, dict] = {}
    for r in rows:
        s = r.get("strategy")
        if not s:
            continue
        a = strat_agg.setdefault(s, {"strategy": s, "notional": 0.0, "upnl": 0.0, "positions": 0})
        a["notional"] += float(r["notional"] or 0)
        a["upnl"] += float(r["upnl"] or 0)
        a["positions"] += 1
    by_strategy = sorted(strat_agg.values(), key=lambda x: abs(x["notional"]), reverse=True)
    for s in by_strategy:
        s["notional"] = round(s["notional"], 2)
        s["upnl"] = round(s["upnl"], 2)

    biggest = max(rows, key=lambda r: abs(float(r["notional"] or 0)))
    biggest_out = {
        "ticker": biggest["ticker"],
        "source": biggest["source"],
        "side": biggest["position_side"],
        "notional": round(float(biggest["notional"] or 0), 2),
        "upnl": round(float(biggest["upnl"] or 0), 2),
    }

    return {
        "open_count": len(rows),
        "binance_count": len(bn),
        "ig_count": len(ig),
        "total_notional": total_notional,
        "total_unrealized_pnl": total_upnl,
        "by_strategy": by_strategy[:5],
        "by_ticker": by_ticker[:5],
        "biggest_position": biggest_out,
    }
