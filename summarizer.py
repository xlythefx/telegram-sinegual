"""
Hands compact aggregate stats (NOT raw trade rows) to Claude and asks for a
short, friendly Telegram-style update.
"""
from __future__ import annotations

import json
import os
import anthropic

from stats import PeriodStats, stats_to_dict

_SYSTEM = (
    "You are SineguAlerts' publisher. You write short, friendly, casual "
    "Telegram updates about trading performance for our community. "
    "Tone: upbeat but honest, never hype-y, no financial advice. "
    "Keep it under 120 words. Use 1-3 emojis sparingly. "
    "Highlight the headline number (total PnL), win rate, and a notable ticker. "
    "If there were no trades, acknowledge a quiet day in one short line.\n\n"
    "Formatting rules (MANDATORY):\n"
    "- All money values: 1,234.50 (comma thousands, two decimals, no currency symbol).\n"
    "- All percentages: 12.34% (two decimals).\n"
    "- Never invent a number not present in the data."
)


def generate_summary(stats: PeriodStats) -> str:
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    payload = stats_to_dict(stats)

    user_prompt = (
        f"Write a {stats.label.lower()} trading recap for our Telegram channel "
        f"using ONLY these aggregated numbers (do not invent anything not present):\n\n"
        f"{json.dumps(payload, indent=2)}"
    )

    response = client.messages.create(
        model=os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5"),
        max_tokens=400,
        system=_SYSTEM,
        messages=[{"role": "user", "content": user_prompt}],
    )

    return next(b.text for b in response.content if b.type == "text").strip()


