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
from summarizer import (generate_summary, generate_greeting, generate_gold_update,
                        generate_exposure_post, generate_strategy_post,
                        generate_status_post)
from market import gold_snapshot
from exposure import exposure_snapshot
from strategy_perf import strategy_summary
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

        quick = ttk.LabelFrame(self, text="Quick Posts (one-click · respects Dry-run)", padding=10)
        quick.pack(fill="x", padx=10, pady=(8, 0))
        self.greet_btn = ttk.Button(quick, text="👋 Greeting", command=self.on_greeting)
        self.greet_btn.pack(side="left", padx=4)
        self.gold_btn = ttk.Button(quick, text="🪙 Gold Update", command=self.on_gold)
        self.gold_btn.pack(side="left", padx=4)
        self.exposure_btn = ttk.Button(quick, text="📊 Exposure Now", command=self.on_exposure)
        self.exposure_btn.pack(side="left", padx=4)
        self.strategy_btn = ttk.Button(quick, text="🎯 Strategy 7d", command=self.on_strategy)
        self.strategy_btn.pack(side="left", padx=4)
        self.status_btn = ttk.Button(quick, text="📋 System Status...", command=self.on_status_dialog)
        self.status_btn.pack(side="left", padx=4)

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
                  self.greet_btn, self.gold_btn, self.exposure_btn,
                  self.strategy_btn, self.status_btn):
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

    def on_exposure(self):
        dry = self.dry_var.get()
        self.log(f"\n[{datetime.now():%H:%M:%S}] Exposure snapshot (dry={dry})", replace=True)

        def work():
            snap = exposure_snapshot()
            self.after(0, lambda: self.log("--- Snapshot ---"))
            self.after(0, lambda: self.log(json.dumps(snap, indent=2, default=str)))
            msg = generate_exposure_post(snap)
            today = datetime.now(ZoneInfo(os.getenv("TIMEZONE", "Asia/Manila"))).strftime("%A, %b %d, %Y · %H:%M")
            if snap["open_count"] == 0:
                footer = "_No active positions._"
            else:
                footer = (f"_Open: {snap['open_count']} · "
                          f"Notional: {fmt_money(snap['total_notional'])} · "
                          f"Unrealized: {fmt_money(snap['total_unrealized_pnl'])}_")
            body = f"*Exposure State* — {today}\n\n{msg}\n\n{footer}"
            self.after(0, lambda: self.log("\n--- Final message ---"))
            self.after(0, lambda: self.log(body))
            if dry:
                self.after(0, lambda: self.log("\nDry run - not sending."))
                return
            self._send_telegram(body, parse_mode="Markdown")
        self.run_async(work)

    def on_strategy(self):
        dry = self.dry_var.get()
        self.log(f"\n[{datetime.now():%H:%M:%S}] Strategy summary (dry={dry})", replace=True)

        def work():
            summary = strategy_summary(7)
            self.after(0, lambda: self.log("--- Summary ---"))
            self.after(0, lambda: self.log(json.dumps(summary, indent=2, default=str)))
            msg = generate_strategy_post(summary)
            today = datetime.now(ZoneInfo(os.getenv("TIMEZONE", "Asia/Manila"))).strftime("%A, %b %d, %Y")
            body = f"*Strategy Summary* — last {summary['window_days']} days · {today}\n\n{msg}"
            self.after(0, lambda: self.log("\n--- Final message ---"))
            self.after(0, lambda: self.log(body))
            if dry:
                self.after(0, lambda: self.log("\nDry run - not sending."))
                return
            self._send_telegram(body, parse_mode="Markdown")
        self.run_async(work)

    # ---- system status dialog -------------------------------------------

    def on_status_dialog(self):
        StatusDialog(self)


class StatusDialog(tk.Toplevel):
    """Modal dialog: paste version + revision + notes, AI rewrites, preview, send."""

    REVISIONS = ("Update", "Patch", "Hotfix", "Maintenance")

    def __init__(self, parent: "App"):
        super().__init__(parent)
        self.parent = parent
        self.title("System Status — compose")
        self.geometry("680x600")
        self.transient(parent)

        head = ttk.Frame(self, padding=10)
        head.pack(fill="x")
        ttk.Label(head, text="Version:").grid(row=0, column=0, sticky="w")
        self.version_var = tk.StringVar()
        ttk.Entry(head, textvariable=self.version_var, width=20).grid(row=0, column=1, padx=6, sticky="w")

        ttk.Label(head, text="Revision:").grid(row=0, column=2, sticky="w", padx=(20, 0))
        self.rev_var = tk.StringVar(value="Update")
        ttk.Combobox(head, textvariable=self.rev_var, values=self.REVISIONS,
                     width=14, state="readonly").grid(row=0, column=3, padx=6, sticky="w")

        ttk.Label(self, text="Raw notes / changelog:", padding=(10, 4)).pack(anchor="w")
        self.notes = scrolledtext.ScrolledText(self, height=10, font=("Consolas", 10), wrap="word")
        self.notes.pack(fill="both", expand=False, padx=10)

        actions = ttk.Frame(self, padding=10)
        actions.pack(fill="x")
        self.gen_btn = ttk.Button(actions, text="Generate", command=self.on_generate)
        self.gen_btn.pack(side="left", padx=4)
        self.send_btn = ttk.Button(actions, text="Send to channel", command=self.on_send,
                                   state="disabled")
        self.send_btn.pack(side="left", padx=4)
        ttk.Button(actions, text="Close", command=self.destroy).pack(side="right", padx=4)

        ttk.Label(self, text="Preview:", padding=(10, 4)).pack(anchor="w")
        self.preview = scrolledtext.ScrolledText(self, height=12, font=("Consolas", 10),
                                                 wrap="word", state="disabled")
        self.preview.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self._preview_body: str | None = None

    def _set_preview(self, text: str):
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        self.preview.insert("end", text)
        self.preview.configure(state="disabled")

    def on_generate(self):
        notes = self.notes.get("1.0", "end").strip()
        version = self.version_var.get().strip() or "—"
        revision = self.rev_var.get()
        if not notes:
            self._set_preview("Notes are empty — type changelog above and click Generate.")
            return

        self.gen_btn.configure(state="disabled")
        self.send_btn.configure(state="disabled")
        self._set_preview("Asking Claude...")

        def work():
            try:
                msg = generate_status_post(version, revision, notes)
                body = f"*System Status* — v{version} · {revision}\n\n{msg}"
                self._preview_body = body
                self.after(0, lambda: self._set_preview(body))
                self.after(0, lambda: self.send_btn.configure(state="normal"))
            except Exception as e:
                self.after(0, lambda: self._set_preview(f"ERROR: {e}"))
            finally:
                self.after(0, lambda: self.gen_btn.configure(state="normal"))
        threading.Thread(target=work, daemon=True).start()

    def on_send(self):
        if not self._preview_body:
            return
        if self.parent.dry_var.get():
            self._set_preview(self._preview_body + "\n\n(Dry run — not sending.)")
            return
        self.send_btn.configure(state="disabled")
        body = self._preview_body

        def work():
            try:
                self.parent._send_telegram(body, parse_mode="Markdown")
                self.after(0, lambda: self._set_preview(body + "\n\n✓ Sent."))
            except Exception as e:
                self.after(0, lambda: self._set_preview(body + f"\n\nERROR sending: {e}"))
                self.after(0, lambda: self.send_btn.configure(state="normal"))
        threading.Thread(target=work, daemon=True).start()


if __name__ == "__main__":
    App().mainloop()
