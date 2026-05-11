"""
Sinegualerts publisher bot.

Modes (one-shot, used for ad-hoc posts):
  python bot.py poll                       - run service: polling + scheduled jobs
  python bot.py daily|weekly|monthly       - publish recap in casual voice
  python bot.py greeting                   - publish casual hello
  python bot.py gold                       - publish gold update with news
  python bot.py exposure                   - publish open-position exposure (operational tone)
  python bot.py strategies                 - publish 7d strategy summary (operational tone)
  python bot.py status --version=X --revision=Patch --notes-file=PATH
                                           - publish system status update (operational tone)

Add --dry to any one-shot mode to print to stdout instead of sending.

Service mode (`poll`) handles BOTH chat commands AND a cron-like internal
scheduler driven by these .env knobs:
  EXEC_WATCH_MINUTES, EXPOSURE_HOURS, STRATEGY_DAYS  (any 0/blank disables)
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

from dotenv import load_dotenv

load_dotenv()

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)
from datetime import time as dtime, timedelta
from apscheduler.triggers.cron import CronTrigger

import json
from pathlib import Path

from stats import (daily_stats, weekly_stats, monthly_stats, PeriodStats,
                   stats_to_dict, format_header, format_footer, fmt_money, get_conn)
from summarizer import (generate_summary, generate_greeting, generate_gold_update,
                        generate_exposure_post, generate_strategy_post,
                        generate_execution_event, generate_status_post)
from market import gold_snapshot
from exposure import exposure_snapshot
from strategy_perf import strategy_summary
from datetime import datetime
from zoneinfo import ZoneInfo

STATE_FILE = Path(__file__).parent / "state.json"


def _build(period: str) -> tuple[PeriodStats, str]:
    fn = {"daily": daily_stats, "weekly": weekly_stats, "monthly": monthly_stats}[period]
    s = fn()
    msg = generate_summary(s)
    return s, msg


def _build_greeting() -> str:
    return generate_greeting()


def _build_gold() -> tuple[dict, str]:
    snap = gold_snapshot()
    msg = generate_gold_update(snap)
    return snap, msg


def _build_exposure() -> tuple[dict, str]:
    snap = exposure_snapshot()
    return snap, generate_exposure_post(snap)


def _exposure_body(snap: dict, msg: str) -> str:
    today = datetime.now(ZoneInfo(os.getenv("TIMEZONE", "Asia/Manila"))).strftime("%A, %b %d, %Y · %H:%M")
    if snap["open_count"] == 0:
        footer = "_No active positions._"
    else:
        footer = (
            f"_Open: {snap['open_count']} · "
            f"Notional: {fmt_money(snap['total_notional'])} · "
            f"Unrealized: {fmt_money(snap['total_unrealized_pnl'])}_"
        )
    return f"*Exposure State* — {today}\n\n{msg}\n\n{footer}"


def _build_strategy(days: int = 7) -> tuple[dict, str]:
    summary = strategy_summary(days)
    return summary, generate_strategy_post(summary)


def _strategy_body(summary: dict, msg: str) -> str:
    today = datetime.now(ZoneInfo(os.getenv("TIMEZONE", "Asia/Manila"))).strftime("%A, %b %d, %Y")
    return f"*Strategy Summary* — last {summary['window_days']} days · {today}\n\n{msg}"


def _gold_body(snap: dict, msg: str) -> str:
    today = datetime.now(ZoneInfo(os.getenv("TIMEZONE", "Asia/Manila"))).strftime("%A, %b %d, %Y")
    arrow = "🟢" if snap["day_change"] >= 0 else "🔴"
    sign = "+" if snap["day_change"] >= 0 else ""
    footer = (
        f"_Spot: {fmt_money(snap['spot'])} | "
        f"Day: {arrow} {sign}{fmt_money(snap['day_change'])} ({sign}{snap['day_change_pct']:.2f}%)_"
    )
    return f"*Gold Update* — {today}\n\n{msg}\n\n{footer}"


# ---- Telegram command handlers ------------------------------------------------

async def _cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, period: str):
    chat_id = update.effective_chat.id
    await context.bot.send_message(chat_id, f"Crunching {period} numbers...")
    try:
        stats, msg = await asyncio.to_thread(_build, period)
    except Exception as e:
        await context.bot.send_message(chat_id, f"Error: {e}")
        return
    body = f"{format_header(stats)}\n\n{msg}\n\n{format_footer(stats)}"
    await context.bot.send_message(chat_id, body, parse_mode="Markdown")


async def cmd_daily(update, context):  await _cmd(update, context, "daily")
async def cmd_weekly(update, context): await _cmd(update, context, "weekly")
async def cmd_monthly(update, context): await _cmd(update, context, "monthly")


async def cmd_greeting(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    try:
        msg = await asyncio.to_thread(_build_greeting)
    except Exception as e:
        await context.bot.send_message(chat_id, f"Error: {e}"); return
    await context.bot.send_message(chat_id, msg)


async def cmd_gold(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await context.bot.send_message(chat_id, "Fetching gold...")
    try:
        snap, msg = await asyncio.to_thread(_build_gold)
    except Exception as e:
        await context.bot.send_message(chat_id, f"Error: {e}"); return
    await context.bot.send_message(chat_id, _gold_body(snap, msg), parse_mode="Markdown")


async def cmd_exposure(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    try:
        snap, msg = await asyncio.to_thread(_build_exposure)
    except Exception as e:
        await context.bot.send_message(chat_id, f"Error: {e}"); return
    await context.bot.send_message(chat_id, _exposure_body(snap, msg), parse_mode="Markdown")


async def cmd_strategies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    try:
        summary, msg = await asyncio.to_thread(_build_strategy, 7)
    except Exception as e:
        await context.bot.send_message(chat_id, f"Error: {e}"); return
    await context.bot.send_message(chat_id, _strategy_body(summary, msg), parse_mode="Markdown")


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Sinegualerts publisher online.\n\n"
        "Commands:\n"
        "/daily - today's recap\n"
        "/weekly - this week's recap\n"
        "/monthly - this month's recap\n"
        "/greeting - casual hello to the channel\n"
        "/gold - live gold price update with news\n"
        "/exposure - current open-position exposure (operational tone)\n"
        "/strategies - last 7-day strategy performance breakdown\n"
        "/chatid - show this chat's id (use as TELEGRAM_CHANNEL_ID)"
    )


async def cmd_chatid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Chat id: `{update.effective_chat.id}`", parse_mode="Markdown")


def run_polling():
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("chatid", cmd_chatid))
    app.add_handler(CommandHandler("daily", cmd_daily))
    app.add_handler(CommandHandler("weekly", cmd_weekly))
    app.add_handler(CommandHandler("monthly", cmd_monthly))
    app.add_handler(CommandHandler("greeting", cmd_greeting))
    app.add_handler(CommandHandler("gold", cmd_gold))
    app.add_handler(CommandHandler("exposure", cmd_exposure))
    app.add_handler(CommandHandler("strategies", cmd_strategies))
    _setup_schedule(app)
    print("Bot service running (polling + scheduled jobs). Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


# ---- scheduled posts ---------------------------------------------------------

TZ = ZoneInfo(os.getenv("TIMEZONE", "Asia/Manila"))


async def _scheduled_send(context: ContextTypes.DEFAULT_TYPE):
    """Each job sets context.job.data = mode (e.g. 'daily', 'weekly', 'gold')."""
    mode = context.job.data
    chat_id = os.getenv("TELEGRAM_CHANNEL_ID", "").strip()
    if not chat_id:
        print(f"[schedule:{mode}] TELEGRAM_CHANNEL_ID not set — skipping")
        return

    print(f"[schedule:{mode}] firing at {datetime.now(TZ):%Y-%m-%d %H:%M %Z}")
    try:
        if mode == "gold":
            snap, msg = await asyncio.to_thread(_build_gold)
            body = _gold_body(snap, msg)
            parse_mode = "Markdown"
        elif mode == "greeting":
            body = await asyncio.to_thread(_build_greeting)
            parse_mode = None
        elif mode == "exposure":
            snap, msg = await asyncio.to_thread(_build_exposure)
            body = _exposure_body(snap, msg)
            parse_mode = "Markdown"
        elif mode == "strategies":
            summary, msg = await asyncio.to_thread(_build_strategy, 7)
            body = _strategy_body(summary, msg)
            parse_mode = "Markdown"
        else:
            stats, msg = await asyncio.to_thread(_build, mode)
            body = f"{format_header(stats)}\n\n{msg}\n\n{format_footer(stats)}"
            parse_mode = "Markdown"
        await context.bot.send_message(chat_id, body, parse_mode=parse_mode)
        print(f"[schedule:{mode}] posted to {chat_id}")
    except Exception as e:
        print(f"[schedule:{mode}] FAILED: {e}")


def _env_int(name: str, default: int) -> int:
    """Parse env var as int. Blank or 0 means 'disabled'."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _setup_schedule(app: Application):
    """Register cron-like jobs. All times are in TIMEZONE (default Asia/Manila)."""
    jq = app.job_queue

    # Daily recap — every day at 23:00 Manila
    jq.run_daily(_scheduled_send, time=dtime(23, 0, tzinfo=TZ), name="daily_recap", data="daily")

    # Weekly recap — Saturdays at 06:00 Manila (PTB days: Mon=0..Sun=6, so Sat=5)
    jq.run_daily(_scheduled_send, time=dtime(6, 0, tzinfo=TZ), days=(5,),
                 name="weekly_recap", data="weekly")

    # Monthly recap — last day of month at 23:00 Manila
    jq.run_custom(_scheduled_send, name="monthly_recap", data="monthly",
                  job_kwargs={"trigger": CronTrigger(day="last", hour=23, minute=0, timezone=TZ)})

    # Gold update — every 8 hours, anchored at 00:00 Manila (so 00:00 / 08:00 / 16:00)
    midnight_today = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    jq.run_repeating(_scheduled_send, interval=timedelta(hours=8),
                     first=midnight_today, name="gold_update", data="gold")

    print(f"Schedule registered (timezone {TZ.key}):")
    print("  - daily_recap   : every day 23:00")
    print("  - weekly_recap  : Saturdays 06:00")
    print("  - monthly_recap : last day of month 23:00")
    print("  - gold_update   : every 8h (00:00 / 08:00 / 16:00)")

    # ---- operational stream (env-gated) -------------------------------------

    exec_watch_min = _env_int("EXEC_WATCH_MINUTES", 5)
    if exec_watch_min > 0:
        jq.run_repeating(_exec_watcher, interval=timedelta(minutes=exec_watch_min),
                         first=timedelta(seconds=20), name="exec_watch")
        print(f"  - exec_watch    : every {exec_watch_min} min")

    exposure_hours = _env_int("EXPOSURE_HOURS", 6)
    if exposure_hours > 0:
        jq.run_repeating(_scheduled_send, interval=timedelta(hours=exposure_hours),
                         first=midnight_today, name="exposure_state", data="exposure")
        print(f"  - exposure_state: every {exposure_hours}h")

    strategy_days = _env_int("STRATEGY_DAYS", 7)
    if strategy_days > 0:
        # Anchor weekly at Sunday 20:00 Manila
        jq.run_daily(_scheduled_send, time=dtime(20, 0, tzinfo=TZ), days=(6,),
                     name="strategy_summary", data="strategies")
        print(f"  - strategy_summary: Sundays 20:00 (window={strategy_days}d)")