def _ask_claude(system: str, user: str, max_tokens: int = 400) -> str:
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    response = client.messages.create(
        model=os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5"),
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return next(b.text for b in response.content if b.type == "text").strip()


_ASK_SYSTEM = (
    "You are SineguAlerts' internal admin assistant. The user is a staff member "
    "asking questions in a private admin Telegram group. Answer concisely and "
    "practically. You can discuss the trading system, draft content, suggest "
    "wording for announcements, or help with operational reasoning. "
    "Keep replies under 250 words unless the question genuinely needs more. "
    "Plain text — no markdown headers."
)


def ask(prompt: str) -> str:
    """Free-form Q&A for the admin group (/ask command)."""
    return _ask_claude(_ASK_SYSTEM, prompt, max_tokens=700)


# ---- knowledge-of-the-day post (brand-philosophy stream) ----------------------

_KNOWLEDGE_SYSTEM = (
    "You are SineguAlerts' brand-voice publisher. Write a SHORT 'knowledge of "
    "the day' post for the public Telegram channel, drawing from the brand "
    "context provided below. Pick ONE small idea, principle, or archetype — "
    "do not try to summarize everything. Translate it into a thought the reader "
    "can apply today. Tone: grounded, reflective, slightly philosophical, never "
    "preachy. Under 110 words. At most 1-2 emojis. No trading talk, no signals, "
    "no calls to action. End with a single quiet line, not a tagline.\n\n"
    "If a 'TODAY'S FOCUS' section is provided, anchor the post around that "
    "idea specifically; otherwise pick freely from the brand context."
)


def generate_knowledge_post() -> str:
    """Daily brand-philosophy post. Reads docs/ for context."""
    from docs_loader import load_brand_context, load_daily_focus

    brand = load_brand_context(max_chars=10000)
    focus = load_daily_focus()

    parts = []
    if focus:
        parts.append(f"TODAY'S FOCUS:\n{focus}")
    if brand:
        parts.append(f"BRAND CONTEXT:\n{brand}")
    parts.append(
        "Write today's knowledge-of-the-day post for the channel. "
        "Vary the angle so it doesn't feel like the same post as yesterday."
    )
    user_prompt = "\n\n".join(parts)

    return _ask_claude(_KNOWLEDGE_SYSTEM, user_prompt, max_tokens=500)


_GREETING_SYSTEM = (
    "You are SineguAlerts' community publisher. Write a SHORT casual greeting "
    "for our Telegram channel — like a friend checking in. Vary the wording every time. "
    "Tone: warm, real, never corporate. 2-3 short sentences max. 1-2 emojis tops. "
    "No trading talk, no signals, no hype. Just 'hey, hope you're doing well' energy."
)


def generate_greeting() -> str:
    return _ask_claude(
        _GREETING_SYSTEM,
        "Write a fresh casual hello to the community. Vary the opening so it doesn't feel canned.",
        max_tokens=250,
    )


_GOLD_SYSTEM = (
    "You are SineguAlerts' market commentator. Write a SHORT, casual gold market "
    "update for our Telegram community. Mention the spot price, today's move, and "
    "weave in what's driving it from the headlines if relevant. "
    "Under 100 words. 1-2 emojis. Conversational, not a research report. "
    "No trade calls, no advice. Use comma thousands and 2 decimals (e.g. 2,640.50, 1.23%)."
)


def generate_gold_update(snapshot: dict) -> str:
    import json
    return _ask_claude(
        _GOLD_SYSTEM,
        "Write a casual gold update for the channel using ONLY this data:\n\n"
        + json.dumps(snapshot, indent=2),
        max_tokens=400,
    )


# ---- operational stream (calm / disciplined voice) -----------------------

_OPERATIONAL_SYSTEM = (
    "You write for SineguAlerts' operational transparency stream. "
    "Voice: calm, structured, disciplined, factual. Never excited, never hype, "
    "no exclamation marks, no rocket/fire/money emojis. At most one understated "
    "marker per post (·, →, —). Short. State what the system did or observed. "
    "No advice, no calls to action, no 'let's go' energy. The reader is "
    "observing a real execution environment, not a signal subscriber.\n\n"
    "Formatting: money as 1,234.50 (comma thousands, two decimals, no currency "
    "symbol). Percentages as 12.34%. Never invent a number not present in data."
)


def generate_exposure_post(snapshot: dict) -> str:
    import json
    if snapshot["open_count"] == 0:
        return _ask_claude(
            _OPERATIONAL_SYSTEM,
            "Write a 1-2 sentence operational note that the system currently "
            "holds no active positions. Calm, factual.",
            max_tokens=120,
        )
    return _ask_claude(
        _OPERATIONAL_SYSTEM,
        "Write a brief exposure-state note for the operational stream. "
        "Mention how many positions are open, total notional, net unrealized PnL, "
        "and a glance at strategy or ticker concentration if relevant. "
        "Under 80 words. Use ONLY this data:\n\n" + json.dumps(snapshot, indent=2),
        max_tokens=300,
    )


def generate_strategy_post(summary: dict) -> str:
    import json
    if not summary["strategies"]:
        return _ask_claude(
            _OPERATIONAL_SYSTEM,
            f"Write a brief operational note that no strategy activity was "
            f"recorded in the last {summary['window_days']} days. Calm, factual.",
            max_tokens=120,
        )
    return _ask_claude(
        _OPERATIONAL_SYSTEM,
        f"Write a brief strategy-behavior summary covering the last "
        f"{summary['window_days']} days. State each strategy's trade count, "
        f"win rate, and total PnL. Lead with the strategy that contributed most. "
        f"Under 100 words. Use ONLY this data:\n\n" + json.dumps(summary, indent=2),
        max_tokens=400,
    )


def generate_execution_event(event: dict) -> str:
    """One-liner for a single closed position. event keys: ticker, source,
    side, pnl, strategy."""
    import json
    return _ask_claude(
        _OPERATIONAL_SYSTEM,
        "Write a SINGLE-LINE operational event note for a position that just "
        "closed. Use the marker ' · ' between fields. Format example: "
        "'Position closed · GOLD on IG.com · VWMA-Reversion · +1,234.50'. "
        "Adapt to actual data. No prose, no second sentence.\n\n"
        + json.dumps(event, indent=2),
        max_tokens=120,
    )


def generate_status_post(version: str, revision: str, notes: str) -> str:
    return _ask_claude(
        _OPERATIONAL_SYSTEM,
        f"Rewrite these raw release notes as a calm system status announcement "
        f"for the operational channel. Lead with the version and revision flag. "
        f"Translate jargon into plain operational language. No marketing tone. "
        f"Under 120 words.\n\n"
        f"Version: {version}\n"
        f"Revision: {revision}\n"
        f"Notes:\n{notes}",
        max_tokens=500,
    )
