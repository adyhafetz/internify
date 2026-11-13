"""Your application tracker in Telegram: where each job stands after the resume is written.

  * Under every resume: ✅ I applied · 📝 What changed · 🧩 Add the missing tools and rebuild.
  * After you apply: 👀 In Review · 🎤 Interviewing · 🏆 Offer · ❌ Rejected · 🚫 Withdrawn (the same words as the Status
    drop-down in your Google Sheet), and a check-in after FOLLOWUP_DAYS ("any reply yet?").
  * A reminder when a job you have a resume for closes in the next few days and isn't marked applied.
  * /applied: every application by status, with the same buttons.
  * Sundays: a short summary of the week (written, sent, replies).
Marking an interview or an offer also teaches the Archivist which of your bullet points tend to work."""
from __future__ import annotations

import asyncio
import html
import logging
from datetime import datetime
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.handlers.common import restricted
from bot.services import db, watchlist
from service import events, outcomes

logger = logging.getLogger(__name__)

PAGE = 6
STATUS_CODES = {"A": db.APPLIED, "V": db.IN_REVIEW, "I": db.INTERVIEW, "O": db.OFFER, "R": db.REJECTED, "X": db.WITHDRAWN}
ICON = {"Ready": "📄", db.APPLIED: "📨", db.IN_REVIEW: "👀", db.INTERVIEW: "🎤", db.OFFER: "🏆", db.REJECTED: "❌",
        db.WITHDRAWN: "🚫", "Failed": "⚠️", "Processing": "⏳", "New": "🆕"}
FILTERS = {"o": ("Open", db.OPEN_STATUSES), "i": ("Interviewing and offers", (db.INTERVIEW, db.OFFER)),
           "c": ("Closed", (db.REJECTED, db.WITHDRAWN, db.OFFER)), "a": ("All", None)}


def _h(t: object) -> str:
    return html.escape(str(t), quote=False)


def _btn(label: str, *parts: object) -> InlineKeyboardButton:
    return InlineKeyboardButton(label, callback_data="jb:" + ":".join(str(p) for p in parts))


def _gap_label(gaps: list[str]) -> str:
    names = ", ".join(gaps[:2]) + ("…" if len(gaps) > 2 else "")
    return f"🧩 I know {names}: rebuild"[:60]


# ---------------------------------------------------------------- buttons

def status_rows(app: dict) -> list[list[InlineKeyboardButton]]:
    """The buttons that move this application on from where it is now."""
    aid, status = app["id"], app.get("status") or "Ready"
    if status in ("Ready", "Failed", "Processing", db.NEW):
        return [[_btn("✅ I applied", "s", aid, "A"), _btn("🚫 Not applying", "s", aid, "X")]]
    if status == db.APPLIED:
        return [[_btn("👀 In Review", "s", aid, "V"), _btn("🎤 Interviewing", "s", aid, "I")],
                [_btn("❌ Rejected", "s", aid, "R"), _btn("🚫 Withdrawn", "s", aid, "X")]]
    if status == db.IN_REVIEW:
        return [[_btn("🎤 Interviewing", "s", aid, "I"), _btn("❌ Rejected", "s", aid, "R")],
                [_btn("🚫 Withdrawn", "s", aid, "X")]]
    if status == db.INTERVIEW:
        return [[_btn("🏆 Offer", "s", aid, "O"), _btn("❌ Rejected", "s", aid, "R")],
                [_btn("🚫 Withdrawn", "s", aid, "X")]]
    return [[_btn("↩ Reopen (applied)", "s", aid, "A")]]


def delivery_keyboard(outcome) -> Optional[InlineKeyboardMarkup]:
    """Under a freshly written resume."""
    if not getattr(outcome, "app_id", None):
        return None
    app = db.get_application(outcome.app_id) or {"id": outcome.app_id, "status": "Ready"}
    rows = status_rows(app)
    rows.append([_btn("📝 What changed", "d", outcome.app_id)])
    if outcome.gaps:
        rows.append([_btn(_gap_label(outcome.gaps), "f", outcome.app_id)])
    return InlineKeyboardMarkup(rows)