# ---- execution event watcher -----------------------------------------------

EXEC_TABLES = {
    # table -> (id col, query for one row)
    "trades":              ("id", "SELECT id, ticker, pnl, strategy FROM trades WHERE id=%s"),
    "binance_pastpositions":("id", "SELECT id, symbol AS ticker, realized_pnl AS pnl, strategy, side FROM binance_pastpositions WHERE id=%s"),
    "ig_past_positions":   ("id", "SELECT id, ticker, pnl, strategy, side FROM ig_past_positions WHERE id=%s"),
}

SOURCE_LABEL = {
    "trades": "Capital.com",
    "binance_pastpositions": "Binance",
    "ig_past_positions": "IG.com",
}

PER_RUN_EVENT_CAP = 5


def _load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def _save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2))


def _current_max_ids() -> dict[str, int]:
    out = {}
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            for table in EXEC_TABLES:
                cur.execute(f"SELECT COALESCE(MAX(id), 0) AS m FROM {table}")
                out[table] = int(cur.fetchone()["m"])
    finally:
        conn.close()
    return out


def _fetch_new_rows(table: str, last_id: int, cap: int) -> list[dict]:
    _, sql_one = EXEC_TABLES[table]
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT id FROM {table} WHERE id > %s ORDER BY id ASC LIMIT %s",
                (last_id, cap),
            )
            ids = [r["id"] for r in cur.fetchall()]
            rows = []
            for new_id in ids:
                cur.execute(sql_one, (new_id,))
                row = cur.fetchone()
                if row:
                    rows.append(row)
            return rows
    finally:
        conn.close()


