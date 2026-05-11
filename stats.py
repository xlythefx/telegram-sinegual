"""
Procedural stats computation for closed trades.

Reads from three tables and computes per-period aggregates so we can hand
*small* numeric summaries to OpenAI rather than dumping raw rows.

Sources:
  - trades                  (Capital.com closed trades)   pnl, date_closed
  - binance_pastpositions   (Binance closed positions)    realized_pnl, closed_at
  - ig_past_positions       (IG.com closed positions)     pnl, closed_at
"""
from __future__ import annotations

import os
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, date
from typing import Iterable
from zoneinfo import ZoneInfo

import pymysql


def get_conn():
    return pymysql.connect(
        host=os.getenv("DB_HOST", "localhost"),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "sinegu_db"),
        port=int(os.getenv("DB_PORT", "3306")),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


@dataclass
class PeriodStats:
    label: str                 # e.g. "Today", "This Week", "This Month"
    range_start: str           # ISO date
    range_end: str             # ISO date
    total_trades: int
    wins: int
    losses: int
    breakeven: int
    win_rate_pct: float
    total_pnl: float
    best_trade_pnl: float
    worst_trade_pnl: float
    avg_pnl: float
    by_source: dict            # {"capital": {...}, "binance": {...}, "ig": {...}}
    top_tickers: list          # [{"ticker": "GOLD", "pnl": 400.0, "trades": 2}, ...]


# ---- raw fetchers ---------------------------------------------------------

def _fetch_capital(conn, start_dt: datetime, end_dt: datetime) -> list[dict]:
    sql = """
        SELECT ticker, pnl, date_closed, time_closed
        FROM trades
        WHERE date_closed >= %s AND date_closed < %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, (start_dt.date(), end_dt.date()))
        rows = cur.fetchall()
    return [
        {"source": "capital", "ticker": r["ticker"], "pnl": float(r["pnl"] or 0)}
        for r in rows
    ]


def _fetch_binance(conn, start_dt: datetime, end_dt: datetime) -> list[dict]:
    sql = """
        SELECT symbol AS ticker, realized_pnl
        FROM binance_pastpositions
        WHERE closed_at >= %s AND closed_at < %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, (start_dt, end_dt))
        rows = cur.fetchall()
    return [
        {"source": "binance", "ticker": r["ticker"], "pnl": float(r["realized_pnl"] or 0)}
        for r in rows
    ]


def _fetch_ig(conn, start_dt: datetime, end_dt: datetime) -> list[dict]:
    sql = """
        SELECT ticker, pnl
        FROM ig_past_positions
        WHERE closed_at >= %s AND closed_at < %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, (start_dt, end_dt))
        rows = cur.fetchall()
    return [
        {"source": "ig", "ticker": r["ticker"], "pnl": float(r["pnl"] or 0)}
        for r in rows
    ]


# ---- aggregators ----------------------------------------------------------

def _summarize(rows: list[dict]) -> dict:
    if not rows:
        return {"trades": 0, "pnl": 0.0, "wins": 0, "losses": 0}
    pnls = [r["pnl"] for r in rows]
    return {
        "trades": len(rows),
        "pnl": round(sum(pnls), 2),
        "wins": sum(1 for p in pnls if p > 0),
        "losses": sum(1 for p in pnls if p < 0),
    }


def _top_tickers(rows: list[dict], n: int = 5) -> list[dict]:
    agg: dict[str, dict] = {}
    for r in rows:
        t = r["ticker"] or "UNKNOWN"
        a = agg.setdefault(t, {"ticker": t, "pnl": 0.0, "trades": 0})
        a["pnl"] += r["pnl"]
        a["trades"] += 1
    out = sorted(agg.values(), key=lambda x: abs(x["pnl"]), reverse=True)[:n]
    for o in out:
        o["pnl"] = round(o["pnl"], 2)
    return out


def compute_period(label: str, start_dt: datetime, end_dt: datetime) -> PeriodStats:
    """Aggregate all closed trades across sources within [start_dt, end_dt)."""
    conn = get_conn()
    try:
        cap = _fetch_capital(conn, start_dt, end_dt)
        bn = _fetch_binance(conn, start_dt, end_dt)
        ig = _fetch_ig(conn, start_dt, end_dt)
    finally:
        conn.close()

    all_rows = cap + bn + ig
    pnls = [r["pnl"] for r in all_rows]
    wins = sum(1 for p in pnls if p > 0)
    losses = sum(1 for p in pnls if p < 0)
    breakeven = sum(1 for p in pnls if p == 0)
    decided = wins + losses

    return PeriodStats(
        label=label,
        range_start=start_dt.date().isoformat(),
        range_end=(end_dt - timedelta(seconds=1)).date().isoformat(),
        total_trades=len(all_rows),
        wins=wins,
        losses=losses,
        breakeven=breakeven,
        win_rate_pct=round((wins / decided * 100), 2) if decided else 0.0,
        total_pnl=round(sum(pnls), 2) if pnls else 0.0,
        best_trade_pnl=round(max(pnls), 2) if pnls else 0.0,
        worst_trade_pnl=round(min(pnls), 2) if pnls else 0.0,
        avg_pnl=round(sum(pnls) / len(pnls), 2) if pnls else 0.0,
        by_source={
            "capital": _summarize(cap),
            "binance": _summarize(bn),
            "ig": _summarize(ig),
        },
        top_tickers=_top_tickers(all_rows),
    )


# ---- period helpers -------------------------------------------------------

def _tz() -> ZoneInfo:
    return ZoneInfo(os.getenv("TIMEZONE", "Asia/Manila"))


def _day_bounds(d: date) -> tuple[datetime, datetime]:
    tz = _tz()
    start = datetime(d.year, d.month, d.day, tzinfo=tz)
    return start, start + timedelta(days=1)


def daily_stats(target_day: date | None = None) -> PeriodStats:
    d = target_day or datetime.now(_tz()).date()
    start, end = _day_bounds(d)
    return compute_period("Today", start, end)


def weekly_stats(target_day: date | None = None) -> PeriodStats:
    d = target_day or datetime.now(_tz()).date()
    monday = d - timedelta(days=d.weekday())
    start, _ = _day_bounds(monday)
    _, end = _day_bounds(d)
    return compute_period("This Week", start, end)


def monthly_stats(target_day: date | None = None) -> PeriodStats:
    d = target_day or datetime.now(_tz()).date()
    first = d.replace(day=1)
    start, _ = _day_bounds(first)
    _, end = _day_bounds(d)
    return compute_period("This Month", start, end)


def stats_to_dict(s: PeriodStats) -> dict:
    return asdict(s)


# ---- presentation helpers -------------------------------------------------

def fmt_money(n: float) -> str:
    """1234.5 -> '1,234.50'."""
    return f"{n:,.2f}"


def fmt_date(iso: str) -> str:
    """'2026-05-11' -> 'Wednesday, May 11, 2026'."""
    d = datetime.fromisoformat(iso).date()
    return d.strftime("%A, %b %d, %Y")


def format_header(s: PeriodStats) -> str:
    """Date line for the published message."""
    if s.range_start == s.range_end:
        return f"*{s.label}* — {fmt_date(s.range_start)}"
    return f"*{s.label}* — {fmt_date(s.range_start)} to {fmt_date(s.range_end)}"


def format_footer(s: PeriodStats) -> str:
    return (
        f"_Trades: {s.total_trades} | "
        f"Win rate: {s.win_rate_pct:.2f}% | "
        f"PnL: {fmt_money(s.total_pnl)}_"
    )
