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
