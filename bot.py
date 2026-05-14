"""
Sinegualerts publisher bot.

Modes (one-shot, used for ad-hoc posts):
  python bot.py poll                       - run service: polling + scheduled jobs
  python bot.py daily|weekly|monthly       - publish recap in casual voice
  python bot.py greeting                   - publish casual hello
  python bot.py gold                       - publish gold update with news
  python bot.py exposure                   - publish open-position exposure
  python bot.py strategies                 - publish 7d strategy summary
  python bot.py status --version=X --revision=Patch --notes-file=PATH
                                           - publish system status update

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

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
from datetime import time as dtime, timedelta
from apscheduler.triggers.cron import CronTrigger

from pathlib import Path

from stats import (daily_stats, weekly_stats, monthly_stats, PeriodStats,
                   stats_to_dict, format_header, format_footer, fmt_money)
from summarizer import (generate_summary, generate_greeting, generate_gold_update,
                        generate_exposure_post, generate_strategy_post,
                        generate_status_post, generate_knowledge_post,
                        ask as ask_assistant)
import docs_loader
from market import gold_snapshot
from exposure import exposure_snapshot
from strategy_perf import strategy_summary
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


# ---- admin gating ------------------------------------------------------------

def _admin_user_ids() -> set[int]:
    raw = os.getenv("TELEGRAM_ADMIN_USER_IDS", "").strip()
    if not raw:
        return set()
    out = set()
    for tok in raw.split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            out.add(int(tok))
        except ValueError:
            pass
    return out


def _is_admin(update: Update) -> bool:
    user = update.effective_user
    if user is None:
        return False
    return user.id in _admin_user_ids()


async def _deny(update: Update) -> None:
    await update.message.reply_text(
        "Not authorized. This command is admin-only. "
        "Ask the operator to add your user ID to TELEGRAM_ADMIN_USER_IDS."
    )


def _public_channel_id() -> str | None:
    cid = os.getenv("TELEGRAM_CHANNEL_ID", "").strip()
    return cid or None


def _admin_chat_id() -> str | None:
    """Where scheduled posts get sent for approval. If unset, scheduled jobs
    fall back to direct-publish to the public channel (legacy behavior)."""
    cid = os.getenv("TELEGRAM_ADMIN_CHAT_ID", "").strip()
    return cid or None


# ---- public utility commands -------------------------------------------------

async def cmd_whoami(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    is_admin = "yes" if _is_admin(update) else "no"
    await update.message.reply_text(
        f"User id: `{user.id}`\n"
        f"Username: @{user.username if user.username else '—'}\n"
        f"Chat id: `{chat.id}`\n"
        f"Admin: {is_admin}",
        parse_mode="Markdown",
    )


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Short welcome — full reference is in /help."""
    base = (
        "Sinegualerts publisher online.\n\n"
        "Type /help for the full command reference."
    )
    if _is_admin(update):
        base += "\n\nYou are an admin — /help will include admin commands."
    await update.message.reply_text(base)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Full command reference. Admin commands appear only for admins."""
    is_admin = _is_admin(update)

    lines = ["*Sinegualerts Publisher — Help*", ""]

    # ---- Public commands ----
    lines += [
        "*Public commands* (anyone in the chat)",
        "",
        "*Recaps & summaries*",
        "  /daily — today's trading recap",
        "  /weekly — this week's recap",
        "  /monthly — this month's recap",
        "  /strategies — last 7-day strategy performance breakdown",
        "  /exposure — current open-position exposure",
        "",
        "*Market & community*",
        "  /gold — live gold price with news commentary",
        "  /greeting — casual hello to the channel",
        "",
        "*Utility*",
        "  /chatid — show this chat's id (use for TELEGRAM\\_CHANNEL\\_ID / TELEGRAM\\_ADMIN\\_CHAT\\_ID)",
        "  /whoami — show your user id (use for TELEGRAM\\_ADMIN\\_USER\\_IDS)",
        "  /start — short welcome",
        "  /help — this message",
    ]

    if is_admin:
        lines += [
            "",
            "━━━━━━━━━━━━━━━━━━━━━",
            "*Admin commands*",
            "",
            "*Draft → Approve flow* (recommended)",
            "  /draft <mode> — generate a post and show Approve / Regenerate / Discard buttons",
            "  /knowledge — shortcut for `/draft knowledge` (brand-philosophy post from docs/)",
            "",
            "  Modes: `daily`, `weekly`, `monthly`, `greeting`, `gold`, `exposure`, `strategies`, `knowledge`",
            "",
            "*Direct publish* (skips approval — be careful)",
            "  /broadcast <text> — send a custom message to the public channel",
            "  /force\\_daily, /force\\_weekly, /force\\_monthly",
            "  /force\\_greeting, /force\\_gold",
            "  /force\\_exposure, /force\\_strategies, /force\\_knowledge",
            "",
            "*AI assist & previews*",
            "  /ask <prompt> — free-form AI Q&A (reply stays in this chat)",
            "  /dryrun <mode> — render a post here without publishing",
            "",
            "*Brand docs*",
            "  /docs — list loaded PDFs, daily.md focus, dated notes",
            "",
            "*System*",
            "  /reload\\_env — re-read `.env` without restarting the service",
            "",
            "━━━━━━━━━━━━━━━━━━━━━",
            "*Scheduled posts* (auto, all Manila time)",
            "",
            "  • Daily recap — 23:00 every day",
            "  • Weekly recap — Saturdays 06:00",
            "  • Monthly recap — last day 23:00",
            "  • Gold update — every 8h (00/08/16)",
            "  • Exposure state — every `EXPOSURE_HOURS`",
            "  • Strategy summary — Sundays 20:00",
            "  • Knowledge of the day — `KNOWLEDGE_HOUR`:00",
            "",
            "_With TELEGRAM\\_ADMIN\\_CHAT\\_ID set, all scheduled posts land here as drafts for approval — nothing auto-publishes._",
            "",
            "━━━━━━━━━━━━━━━━━━━━━",
            "*Tips*",
            "  • Tap 🔄 Regenerate on a draft to roll a fresh take without re-running /draft",
            "  • Edit `docs/daily.md` to steer the next /knowledge post",
            "  • Drop `docs/notes/YYYY-MM-DD.md` for dated brand context",
        ]
    else:
        lines += [
            "",
            "_Admin commands are hidden — your user id is not on the allowlist._",
            "_Use /whoami to get your id, then ask the operator to add it._",
        ]

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_chatid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Chat id: `{update.effective_chat.id}`", parse_mode="Markdown")


# ---- shared: build a post body for a mode ------------------------------------

_POST_MODES = {"daily", "weekly", "monthly", "greeting", "gold",
               "exposure", "strategies", "knowledge"}


def _knowledge_body(msg: str) -> str:
    today = datetime.now(ZoneInfo(os.getenv("TIMEZONE", "Asia/Manila"))).strftime("%A, %b %d, %Y")
    return f"*Knowledge of the Day* — {today}\n\n{msg}"


def _build_post(mode: str) -> tuple[str, str | None]:
    """Render a publishable post body for the given mode. Returns (body, parse_mode)."""
    if mode == "greeting":
        return _build_greeting(), None
    if mode == "gold":
        snap, msg = _build_gold()
        return _gold_body(snap, msg), "Markdown"
    if mode == "exposure":
        snap, msg = _build_exposure()
        return _exposure_body(snap, msg), "Markdown"
    if mode == "strategies":
        summary, msg = _build_strategy(7)
        return _strategy_body(summary, msg), "Markdown"
    if mode == "knowledge":
        msg = generate_knowledge_post()
        return _knowledge_body(msg), "Markdown"
    if mode in ("daily", "weekly", "monthly"):
        stats, msg = _build(mode)
        return f"{format_header(stats)}\n\n{msg}\n\n{format_footer(stats)}", "Markdown"
    raise ValueError(f"unknown mode: {mode}")


# ---- admin commands ----------------------------------------------------------

async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        await _deny(update); return
    text = " ".join(context.args).strip() if context.args else ""
    if not text:
        await update.message.reply_text("Usage: /broadcast <message>")
        return
    chan = _public_channel_id()
    if not chan:
        await update.message.reply_text("TELEGRAM_CHANNEL_ID is not set.")
        return
    try:
        await context.bot.send_message(chan, text)
        await update.message.reply_text(f"Posted to {chan}.")
    except Exception as e:
        await update.message.reply_text(f"Failed to post: {e}")


async def cmd_ask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        await _deny(update); return
    prompt = " ".join(context.args).strip() if context.args else ""
    if not prompt:
        await update.message.reply_text("Usage: /ask <your question>")
        return
    await update.message.reply_text("Thinking...")
    try:
        reply = await asyncio.to_thread(ask_assistant, prompt)
    except Exception as e:
        await update.message.reply_text(f"Error: {e}"); return
    await update.message.reply_text(reply)


async def cmd_dryrun(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        await _deny(update); return
    if not context.args:
        await update.message.reply_text(
            "Usage: /dryrun <mode> — modes: " + ", ".join(sorted(_POST_MODES)))
        return
    mode = context.args[0].strip().lower()
    if mode not in _POST_MODES:
        await update.message.reply_text(f"Unknown mode '{mode}'.")
        return
    await update.message.reply_text(f"Rendering {mode} (dry run)...")
    try:
        body, parse_mode = await asyncio.to_thread(_build_post, mode)
    except Exception as e:
        await update.message.reply_text(f"Error: {e}"); return
    await update.message.reply_text(
        f"--- DRY RUN: {mode} ---\n\n{body}", parse_mode=parse_mode)


async def _force_publish(update: Update, context: ContextTypes.DEFAULT_TYPE, mode: str):
    if not _is_admin(update):
        await _deny(update); return
    chan = _public_channel_id()
    if not chan:
        await update.message.reply_text("TELEGRAM_CHANNEL_ID is not set.")
        return
    await update.message.reply_text(f"Force-publishing {mode}...")
    try:
        body, parse_mode = await asyncio.to_thread(_build_post, mode)
        await context.bot.send_message(chan, body, parse_mode=parse_mode)
    except Exception as e:
        await update.message.reply_text(f"Failed: {e}"); return
    await update.message.reply_text(f"Posted {mode} to {chan}.")


async def cmd_force_daily(u, c):      await _force_publish(u, c, "daily")
async def cmd_force_weekly(u, c):     await _force_publish(u, c, "weekly")
async def cmd_force_monthly(u, c):    await _force_publish(u, c, "monthly")
async def cmd_force_greeting(u, c):   await _force_publish(u, c, "greeting")
async def cmd_force_gold(u, c):       await _force_publish(u, c, "gold")
async def cmd_force_exposure(u, c):   await _force_publish(u, c, "exposure")
async def cmd_force_strategies(u, c): await _force_publish(u, c, "strategies")
async def cmd_force_knowledge(u, c):  await _force_publish(u, c, "knowledge")


async def cmd_knowledge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shortcut for /draft knowledge — drafts today's brand-knowledge post."""
    if not _is_admin(update):
        await _deny(update); return
    context.args = ["knowledge"]
    await cmd_draft(update, context)


