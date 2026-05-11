# Sinegualerts Telegram Publisher Bot

Daily/weekly/monthly performance recap publisher for the SineguAlerts community.
Reads closed trades from `sinegu_db` (Capital.com `trades`, `binance_pastpositions`, `ig_past_positions`),
computes aggregates **procedurally** (so OpenAI never sees raw rows), and asks GPT to
write a short, casual recap that gets posted to a Telegram channel.

## Setup

```powershell
cd C:\Users\Xlythe\telegram-bot
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Edit `.env` and fill in `TELEGRAM_CHANNEL_ID` after running step 2 below.

## How to find the channel id

1. Add `@sinegualerts_publisher_bot` as an admin of the target channel (or to a test group).
2. In a DM/group with the bot, run `python bot.py poll` then send `/chatid`.
   - For a channel, instead post any message in the channel and check the bot's `getUpdates`,
     or use the group/DM to test first. Channel ids look like `-1001234567890`.
3. Put the id in `.env` as `TELEGRAM_CHANNEL_ID`.

## Usage

Manual test (no Telegram send):
```powershell
python bot.py daily --dry
```

Manual publish to channel:
```powershell
python bot.py daily
python bot.py weekly
python bot.py monthly
```

Interactive bot (chat commands `/daily` `/weekly` `/monthly` `/chatid`):
```powershell
python bot.py poll
```

## Cron (11pm Manila daily)

Manila is UTC+8. 23:00 Manila = 15:00 UTC.

**Linux cron:**
```
0 15 * * * cd /path/to/telegram-bot && /path/to/.venv/bin/python bot.py daily >> logs.txt 2>&1
```

**Windows Task Scheduler:** create a daily task at 23:00 local time running:
```
C:\Users\Xlythe\telegram-bot\.venv\Scripts\python.exe C:\Users\Xlythe\telegram-bot\bot.py daily
```

## How it conserves OpenAI tokens

`stats.py` aggregates everything to ~15 numbers + top-5 tickers per period (a few hundred bytes
of JSON). Only that compact dict goes to GPT. Raw trade rows never leave the DB.

## Files

- `stats.py` - DB queries + procedural aggregation (daily/weekly/monthly)
- `summarizer.py` - OpenAI call (sends only aggregated numbers)
- `bot.py` - Telegram polling + one-shot publish entrypoints
- `.env` - secrets and config
