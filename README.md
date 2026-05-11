# Sinegualerts Telegram Publisher Bot

Operational-transparency publisher for the SineguAlerts community channel.
Reads live data from `sinegu_db` (open positions, closed trades by strategy) +
yfinance (gold spot), aggregates everything **procedurally** so Claude only
ever sees small numeric summaries, then publishes calm/disciplined posts to
Telegram on a schedule.

## Setup

```powershell
cd C:\Users\Xlythe\telegram-bot
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

copy .env.example .env
notepad .env       # paste your bot token, Anthropic key, channel id
```

## Run

**Service mode** — polling + scheduled jobs in one process:
```powershell
python bot.py poll
```
or double-click `Run Service.bat`. On the VPS, install via NSSM (see "VPS install" below).

**One-shot publishing** — used for ad-hoc posts and `--dry` previews:
```powershell
python bot.py daily               # publish today's recap (casual voice)
python bot.py weekly              # this week
python bot.py monthly             # this month
python bot.py greeting            # casual hello
python bot.py gold                # live gold update + headlines
python bot.py exposure            # current open-position exposure (operational tone)
python bot.py strategies          # last-7d strategy performance (operational tone)
python bot.py status --version=1.2.3 --revision=Patch --notes-file=notes.txt

# add --dry to any of the above to print to stdout instead of sending
```

**Tkinter control panel** — manual previews + send buttons:
```powershell
python ui.py
```
or double-click `Launch UI.bat`. Includes a System Status modal for ad-hoc release notes.

## Telegram chat commands

When the service is running, the bot listens for these in any chat it's a member of:

| Command | Purpose |
|---|---|
| `/daily` `/weekly` `/monthly` | Trade recaps (casual voice) |
| `/greeting` | Casual hello |
| `/gold` | Live gold spot + news |
| `/exposure` | Current open-position exposure (operational tone) |
| `/strategies` | Last 7-day strategy breakdown |
| `/chatid` | Show current chat id (for setting `TELEGRAM_CHANNEL_ID`) |
| `/help` | List commands |

## Schedule (all Asia/Manila)

| Job | When | Voice | Disable via |
|---|---|---|---|
| `daily_recap` | every day 23:00 | casual | (always on) |
| `weekly_recap` | Saturdays 06:00 | casual | (always on) |
| `monthly_recap` | last day of month 23:00 | casual | (always on) |
| `gold_update` | every 8h (00:00 / 08:00 / 16:00) | casual | (always on) |
| `exposure_state` | every `EXPOSURE_HOURS` | operational | `EXPOSURE_HOURS=0` |
| `strategy_summary` | Sundays 20:00 (window=`STRATEGY_DAYS`d) | operational | `STRATEGY_DAYS=0` |

Times come from `TIMEZONE` in `.env` (default `Asia/Manila`).

## Brand voice

Two distinct system prompts in `summarizer.py`:

- `_SYSTEM` — casual/upbeat, used for daily/weekly/monthly recaps and greeting/gold. "Looking back together" tone.
- `_OPERATIONAL_SYSTEM` — calm, structured, disciplined, factual. Used for exposure / strategies / execution events / system status. "Observation environment" tone.

Operational posts MUST avoid: exclamation marks, hype emojis, "let's go" energy, advice, calls to action.

## Scope: summarization only

Real-time entry/exit alerts are owned by the broker projects
(CapitalFlask, binance-flask, igcom) — they fire `tg_send` the moment a
master trade executes. This bot only handles **periodic summarization +
market commentary**: recaps, exposure snapshots, strategy summaries, gold
updates, greetings, system status. No event polling here.

## How tokens are conserved

Every Claude call sends only a small aggregated dict (typically <500 bytes):
counts, sums, win-rates, top-N rankings. Raw trade rows never leave the DB.
Sample monthly cost at full schedule: under $1.

## VPS install (NSSM service)

```powershell
winget install nssm

nssm install SinegualBot "C:\path\to\telegram-bot\.venv\Scripts\python.exe" "bot.py" "poll"
nssm set SinegualBot AppDirectory "C:\path\to\telegram-bot"
nssm set SinegualBot AppStdout    "C:\path\to\telegram-bot\service.log"
nssm set SinegualBot AppStderr    "C:\path\to\telegram-bot\service.log"
nssm start SinegualBot
```

Updates: `git pull` → (if requirements changed) `pip install -r requirements.txt` → `nssm restart SinegualBot`.

## Files

| File | Role |
|---|---|
| `bot.py` | CLI entry, polling, scheduler, exec watcher |
| `stats.py` | DB queries + daily/weekly/monthly aggregation |
| `exposure.py` | Open-position aggregation from `binance_positions` + `ig_positions` |
| `strategy_perf.py` | `GROUP BY strategy` over closed-trade tables |
| `market.py` | yfinance gold snapshot |
| `summarizer.py` | All Claude calls (two voice profiles) |
| `ui.py` | Tkinter control panel + System Status modal |
| `.env` | Secrets and schedule knobs (gitignored) |
