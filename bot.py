"""
Sinegualerts publisher bot.

Modes:
  python bot.py poll              - run a polling bot listening for /daily /weekly /monthly
  python bot.py daily [--dry]     - one-shot: post daily recap to TELEGRAM_CHANNEL_ID (used by cron)
  python bot.py weekly [--dry]
  python bot.py monthly [--dry]

Cron (11pm Manila daily):
  0 23 * * * cd /path/to/telegram-bot && python bot.py daily >> logs.txt 2>&1
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

from stats import daily_stats, weekly_stats, monthly_stats, PeriodStats, stats_to_dict, format_header, format_footer, fmt_money
from summarizer import generate_summary, generate_greeting, generate_gold_update
from market import gold_snapshot
from datetime import datetime
from zoneinfo import ZoneInfo


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


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Sinegualerts publisher online.\n\n"
        "Commands:\n"
        "/daily - today's recap\n"
        "/weekly - this week's recap\n"
        "/monthly - this month's recap\n"
        "/greeting - casual hello to the channel\n"
        "/gold - live gold price update with news\n"
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
        else:
            stats, msg = await asyncio.to_thread(_build, mode)
            body = f"{format_header(stats)}\n\n{msg}\n\n{format_footer(stats)}"
            parse_mode = "Markdown"
        await context.bot.send_message(chat_id, body, parse_mode=parse_mode)
        print(f"[schedule:{mode}] posted to {chat_id}")
    except Exception as e:
        print(f"[schedule:{mode}] FAILED: {e}")


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




# ---- One-shot publish (cron) --------------------------------------------------

async def publish_once(mode: str, dry: bool):
    parse_mode = "Markdown"
    if mode == "greeting":
        body = _build_greeting()
        parse_mode = None
        debug = body
    elif mode == "gold":
        snap, msg = _build_gold()
        body = _gold_body(snap, msg)
        debug = f"Snapshot: {snap}\n---\n{body}"
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
    parser.add_argument("mode", choices=["poll", "daily", "weekly", "monthly", "greeting", "gold"])
    parser.add_argument("--dry", action="store_true", help="Print to stdout instead of sending")
    args = parser.parse_args()

    if args.mode == "poll":
        run_polling()
    else:
        asyncio.run(publish_once(args.mode, args.dry))


if __name__ == "__main__":
    main()