def _app_keyboard(app: dict, back: str = "") -> InlineKeyboardMarkup:
    rows = status_rows(app)
    rows.append([_btn("📝 What changed", "d", app["id"]), _btn("♻️ Rebuild", "r", app["id"])])
    if app.get("gaps"):
        rows.append([_btn(_gap_label(app["gaps"]), "f", app["id"])])
    if back:
        rows.append([_btn("⬅ Back", "L", back, 0)])
    return InlineKeyboardMarkup(rows)


# ---------------------------------------------------------------- screens

def _when(ts: object) -> str:
    return str(ts or "")[:10]


def app_card(app: dict) -> str:
    lines = [f"{ICON.get(app['status'], '•')} <b>{_h(app['job_title'])}</b> at <b>{_h(app['company'])}</b>",
             f"Status: <b>{_h(app['status'])}</b>"
             + (f" since {_when(app.get('status_at'))}" if app.get("status_at") else "")]
    if app.get("applied_at"):
        lines.append(f"Applied {_when(app['applied_at'])}")
    if app.get("deadline"):
        lines.append(f"⏰ Closes {_h(app['deadline'])}")
    if app.get("score"):
        lines.append(f"Resume match {app['score']:.0f}%")
    links = []
    if app.get("job_link"):
        links.append(f'<a href="{_h(app["job_link"])}">job</a>')
    if app.get("drive_link"):
        links.append(f'<a href="{_h(app["drive_link"])}">resume</a>')
    if links:
        lines.append("Open: " + " · ".join(links))
    return "\n".join(lines)