async def cmd_docs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        await _deny(update); return
    items = docs_loader.list_loaded_docs()
    if not items:
        await update.message.reply_text("No docs loaded. Drop files in docs/ to add brand context.")
        return
    lines = ["*Loaded docs* (used by /knowledge and brand-aware drafts):", ""]
    for it in items:
        kb = it["size_bytes"] / 1024
        lines.append(f"• `{it['name']}` — {it['kind']} · {it['chars']:,} chars · {kb:.1f} KB")
    focus = docs_loader.load_daily_focus()
    lines.append("")
    if focus:
        preview = (focus[:200] + "…") if len(focus) > 200 else focus
        lines.append(f"*Today's focus* (`docs/daily.md`):\n_{preview}_")
    else:
        lines.append("_No docs/daily.md set. Create it to anchor today's knowledge post._")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_reload_env(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        await _deny(update); return
    try:
        load_dotenv(override=True)
        admins = len(_admin_user_ids())
        chan = _public_channel_id() or "(unset)"
        await update.message.reply_text(
            f".env reloaded.\nAdmins: {admins}\nChannel: {chan}"
        )
    except Exception as e:
        await update.message.reply_text(f"Reload failed: {e}")


# ---- /draft + inline approval flow ------------------------------------------

def _draft_keyboard(draft_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approve", callback_data=f"draft:approve:{draft_id}"),
        InlineKeyboardButton("🔄 Regenerate", callback_data=f"draft:regen:{draft_id}"),
        InlineKeyboardButton("❌ Discard", callback_data=f"draft:discard:{draft_id}"),
    ]])


