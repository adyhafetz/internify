"""The Curator in Telegram: the 🧹 Tidy up screen in /profile, the weekly run, and the check after a resume import.
Suggestions wait in the database until you apply or skip them, so the weekly message's button still works days later."""
from __future__ import annotations

import asyncio
import html
import json
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from bot.services import watchlist
from service import curator

logger = logging.getLogger(__name__)

PENDING = "tidy_pending"
FIXED = "tidy_fixed"
SHOW = 6


def _h(t: object) -> str:
    return html.escape(str(t), quote=False)


def _btn(label: str, *parts: object) -> InlineKeyboardButton:
    return InlineKeyboardButton(label, callback_data="xp:" + ":".join(str(p) for p in parts))


def pending() -> list[curator.Suggestion]:
    try:
        return curator.from_json(json.loads(watchlist.get_setting(PENDING, "[]")))
    except Exception:
        return []


def _store(sugs: list[curator.Suggestion], fixed: list[str] | None = None) -> None:
    watchlist.set_setting(PENDING, json.dumps(curator.to_json(sugs)))
    if fixed is not None:
        watchlist.set_setting(FIXED, json.dumps(fixed))


def _fixed() -> list[str]:
    try:
        return json.loads(watchlist.get_setting(FIXED, "[]"))
    except Exception:
        return []


async def run(trigger: str = "manual") -> curator.TidyReport:
    """Run the Curator (off the event loop) and keep its suggestions for review. It shows on the dashboard as its own run."""
    from service import events
    events.start_run(url="", mode="tidy", trigger=trigger)
    try:
        report = await asyncio.to_thread(curator.tidy)
    except Exception as err:
        events.emit_run("failed", error=f"The tidy-up stopped: {err}")
        raise
    _store(report.suggestions, report.fixed)
    events.emit_run("finished", tidy=True, fixed=len(report.fixed), suggestions=len(report.suggestions))
    return report


def screen(notice: str = ""):
    sugs, fixed = pending(), _fixed()
    lines = [notice, ""] if notice else []
    lines.append("🧹 <b>Tidy up</b>")
    if fixed:
        lines += ["", "<b>Fixed automatically</b> (a backup was kept):"] + [f"• {_h(x)}" for x in fixed[:8]]
        if len(fixed) > 8:
            lines.append(f"• …and {len(fixed) - 8} more")
    rows = []
    if sugs:
        lines += ["", f"<b>Suggestions</b> ({len(sugs)}). Nothing changes until you tap ✅."]
        for n, s in enumerate(sugs[:SHOW], start=1):
            lines.append(f"\n<b>{n}.</b> {_h(s.text)}")
            rows.append([_btn(f"✅ {n}", "ta", s.id), _btn(f"✕ {n}", "tx", s.id)])
        if len(sugs) > SHOW:
            lines.append(f"\n…{len(sugs) - SHOW} more after these.")
        rows.append([_btn("✅ Apply all", "tA"), _btn("✕ Skip all", "tX")])
    elif not fixed:
        lines += ["", "Everything looks tidy. ✨"]
    else:
        lines += ["", "No other suggestions. ✨"]
    rows.append([_btn("🔄 Check again", "ty"), _btn("⬅ Back to profile", "l")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def handle(query, action: str, args: list[str]):
    """Callback actions: ty (check now), tv (view), ta/tx (apply/skip one), tA/tX (apply/skip all)."""
    if action == "ty":
        await query.edit_message_text("⏳ Checking your saved experience…")
        await run()
        return screen()
    if action == "tv":
        return screen()
    sugs = pending()
    if action in ("ta", "tx"):
        s = next((x for x in sugs if x.id == (args[0] if args else "")), None)
        if s is None:
            return screen("That one was already handled.")
        note = await asyncio.to_thread(curator.apply, s) if action == "ta" else "Skipped"
        _store([x for x in sugs if x.id != s.id])
        return screen(f"✅ {_h(note)}." if action == "ta" else "Skipped.")
    if action == "tA":
        notes = [await asyncio.to_thread(curator.apply, s) for s in sugs]
        _store([], [])
        return screen(f"✅ Applied {len(notes)} suggestion{'s' if len(notes) != 1 else ''}.")
    if action == "tX":
        _store([], [])
        return screen("Skipped them all.")
    return None


def summary(report: curator.TidyReport) -> str:
    if not report.fixed and not report.suggestions:
        return ""
    bits = []
    if report.fixed:
        bits.append(f"fixed {len(report.fixed)} small thing{'s' if len(report.fixed) != 1 else ''} automatically")
    if report.suggestions:
        bits.append(f"have {len(report.suggestions)} suggestion{'s' if len(report.suggestions) != 1 else ''} for you")
    return "🧹 I tidied your saved experience: I " + " and ".join(bits) + "."


async def send_report(bot, chat_id: int, report: curator.TidyReport) -> None:
    text = summary(report)
    if text:
        await bot.send_message(chat_id, text, reply_markup=InlineKeyboardMarkup([[_btn("🧹 Review", "tv")]]))


async def weekly(context) -> None:
    chat_id = watchlist.owner_chat_id()
    if chat_id is None:
        return
    try:
        await send_report(context.bot, chat_id, await run("weekly"))
    except Exception as err:
        logger.warning("Weekly tidy-up failed: %s", err)


def schedule(application) -> None:
    """Every Sunday, at the daily check's time."""
    if getattr(application, "job_queue", None) is None:
        return
    from datetime import time
    from zoneinfo import ZoneInfo
    from bot import config
    try:
        hh, mm = (int(x) for x in config.SCAN_TIME.split(":"))
        when = time(hh, mm, tzinfo=ZoneInfo(config.SCAN_TIMEZONE))
    except Exception:
        return
    application.job_queue.run_daily(weekly, time=when, days=(0,), name="weekly-tidy")