def list_screen(flt: str = "o", page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    title, statuses = FILTERS.get(flt, FILTERS["o"])
    apps, total = db.list_applications(statuses, PAGE, page * PAGE)
    counts = db.status_counts()
    summary = " · ".join(f"{ICON.get(s, '')} {counts.get(s, 0)} {s.lower()}" for s in
                         ("Ready", db.APPLIED, db.IN_REVIEW, db.INTERVIEW, db.OFFER) if counts.get(s))
    lines = ["📊 <b>Your applications</b>", summary or "Nothing tracked yet. Send me a job link to start.", "",
             f"<b>{_h(title)}</b>" + (f" ({total})" if total else "")]
    rows = []
    for app in apps:
        lines.append(f"{ICON.get(app['status'], '•')} {_h(app['job_title'])} · {_h(app['company'])} "
                     f"<i>({_h(app['status'])}, {_when(app.get('applied_at') or app.get('created_at'))})</i>")
        rows.append([_btn(f"{ICON.get(app['status'], '•')} {app['company']} · {app['job_title']}"[:60], "v", app["id"], flt)])
    if not apps and total == 0:
        lines.append("<i>None here.</i>")
    nav = []
    if page > 0:
        nav.append(_btn("◀ Newer", "L", flt, page - 1))
    if (page + 1) * PAGE < total:
        nav.append(_btn("Older ▶", "L", flt, page + 1))
    if nav:
        rows.append(nav)
    rows.append([_btn(("• " if k == flt else "") + name, "L", k, 0) for k, (name, _) in FILTERS.items()][:2])
    rows.append([_btn(("• " if k == flt else "") + name, "L", k, 0) for k, (name, _) in FILTERS.items()][2:])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def details_text(app: dict) -> tuple[str, InlineKeyboardMarkup]:
    """What went onto this resume and what didn't, and buttons to put a left-out job or project back."""
    d = app.get("details") or {}
    lines = [f"📝 <b>What I did for {_h(app['job_title'])} at {_h(app['company'])}</b>", ""]
    if not d:
        lines.append("<i>No details saved for this one (it was made before this feature). Tap ♻️ Rebuild to get them.</i>")
        return "\n".join(lines), InlineKeyboardMarkup([[_btn("♻️ Rebuild", "r", app["id"])]])
    total = len(d.get("covered", [])) + len(d.get("missing", []))
    if total:
        lines.append(f"✅ <b>Shows {len(d['covered'])} of {total} requirements</b>: {_h(', '.join(d['covered'][:12])) or '-'}")
        if d.get("missing"):
            lines.append(f"➖ <b>Not on it</b>: {_h(', '.join(d['missing'][:12]))}")
        lines.append("")
    lines.append(f"<b>Used {d.get('bullets', 0)} of your {d.get('facts', 0)} bullet points</b>"
                 + (f", {d['reworded']} reworded for this job (never adding anything):" if d.get("reworded") else ":"))
    for entry in d.get("picked", []):
        lines.append(f"• <b>{_h(entry['name'])}</b>")
        for b in entry["bullets"][:4]:
            lines.append(f"   {'✏️' if b.get('reworded') else '▫️'} {_h(b['text'][:150])}")
    out = d.get("left_out", [])
    if out or d.get("leadership_out"):
        lines += ["", "<b>Left out</b> (less related to this job):"]
        lines += [f"• {_h(e['name'])}" for e in out] + [f"• {_h(n)}" for n in d.get("leadership_out", [])]
    rows = [[_btn(f"➕ Keep {e['name']}"[:60], "k", app["id"], i)] for i, e in enumerate(out[:4])]
    rows.append([_btn("♻️ Rebuild", "r", app["id"])])
    text = "\n".join(lines)
    return (text if len(text) < 3900 else text[:3880] + "…"), InlineKeyboardMarkup(rows)


# ---------------------------------------------------------------- changing the status

def change_status(app_id: int, status: str) -> Optional[dict]:
    """Save the new status everywhere: the database, your Google Sheet, and what the Archivist learns from."""
    app = db.set_status(app_id, status)
    if app is None:
        return None
    if status in (db.INTERVIEW, db.OFFER):     # the resume worked: remember its bullet points
        outcomes.record(str(app_id), app.get("facts_used") or [], good=True)
    elif status in (db.APPLIED, db.IN_REVIEW, db.WITHDRAWN):   # reopened or a mis-tap: forget it again
        outcomes.record(str(app_id), [], good=False)
    try:
        from bot.services.sheets import find_row_by_url, update_application_status
        row = find_row_by_url(app["job_link"]) if app.get("job_link") else None
        if row:
            update_application_status(row, status=status,   # one of the Sheet's own drop-down words
                                      applied_date=_when(app.get("applied_at")) if status in db.WAITING else "")
    except Exception as err:   # the Sheet is a mirror: never lose the change because Google hiccuped
        logger.warning("Could not update the Sheet status: %s", err)
    return app


def _status_toast(app: dict) -> str:
    s = app["status"]
    if s == db.APPLIED:
        return f"📨 Marked as applied. I'll check in with you in {db.FOLLOWUP_DAYS} days."
    if s == db.IN_REVIEW:
        return "👀 Marked as in review. I'll check in again later."
    if s == db.INTERVIEW:
        return "🎤 Nice! Marked as interviewing. Good luck!"
    if s == db.OFFER:
        return "🏆 Congratulations! Marked as an offer."
    if s == db.REJECTED:
        return "Marked as rejected. On to the next one."
    return f"Marked as {s.lower()}."


# ---------------------------------------------------------------- rebuilding from the tracker

async def _rebuild(query, context, app_id: int, keep: Optional[list[str]] = None, ask_now: bool = False) -> None:
    from bot.handlers.job import TelegramReporter
    from bot.jobflow import rebuild
    from bot.progress import TelegramProgress
    app = db.get_application(app_id)
    if app is None:
        await query.message.reply_text("❌ I can't find that job in your tracker any more.")
        return
    note = ("I'll ask about the tools first, then rebuild." if ask_now
            else "Keeping what you asked for." if keep else "Writing it again from the saved job details.")
    status_msg = await query.message.reply_text(f"♻️ Rebuilding your resume for {app['company']}… {note}")
    run_id = events.start_run(url=app.get("job_link") or "")
    loop = asyncio.get_running_loop()
    reporter = TelegramReporter(query.message, status_msg, TelegramProgress(status_msg, run_id, loop))
    await rebuild(app_id, reporter, bot=context.bot, chat_id=query.message.chat_id, loop=loop, keep_entries=keep, ask_now=ask_now)


# ---------------------------------------------------------------- Telegram handlers

@restricted
async def applied_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text, kb = list_screen("o")
    await update.effective_message.reply_text(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)


@restricted
async def tracker_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    parts = (query.data or "").split(":")[1:]
    act, args = (parts[0] if parts else ""), parts[1:]
    try:
        if act == "L":
            await query.answer()
            text, kb = list_screen(args[0] if args else "o", int(args[1]) if len(args) > 1 else 0)
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
            return
        app_id = int(args[0])
        if act == "v":
            app = db.get_application(app_id)
            await query.answer()
            if app:
                await query.edit_message_text(app_card(app), parse_mode="HTML", disable_web_page_preview=True,
                                              reply_markup=_app_keyboard(app, args[1] if len(args) > 1 else "o"))
            return
        if act == "s":
            status = STATUS_CODES.get(args[1]) if len(args) > 1 else None
            if args[1:2] == ["W"]:
                db.snooze_followup(app_id, 7)
                await query.answer("⏳ Okay, I'll ask again in a week.")
                await _drop_buttons(query)
                return
            app = await asyncio.to_thread(change_status, app_id, status) if status else None
            if app is None:
                await query.answer("That job is no longer in your tracker.")
                return
            await query.answer(_status_toast(app), show_alert=False)
            await _refresh_buttons(query, app)
            return
        if act == "d":
            app = db.get_application(app_id)
            await query.answer()
            if app:
                text, kb = details_text(app)
                await query.message.reply_text(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
            return
        if act in ("r", "f", "k"):
            app = db.get_application(app_id)
            if app is None:
                await query.answer("That job is no longer in your tracker.")
                return
            keep = None
            if act == "k":
                left = (app.get("details") or {}).get("left_out", [])
                i = int(args[1]) if len(args) > 1 else -1
                if not 0 <= i < len(left):
                    await query.answer("That choice has expired.")
                    return
                keep = [left[i]["id"]]
            await query.answer("♻️ On it")
            context.application.create_task(_rebuild(query, context, app_id, keep=keep, ask_now=act == "f"))
            return
        await query.answer()
    except Exception as err:
        logger.error("Tracker button failed: %s", err)
        try:
            await query.answer("Something went wrong. Please try again.")
        except Exception:
            pass


async def _refresh_buttons(query, app: dict) -> None:
    """After a status change, show the next steps on the same message (the resume or the tracker card)."""
    try:
        msg = query.message
        if msg.document is not None:   # under a resume: status buttons + the resume's own buttons
            rows = status_rows(app) + [[_btn("📝 What changed", "d", app["id"])]]
            if app.get("gaps"):
                rows.append([_btn(_gap_label(app["gaps"]), "f", app["id"])])
            await msg.edit_reply_markup(InlineKeyboardMarkup(rows))
        elif msg.text and "Status:" in msg.text:
            await msg.edit_text(app_card(app), parse_mode="HTML", disable_web_page_preview=True, reply_markup=_app_keyboard(app, "o"))
        else:   # a reminder: replace its buttons with the outcome
            await msg.edit_text(f"{msg.text_html}\n\n➡️ {_h(app['status'])}", parse_mode="HTML",
                                reply_markup=InlineKeyboardMarkup(status_rows(app)) if app["status"] in db.WAITING else None)
    except Exception as err:
        logger.debug("Could not refresh the buttons: %s", err)


async def _drop_buttons(query) -> None:
    try:
        await query.message.edit_text(f"{query.message.text_html}\n\n➡️ I'll ask again in a week", parse_mode="HTML")
    except Exception:
        pass


# ---------------------------------------------------------------- reminders and the weekly summary

def followup_message(app: dict) -> tuple[str, InlineKeyboardMarkup]:
    days = db.FOLLOWUP_DAYS
    try:
        days = (datetime.utcnow() - datetime.fromisoformat(str(app["applied_at"])[:19])).days
    except Exception:
        pass
    text = (f"📬 <b>Any news from {_h(app['company'])}?</b>\n"
            f"You applied for {_h(app['job_title'])} {days} days ago"
            + (" (in review)" if app.get("status") == db.IN_REVIEW else "") + ". Heard back?\n"
            "<i>If not, a short polite follow-up email to the recruiter often helps.</i>")
    aid = app["id"]
    kb = InlineKeyboardMarkup([[_btn("🎤 Interviewing", "s", aid, "I"), _btn("❌ Rejected", "s", aid, "R")],
                               [_btn("⏳ Still waiting", "s", aid, "W"), _btn("🚫 Withdrawn", "s", aid, "X")]])
    return text, kb


def deadline_message(app: dict) -> tuple[str, InlineKeyboardMarkup]:
    link = f' <a href="{_h(app["job_link"])}">Open the job</a>' if app.get("job_link") else ""
    text = (f"⏰ <b>{_h(app['job_title'])} at {_h(app['company'])} closes {_h(app['deadline'])}</b>\n"
            f"Your resume is ready but you haven't marked it applied.{link}")
    aid = app["id"]
    kb = InlineKeyboardMarkup([[_btn("✅ I applied", "s", aid, "A"), _btn("🚫 Not applying", "s", aid, "X")]])
    return text, kb


def weekly_text() -> str:
    w = db.week_stats()
    lines = ["📈 <b>Your week</b>",
             f"• {w['written']} resume{'s' if w['written'] != 1 else ''} written, {w['applied']} application{'s' if w['applied'] != 1 else ''} sent",
             f"• {w['interviews']} moved to interviewing or an offer, {w['rejected']} rejection{'s' if w['rejected'] != 1 else ''}",
             f"• {w['waiting']} still waiting for a reply"]
    ready = db.status_counts().get("Ready", 0)
    if ready:
        lines.append(f"• {ready} resume{'s' if ready != 1 else ''} ready but not sent yet: /applied")
    return "\n".join(lines)


async def daily_check(context) -> None:
    """Follow-ups that are due, deadlines coming up, and (on Sundays) the weekly summary."""
    chat_id = watchlist.owner_chat_id()
    if chat_id is None:
        return
    bot = context.bot
    for app in db.due_followups()[:5]:
        text, kb = followup_message(app)
        await bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
        db.snooze_followup(app["id"], 7)   # no answer: ask again in a week, not every day
    for app in db.deadlines_soon(3):
        text, kb = deadline_message(app)
        await bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
        db.mark_deadline_reminded(app["id"])
    from zoneinfo import ZoneInfo
    from bot import config
    try:
        today = datetime.now(ZoneInfo(config.SCAN_TIMEZONE))
    except Exception:
        today = datetime.now()
    if today.weekday() == 6:
        w = db.week_stats()
        if w["written"] or w["applied"] or w["waiting"]:
            await bot.send_message(chat_id, weekly_text(), parse_mode="HTML")


def schedule(application) -> None:
    """Once a day, an hour after the daily job check."""
    if getattr(application, "job_queue", None) is None:
        return
    from datetime import time
    from zoneinfo import ZoneInfo
    from bot import config
    try:
        hh, mm = (int(x) for x in config.SCAN_TIME.split(":"))
        when = time((hh + 1) % 24, mm, tzinfo=ZoneInfo(config.SCAN_TIMEZONE))
    except Exception:
        return
    application.job_queue.run_daily(daily_check, time=when, name="tracker-reminders")