def _drafts(context: ContextTypes.DEFAULT_TYPE) -> dict:
    """Per-application draft store keyed by draft_id -> {mode, body, parse_mode}."""
    return context.application.bot_data.setdefault("drafts", {})


async def cmd_draft(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        await _deny(update); return
    if not context.args:
        await update.message.reply_text(
            "Usage: /draft <mode> — modes: " + ", ".join(sorted(_POST_MODES)))
        return
    mode = context.args[0].strip().lower()
    if mode not in _POST_MODES:
        await update.message.reply_text(f"Unknown mode '{mode}'.")
        return
    await update.message.reply_text(f"Drafting {mode}...")
    try:
        body, parse_mode = await asyncio.to_thread(_build_post, mode)
    except Exception as e:
        await update.message.reply_text(f"Error: {e}"); return

    import uuid
    draft_id = uuid.uuid4().hex[:8]
    _drafts(context)[draft_id] = {"mode": mode, "body": body, "parse_mode": parse_mode}

    header = f"📝 *Draft: {mode}* (id `{draft_id}`)\n\n"
    await update.message.reply_text(
        header + body,
        parse_mode="Markdown" if parse_mode == "Markdown" else None,
        reply_markup=_draft_keyboard(draft_id),
    )


async def on_draft_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id if query.from_user else None
    if user_id not in _admin_user_ids():
        await query.edit_message_reply_markup(reply_markup=None)
        await context.bot.send_message(query.message.chat.id,
                                       "Not authorized to act on drafts.")
        return

    try:
        _, action, draft_id = query.data.split(":", 2)
    except ValueError:
        return

    drafts = _drafts(context)
    draft = drafts.get(draft_id)
    if not draft:
        await query.edit_message_reply_markup(reply_markup=None)
        await context.bot.send_message(query.message.chat.id,
                                       f"Draft `{draft_id}` no longer available "
                                       "(service restart wipes drafts).",
                                       parse_mode="Markdown")
        return

    if action == "discard":
        drafts.pop(draft_id, None)
        await query.edit_message_reply_markup(reply_markup=None)
        await context.bot.send_message(query.message.chat.id,
                                       f"❌ Draft `{draft_id}` discarded.",
                                       parse_mode="Markdown")
        return

    if action == "regen":
        await context.bot.send_message(query.message.chat.id,
                                       f"🔄 Regenerating {draft['mode']}...")
        try:
            body, parse_mode = await asyncio.to_thread(_build_post, draft["mode"])
        except Exception as e:
            await context.bot.send_message(query.message.chat.id, f"Error: {e}")
            return
        drafts[draft_id] = {"mode": draft["mode"], "body": body, "parse_mode": parse_mode}
        header = f"📝 *Draft: {draft['mode']}* (id `{draft_id}`) — regenerated\n\n"
        await context.bot.send_message(
            query.message.chat.id,
            header + body,
            parse_mode="Markdown" if parse_mode == "Markdown" else None,
            reply_markup=_draft_keyboard(draft_id),
        )
        await query.edit_message_reply_markup(reply_markup=None)
        return

    if action == "approve":
        chan = _public_channel_id()
        if not chan:
            await context.bot.send_message(query.message.chat.id,
                                           "TELEGRAM_CHANNEL_ID not set.")
            return
        try:
            await context.bot.send_message(chan, draft["body"],
                                           parse_mode=draft["parse_mode"])
        except Exception as e:
            await context.bot.send_message(query.message.chat.id, f"Publish failed: {e}")
            return
        drafts.pop(draft_id, None)
        await query.edit_message_reply_markup(reply_markup=None)
        await context.bot.send_message(query.message.chat.id,
                                       f"✅ Draft `{draft_id}` published to {chan}.",
                                       parse_mode="Markdown")
        return


def run_polling():
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("chatid", cmd_chatid))
    app.add_handler(CommandHandler("whoami", cmd_whoami))
    app.add_handler(CommandHandler("daily", cmd_daily))
    app.add_handler(CommandHandler("weekly", cmd_weekly))
    app.add_handler(CommandHandler("monthly", cmd_monthly))
    app.add_handler(CommandHandler("greeting", cmd_greeting))
    app.add_handler(CommandHandler("gold", cmd_gold))
    app.add_handler(CommandHandler("exposure", cmd_exposure))
    app.add_handler(CommandHandler("strategies", cmd_strategies))

    # admin commands
    app.add_handler(CommandHandler("broadcast", cmd_broadcast))
    app.add_handler(CommandHandler("ask", cmd_ask))
    app.add_handler(CommandHandler("draft", cmd_draft))
    app.add_handler(CommandHandler("dryrun", cmd_dryrun))
    app.add_handler(CommandHandler("reload_env", cmd_reload_env))
    app.add_handler(CommandHandler("force_daily", cmd_force_daily))
    app.add_handler(CommandHandler("force_weekly", cmd_force_weekly))
    app.add_handler(CommandHandler("force_monthly", cmd_force_monthly))
    app.add_handler(CommandHandler("force_greeting", cmd_force_greeting))
    app.add_handler(CommandHandler("force_gold", cmd_force_gold))
    app.add_handler(CommandHandler("force_exposure", cmd_force_exposure))
    app.add_handler(CommandHandler("force_strategies", cmd_force_strategies))
    app.add_handler(CommandHandler("force_knowledge", cmd_force_knowledge))
    app.add_handler(CommandHandler("docs", cmd_docs))
    app.add_handler(CommandHandler("knowledge", cmd_knowledge))
    app.add_handler(CallbackQueryHandler(on_draft_callback, pattern=r"^draft:"))

    _setup_schedule(app)
    print("Bot service running (polling + scheduled jobs). Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


# ---- scheduled posts ---------------------------------------------------------

TZ = ZoneInfo(os.getenv("TIMEZONE", "Asia/Manila"))


async def _scheduled_send(context: ContextTypes.DEFAULT_TYPE):
    """Build the scheduled post and route it for approval.

    If TELEGRAM_ADMIN_CHAT_ID is set, the post lands in the admin chat with
    Approve/Regenerate/Discard buttons — nothing reaches the public channel
    until a listed admin taps Approve.

    If TELEGRAM_ADMIN_CHAT_ID is NOT set, falls back to direct-publish to
    TELEGRAM_CHANNEL_ID (legacy behavior).
    """
    mode = context.job.data
    public_chan = _public_channel_id()
    admin_chat = _admin_chat_id()

    print(f"[schedule:{mode}] firing at {datetime.now(TZ):%Y-%m-%d %H:%M %Z}")
    try:
        body, parse_mode = await asyncio.to_thread(_build_post, mode)
    except Exception as e:
        print(f"[schedule:{mode}] BUILD FAILED: {e}")
        return

    if admin_chat:
        import uuid
        draft_id = uuid.uuid4().hex[:8]
        context.application.bot_data.setdefault("drafts", {})[draft_id] = {
            "mode": mode, "body": body, "parse_mode": parse_mode,
        }
        header = (f"📝 *Scheduled draft: {mode}* (id `{draft_id}`)\n"
                  f"_Auto-generated at {datetime.now(TZ):%H:%M %Z}. "
                  f"Approve to publish to the channel._\n\n")
        try:
            await context.bot.send_message(
                admin_chat,
                header + body,
                parse_mode="Markdown" if parse_mode == "Markdown" else None,
                reply_markup=_draft_keyboard(draft_id),
            )
            print(f"[schedule:{mode}] sent to admin {admin_chat} for approval (id {draft_id})")
        except Exception as e:
            print(f"[schedule:{mode}] FAILED to send draft: {e}")
        return

    # Legacy direct-publish fallback
    if not public_chan:
        print(f"[schedule:{mode}] neither TELEGRAM_ADMIN_CHAT_ID nor TELEGRAM_CHANNEL_ID set — skipping")
        return
    try:
        await context.bot.send_message(public_chan, body, parse_mode=parse_mode)
        print(f"[schedule:{mode}] posted directly to {public_chan} (no admin approval configured)")
    except Exception as e:
        print(f"[schedule:{mode}] PUBLISH FAILED: {e}")


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

    # Knowledge of the day — daily at KNOWLEDGE_HOUR (default 09:00). Set to -1 to disable.
    knowledge_hour = _env_int("KNOWLEDGE_HOUR", 9)
    if knowledge_hour >= 0:
        jq.run_daily(_scheduled_send, time=dtime(knowledge_hour, 0, tzinfo=TZ),
                     name="knowledge_post", data="knowledge")
        print(f"  - knowledge_post: every day {knowledge_hour:02d}:00 (from docs/)")






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
