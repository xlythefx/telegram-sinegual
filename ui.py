"""
Tkinter control panel for the Sinegualerts publisher.

Run:  python ui.py

Buttons trigger the same query -> Claude -> Telegram pipeline as the cron job,
so you can verify everything end-to-end without waiting for 11pm.
"""
from __future__ import annotations

import asyncio
import json
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

import os
from stats import daily_stats, weekly_stats, monthly_stats, stats_to_dict, format_header, format_footer, fmt_money
from summarizer import generate_summary, generate_greeting, generate_gold_update
from market import gold_snapshot
from zoneinfo import ZoneInfo


PERIODS = {
    "Daily":   daily_stats,
    "Weekly":  weekly_stats,
    "Monthly": monthly_stats,
}


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Sinegualerts Publisher - Control Panel")
        self.geometry("780x620")

        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")

        ttk.Label(top, text="Period:").pack(side="left")
        self.period_var = tk.StringVar(value="Daily")
        for name in PERIODS:
            ttk.Radiobutton(top, text=name, value=name, variable=self.period_var).pack(side="left", padx=4)

        self.dry_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text="Dry run (don't send to Telegram)", variable=self.dry_var).pack(side="left", padx=20)

        actions = ttk.Frame(self, padding=(10, 0))
        actions.pack(fill="x")
        self.preview_btn = ttk.Button(actions, text="1. Preview stats only", command=self.on_preview)
        self.preview_btn.pack(side="left", padx=4)
        self.generate_btn = ttk.Button(actions, text="2. Generate AI message", command=self.on_generate)
        self.generate_btn.pack(side="left", padx=4)
        self.publish_btn = ttk.Button(actions, text="3. Run full pipeline (Stats -> AI -> Telegram)", command=self.on_publish)
        self.publish_btn.pack(side="left", padx=4)

        quick = ttk.LabelFrame(self, text="Quick Posts (one-click)", padding=10)
        quick.pack(fill="x", padx=10, pady=(8, 0))
        self.greet_btn = ttk.Button(quick, text="👋 Send Greeting", command=self.on_greeting)
        self.greet_btn.pack(side="left", padx=4)
        self.gold_btn = ttk.Button(quick, text="🪙 Send Gold Update", command=self.on_gold)
        self.gold_btn.pack(side="left", padx=4)
        ttk.Label(quick, text="(respects the Dry-run checkbox)").pack(side="left", padx=10)

        chat_frame = ttk.Frame(self, padding=10)
        chat_frame.pack(fill="x")
        ttk.Label(chat_frame, text="Channel/Chat ID:").pack(side="left")
        self.chat_var = tk.StringVar(value=os.getenv("TELEGRAM_CHANNEL_ID", ""))
        ttk.Entry(chat_frame, textvariable=self.chat_var, width=40).pack(side="left", padx=6)
        ttk.Label(chat_frame, text="(blank = use TELEGRAM_CHANNEL_ID from .env)").pack(side="left")

        self.status_var = tk.StringVar(value="Idle.")
        ttk.Label(self, textvariable=self.status_var, padding=(10, 4), foreground="#555").pack(fill="x")

        self.output = scrolledtext.ScrolledText(self, wrap="word", font=("Consolas", 10))
        self.output.pack(fill="both", expand=True, padx=10, pady=10)

    # ---- helpers ---------------------------------------------------------

    def log(self, text: str, replace: bool = False):
        self.output.configure(state="normal")
        if replace:
            self.output.delete("1.0", "end")
        self.output.insert("end", text + "\n")
        self.output.see("end")
        self.output.configure(state="disabled")

    def set_busy(self, busy: bool, msg: str = ""):
        state = "disabled" if busy else "normal"
        for b in (self.preview_btn, self.generate_btn, self.publish_btn,
                  self.greet_btn, self.gold_btn):
            b.configure(state=state)
        self.status_var.set(msg or ("Working..." if busy else "Idle."))
        self.update_idletasks()

    def run_async(self, fn):
        """Run a callable in a background thread so the UI stays responsive."""
        def worker():
            try:
                fn()
            except Exception as e:
                self.after(0, lambda: self.log(f"\nERROR: {e}"))
            finally:
                self.after(0, lambda: self.set_busy(False))
        self.set_busy(True)
        threading.Thread(target=worker, daemon=True).start()

    # ---- actions ---------------------------------------------------------

    def on_preview(self):
        self.log(f"\n[{datetime.now():%H:%M:%S}] Computing {self.period_var.get()} stats...", replace=True)

        def work():
            stats = PERIODS[self.period_var.get()]()
            self.after(0, lambda: self.log(json.dumps(stats_to_dict(stats), indent=2, default=str)))
        self.run_async(work)

    def on_generate(self):
        self.log(f"\n[{datetime.now():%H:%M:%S}] Computing stats and asking Claude...", replace=True)

        def work():
            stats = PERIODS[self.period_var.get()]()
            self.after(0, lambda: self.log("--- Aggregated stats sent to Claude ---"))
            self.after(0, lambda: self.log(json.dumps(stats_to_dict(stats), indent=2, default=str)))
            msg = generate_summary(stats)
            self.after(0, lambda: self.log("\n--- Claude message ---"))
            self.after(0, lambda: self.log(msg))
        self.run_async(work)

    def on_publish(self):
        period = self.period_var.get()
        dry = self.dry_var.get()
        self.log(f"\n[{datetime.now():%H:%M:%S}] Full pipeline: {period} (dry={dry})", replace=True)

        def work():
            stats = PERIODS[period]()
            self.after(0, lambda: self.log("--- Aggregated stats ---"))
            self.after(0, lambda: self.log(json.dumps(stats_to_dict(stats), indent=2, default=str)))

            msg = generate_summary(stats)
            body = f"{format_header(stats)}\n\n{msg}\n\n{format_footer(stats)}"
            self.after(0, lambda: self.log("\n--- Final Telegram message ---"))
            self.after(0, lambda: self.log(body))

            if dry:
                self.after(0, lambda: self.log("\nDry run - not sending."))
                return

            chat_id = self.chat_var.get().strip() or os.getenv("TELEGRAM_CHANNEL_ID", "").strip()
            if not chat_id:
                self.after(0, lambda: self.log("\nNo chat id set. Fill the field or .env TELEGRAM_CHANNEL_ID."))
                return

            from telegram import Bot

            async def send():
                bot = Bot(token=os.environ["TELEGRAM_BOT_TOKEN"])
                async with bot:
                    await bot.send_message(chat_id=chat_id, text=body, parse_mode="Markdown")

            asyncio.run(send())
            self.after(0, lambda: self.log(f"\nSent to {chat_id}."))
        self.run_async(work)

    # ---- quick posts -----------------------------------------------------

    def _send_telegram(self, body: str, parse_mode: str | None):
        chat_id = self.chat_var.get().strip() or os.getenv("TELEGRAM_CHANNEL_ID", "").strip()
        if not chat_id:
            self.after(0, lambda: self.log("\nNo chat id set."))
            return
        from telegram import Bot

        async def send():
            bot = Bot(token=os.environ["TELEGRAM_BOT_TOKEN"])
            async with bot:
                await bot.send_message(chat_id=chat_id, text=body, parse_mode=parse_mode)

        asyncio.run(send())
        self.after(0, lambda: self.log(f"\nSent to {chat_id}."))

    def on_greeting(self):
        dry = self.dry_var.get()
        self.log(f"\n[{datetime.now():%H:%M:%S}] Greeting (dry={dry})", replace=True)

        def work():
            msg = generate_greeting()
            self.after(0, lambda: self.log("--- Greeting ---"))
            self.after(0, lambda: self.log(msg))
            if dry:
                self.after(0, lambda: self.log("\nDry run - not sending."))
                return
            self._send_telegram(msg, parse_mode=None)
        self.run_async(work)

    def on_gold(self):
        dry = self.dry_var.get()
        self.log(f"\n[{datetime.now():%H:%M:%S}] Gold update (dry={dry})", replace=True)

        def work():
            snap = gold_snapshot()
            self.after(0, lambda: self.log("--- Gold snapshot ---"))
            self.after(0, lambda: self.log(json.dumps(snap, indent=2, default=str)))
            msg = generate_gold_update(snap)
            today = datetime.now(ZoneInfo(os.getenv("TIMEZONE", "Asia/Manila"))).strftime("%A, %b %d, %Y")
            arrow = "🟢" if snap["day_change"] >= 0 else "🔴"
            sign = "+" if snap["day_change"] >= 0 else ""
            footer = (
                f"_Spot: {fmt_money(snap['spot'])} | "
                f"Day: {arrow} {sign}{fmt_money(snap['day_change'])} ({sign}{snap['day_change_pct']:.2f}%)_"
            )
            body = f"*Gold Update* — {today}\n\n{msg}\n\n{footer}"
            self.after(0, lambda: self.log("\n--- Final message ---"))
            self.after(0, lambda: self.log(body))
            if dry:
                self.after(0, lambda: self.log("\nDry run - not sending."))
                return
            self._send_telegram(body, parse_mode="Markdown")
        self.run_async(work)


if __name__ == "__main__":
    App().mainloop()
