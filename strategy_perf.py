"""
Per-strategy performance over a recent window. Aggregates across the three
closed-trade tables (Capital.com `trades`, `binance_pastpositions`,
`ig_past_positions`) and returns a small list sorted by total PnL.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from stats import get_conn, _tz


def strategy_summary(days: int = 7) -> dict:
    """Per-strategy roll-up over the last `days` days (TIMEZONE-bound)."""
    cutoff = datetime.now(_tz()) - timedelta(days=days)

    sql = """
        SELECT strategy, pnl FROM (
            SELECT strategy, COALESCE(pnl, 0) AS pnl
              FROM trades
              WHERE strategy IS NOT NULL AND strategy <> ''
                AND created_at >= %s
            UNION ALL
            SELECT strategy, COALESCE(realized_pnl, 0) AS pnl
              FROM binance_pastpositions
              WHERE strategy IS NOT NULL AND strategy <> ''
                AND closed_at >= %s
            UNION ALL
            SELECT strategy, COALESCE(pnl, 0) AS pnl
              FROM ig_past_positions
              WHERE strategy IS NOT NULL AND strategy <> ''
                AND closed_at >= %s
        ) u
    """
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (cutoff, cutoff, cutoff))
            rows = cur.fetchall()
    finally:
        conn.close()

    agg: dict[str, dict] = {}
    for r in rows:
        s = r["strategy"]
        pnl = float(r["pnl"] or 0)
        a = agg.setdefault(s, {"strategy": s, "trades": 0, "wins": 0, "losses": 0, "total_pnl": 0.0})
        a["trades"] += 1
        a["total_pnl"] += pnl
        if pnl > 0: a["wins"] += 1
        elif pnl < 0: a["losses"] += 1

    out = []
    for s in agg.values():
        decided = s["wins"] + s["losses"]
        out.append({
            "strategy": s["strategy"],
            "trades": s["trades"],
            "wins": s["wins"],
            "losses": s["losses"],
            "win_rate_pct": round(s["wins"] / decided * 100, 2) if decided else 0.0,
            "total_pnl": round(s["total_pnl"], 2),
            "avg_pnl": round(s["total_pnl"] / s["trades"], 2),
        })
    out.sort(key=lambda x: x["total_pnl"], reverse=True)
    return {"window_days": days, "strategies": out}
