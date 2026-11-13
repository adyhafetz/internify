"""The Curator in Telegram: the 🧹 Tidy up screen in /profile, the weekly run, and the check after a resume import.
Suggestions wait in the database until you apply or skip them, so the weekly message's button still works days later."""
from __future__ import annotations

import asyncio
import html
import json
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from bot.services import watchlist
from service import curator, github_evidence

logger = logging.getLogger(__name__)

PENDING = "tidy_pending"
FIXED = "tidy_fixed"
SKIPPED = "tidy_skipped"      # suggestions you skipped are not suggested again (until what they are about changes)
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


def _skipped() -> set[str]:
    try:
        return set(json.loads(watchlist.get_setting(SKIPPED, "[]")))
    except Exception:
        return set()


def _remember_skipped(sugs: list[curator.Suggestion]) -> None:
    keys = list(_skipped() | {curator.skip_key(s) for s in sugs})
    watchlist.set_setting(SKIPPED, json.dumps(keys[-500:]))


async def run(trigger: str = "manual") -> curator.TidyReport:
    """Run the Curator (off the event loop) and keep its suggestions for review. It shows on the dashboard as its own run."""
    from service import events
    rid = events.start_run(url="", mode="tidy", trigger=trigger)
    watchlist.set_setting("tidy_run", rid)     # so your ✅ / ✕ later show up on the dashboard in this run
    try:
        # a tidy-up you asked for re-reads GitHub now (only repos that changed are downloaded again)
        report = await asyncio.to_thread(curator.tidy, fresh_github=(trigger == "manual"))
    except Exception as err:
        events.emit_run("failed", error=f"The tidy-up stopped: {err}")
        raise
    skipped = _skipped()
    report.suggestions = [s for s in report.suggestions if curator.skip_key(s) not in skipped]
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
    added = github_evidence.recent_added(6)
    if added:
        lines += ["", "<b>Added from your GitHub</b> (tap ✕ to take one back):"]
        chips = []
        for i, a in enumerate(added, start=1):
            where = f"a new project {a['project']}" if a.get("new_project") else a["project"]
            lines.append(f"{i}. {_h(a['tool'])}, with a bullet point in {_h(where)} ({_h(a.get('at', ''))})")
            chips.append(_btn(f"✕ {i}", "tg", i - 1))
        rows += [chips[j:j + 6] for j in range(0, len(chips), 6)]
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
    rows.append([_btn("🔄 Check again", "ty"), _btn("↩ Undo last change", "tz")])
    rows.append([_btn("⬅ Back to profile", "l")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


_REASONS = {"a tidy-up": "before a tidy-up", "importing a resume": "before you imported a resume", "undo": "before your last undo"}


def undo_screen():
    from service.importer import list_backups
    backups = list_backups()
    if not backups:
        return "↩ <b>Undo</b>\n\nThere is nothing to undo yet.", InlineKeyboardMarkup([[_btn("⬅ Back", "tv")]])
    folder, when, reason = backups[0]
    what = _REASONS.get(reason, "at that time")
    text = (f"↩ <b>Undo last change</b>\n\nPut your saved experience back the way it was {what} ({_h(when)})?\n\n"
            "Anything you changed since then is replaced, but what you have now is backed up first, so you can undo this too.")
    return text, InlineKeyboardMarkup([[_btn("↩ Yes, put it back", "tZ"), _btn("Cancel", "tv")]])


async def handle(query, action: str, args: list[str]):
    """Callback actions: ty (check now), tv (view), ta/tx (apply/skip one), tA/tX (apply/skip all)."""
    if action == "ty":
        await query.edit_message_text("⏳ Checking your saved experience…")
        await run()
        return screen()
    if action == "tv":
        return screen()
    if action == "tz":
        return undo_screen()
    if action == "tZ":
        from service.importer import list_backups, restore_backup
        backups = list_backups()
        if not backups:
            return screen("There is nothing to undo.")
        await asyncio.to_thread(restore_backup, backups[0][0])
        _store([], [])
        return screen(f"↩ Done: your saved experience is back the way it was {_REASONS.get(backups[0][2], 'before')} ({_h(backups[0][1])}).")
    if action == "tg":
        added = github_evidence.recent_added(6)
        i = int(args[0]) if args and args[0].isdigit() else -1
        if not 0 <= i < len(added):
            return screen("That one was already taken back.")
        note = await asyncio.to_thread(github_evidence.undo_added, added[i]["fact_id"])
        return screen(f"✕ {_h(note)}.")
    sugs = pending()
    if action in ("ta", "tx"):
        s = next((x for x in sugs if x.id == (args[0] if args else "")), None)
        if s is None:
            return screen("That one was already handled.")
        note = await asyncio.to_thread(curator.apply, s) if action == "ta" else "Skipped"
        if action == "tx":
            _remember_skipped([s])
        _store([x for x in sugs if x.id != s.id])
        _tell_dashboard("apply" if action == "ta" else "skip", note, len(sugs) - 1)
        return screen(f"✅ {_h(note)}." if action == "ta" else "Skipped. I won't suggest it again.")
    if action == "tA":
        notes = [await asyncio.to_thread(curator.apply, s) for s in sugs]
        _store([], [])
        _tell_dashboard("apply", f"applied {len(notes)}", 0)
        return screen(f"✅ Applied {len(notes)} suggestion{'s' if len(notes) != 1 else ''}.")
    if action == "tX":
        _remember_skipped(sugs)
        _store([], [])
        _tell_dashboard("skip", "skipped all", 0)
        return screen("Skipped them all. I won't suggest them again.")
    return None


def _tell_dashboard(action: str, note: str, remaining: int) -> None:
    """Your answers travel back to the Curator on the dashboard, in the run that made the suggestions."""
    from service import events
    rid = watchlist.get_setting("tidy_run", "")
    if not rid:
        return
    with events.in_run(rid):
        events.emit("curator", events.PROGRESS, f"You chose: {action}", reviewed=True, action=action, note=note[:80], remaining=remaining)


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