async def _exec_watcher(context: ContextTypes.DEFAULT_TYPE):
    """Poll the three closed-trade tables, post one operational note per new row."""
    chat_id = os.getenv("TELEGRAM_CHANNEL_ID", "").strip()
    if not chat_id:
        return

    state = _load_state()

    # First-run gate: no state file -> just snapshot current max IDs and exit.
    if not state:
        state = _current_max_ids()
        _save_state(state)
        print(f"[exec_watch] first run, baselined state: {state}")
        return

    posted = 0
    for table in EXEC_TABLES:
        if posted >= PER_RUN_EVENT_CAP:
            break
        last = int(state.get(table, 0))
        new_rows = _fetch_new_rows(table, last, PER_RUN_EVENT_CAP - posted)
        for row in new_rows:
            event = {
                "ticker": row.get("ticker") or "?",
                "source": SOURCE_LABEL[table],
                "side": row.get("side"),
                "pnl": float(row.get("pnl") or 0),
                "strategy": row.get("strategy") or "—",
            }
            try:
                msg = await asyncio.to_thread(generate_execution_event, event)
                await context.bot.send_message(chat_id, msg)
                posted += 1
                state[table] = int(row["id"])
                print(f"[exec_watch] posted {table}#{row['id']}")
            except Exception as e:
                print(f"[exec_watch] FAILED {table}#{row['id']}: {e}")
                # don't advance state — retry next cycle
                break

    _save_state(state)




# ---- One-shot publish (cron) --------------------------------------------------

async def publish_once(mode: str, dry: bool, **kwargs):
    parse_mode = "Markdown"
    if mode == "greeting":
        body = _build_greeting()
        parse_mode = None
        debug = body
    elif mode == "gold":
        snap, msg = _build_gold()
        body = _gold_body(snap, msg)
        debug = f"Snapshot: {snap}\n---\n{body}"
    elif mode == "exposure":
        snap, msg = _build_exposure()
        body = _exposure_body(snap, msg)
        debug = f"Snapshot: {snap}\n---\n{body}"
    elif mode == "strategies":
        summary, msg = _build_strategy(7)
        body = _strategy_body(summary, msg)
        debug = f"Summary: {summary}\n---\n{body}"
    elif mode == "status":
        version = kwargs.get("version") or "—"
        revision = kwargs.get("revision") or "Update"
        notes = kwargs.get("notes") or ""
        if not notes.strip():
            print("status mode requires --notes-file (or --notes='...').", file=sys.stderr)
            sys.exit(2)
        msg = generate_status_post(version, revision, notes)
        body = f"*System Status* — v{version} · {revision}\n\n{msg}"
        debug = body
    else:
        stats, msg = _build(mode)
        body = f"{format_header(stats)}\n\n{msg}\n\n{format_footer(stats)}"
        debug = f"Stats: {stats_to_dict(stats)}\n---\n{body}"

    if dry:
        print("--- DRY RUN ---")
        print(debug)
        return

    chat_id = os.getenv("TELEGRAM_CHANNEL_ID")
    if not chat_id:
        print("TELEGRAM_CHANNEL_ID not set. Use /chatid in the channel/group to find it, then set in .env.", file=sys.stderr)
        sys.exit(2)

    from telegram import Bot
    bot = Bot(token=os.environ["TELEGRAM_BOT_TOKEN"])
    async with bot:
        await bot.send_message(chat_id=chat_id, text=body, parse_mode=parse_mode)
    print(f"Posted {mode} to {chat_id}.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["poll", "daily", "weekly", "monthly",
                                         "greeting", "gold", "exposure",
                                         "strategies", "status"])
    parser.add_argument("--dry", action="store_true", help="Print to stdout instead of sending")
    parser.add_argument("--version", help="(status mode) version string, e.g. 1.2.3")
    parser.add_argument("--revision", help="(status mode) Update | Patch | Hotfix | Maintenance",
                        default="Update")
    parser.add_argument("--notes", help="(status mode) raw notes inline")
    parser.add_argument("--notes-file", help="(status mode) path to a notes text file")
    args = parser.parse_args()

    if args.mode == "poll":
        run_polling()
        return

    kwargs = {}
    if args.mode == "status":
        notes = args.notes
        if args.notes_file:
            notes = Path(args.notes_file).read_text(encoding="utf-8")
        kwargs = {"version": args.version, "revision": args.revision, "notes": notes}
    asyncio.run(publish_once(args.mode, args.dry, **kwargs))


if __name__ == "__main__":
    main()
