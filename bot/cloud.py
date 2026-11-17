"""The hosted version (MODE=cloud) in Telegram: signing up, who may use the bot, a limit on how fast, /plan and /deleteaccount.

Everything here is only used in cloud mode. In single mode the bot works as it always has (see handlers/common.restricted)."""
from __future__ import annotations

import asyncio
import collections
import html
import logging
import os
import time
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot import config
from bot.services import accounts
from service import users

logger = logging.getLogger(__name__)

TOUCH_EVERY = 60            # seconds: how often "last active" is written
CONFIRM_SECONDS = 10 * 60   # the second "are you sure" must be answered within this
_hits: dict[str, collections.deque] = {}
_told: dict[str, float] = {}
_touched: dict[str, float] = {}


def is_start(update: Update) -> bool:
    text = (update.effective_message.text or "") if update.effective_message else ""
    return text.split("@", 1)[0].split(" ", 1)[0].strip().lower() == "/start"


def too_fast(user_id: str, now: Optional[float] = None) -> bool:
    """More than RATE_LIMIT_PER_MINUTE messages or taps in the last minute?"""
    now = time.monotonic() if now is None else now
    q = _hits.setdefault(user_id, collections.deque())
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= config.RATE_LIMIT_PER_MINUTE:
        return True
    q.append(now)
    return False


async def _say(update: Update, text: str) -> None:
    """Answer whatever the person did (a message or a button tap) in plain words."""
    try:
        if update.callback_query is not None:
            await update.callback_query.answer(text[:190], show_alert=True)
        elif update.effective_message is not None:
            await update.effective_message.reply_text(text)
    except Exception as err:
        logger.debug("Could not answer: %s", err)


async def run_as_account(update: Update, context: ContextTypes.DEFAULT_TYPE, func, args, kwargs):
    """Find (or, on /start, create) this Telegram user's account, then run the handler as them."""
    from bot.handlers.common import _remember_chat
    tg = update.effective_user
    acct = await asyncio.to_thread(accounts.by_telegram, tg.id)
    if acct is None:
        if not is_start(update):
            await _say(update, "👋 Send /start to create your Internify account first.")
            return None
        acct = await asyncio.to_thread(accounts.create, tg.id, tg.first_name or "")
        context.user_data["new_account"] = True
    if acct.status == accounts.PAUSED:
        await _say(update, "⏸ Your account is paused. Please contact the person who runs this bot.")
        return None
    if too_fast(acct.id):
        now = time.monotonic()
        if now - _told.get(acct.id, -999) > 60:   # say it once a minute, not for every message
            _told[acct.id] = now
            await _say(update, "⏳ That's a lot at once. Please slow down a little and try again in a minute.")
        return None
    with users.use(acct.id):
        _remember_chat(update)
        if time.monotonic() - _touched.get(acct.id, -999) > TOUCH_EVERY:
            _touched[acct.id] = time.monotonic()
            await asyncio.to_thread(accounts.touch, acct.id)
        return await func(update, context, *args, **kwargs)


def quiet(func):
    """For handlers that answer typed text only when asked a question: run as the person's account if they have one, and
    otherwise ignore the message (no account is created, nothing is said)."""
    import functools

    @functools.wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        if not users.cloud():
            return await func(update, context, *args, **kwargs)
        tg = update.effective_user
        acct = await asyncio.to_thread(accounts.by_telegram, tg.id) if tg else None
        if acct is None or acct.status != accounts.ACTIVE:
            return None
        with users.use(acct.id):
            return await func(update, context, *args, **kwargs)
    return wrapped


# ---------------------------------------------------------------- what people read

def welcome_text(name: str, new: bool, upgrade: bool = False, reference: str = "") -> str:
    name = html.escape(name, quote=False)       # sent as HTML: a name can't inject markup
    greeting = (f"👋 Welcome {name}! Your Internify account is ready." if new else f"👋 Hi {name}! I'm Internify.")
    return (f"{greeting}\n\n"
            "Send me a job link and I'll write a 1-page resume for that job, using only your real experience, then send you the PDF.\n\n"
            "First, tell me about you:\n"
            "• Send me your current resume file (PDF or Word) and I'll fill in your experience\n"
            "• or use /profile to add it yourself\n\n"
            "Other things:\n"
            "• /profile – see and edit everything on your resume\n"
            "• /applied – your applications: applied, in review, interviewing, offers\n"
            "• /profile → Connect Google – keep each resume in your own Google Drive, with a tracker sheet\n"
            "• /plan – your plan\n"
            "• /dashboard – watch your resumes being written\n"
            "• /deleteaccount – delete your account and all your data\n"
            "• /help – everything I can do"
            + (f"\n\n💳 <b>Paid plan</b> ({config.PAID_PRICE} a month, {config.PAID_RESUMES} resumes and the daily job search): {html.escape(config.PAY_INSTRUCTIONS, quote=False)}"
            + (f"\nPut this reference on your payment: <code>{html.escape(reference)}</code>" if reference else "") if upgrade else ""))


def plan_text(acct: accounts.Account) -> str:
    """Plan, what has been used this month and how to upgrade. Runs as the person whose plan it is."""
    from bot.services import plans
    lim = plans.limits_for(acct)
    used = plans.used("resume", acct.id)
    plan_line = f"• Plan: <b>{lim.name}</b>" + (f", until {acct.plan_until}" if acct.plan_until and not lim.lapsed else "")
    if lim.lapsed:
        plan_line = f"• Plan: <b>Free</b> (your paid plan ended on {acct.plan_until})"
    lines = ["💳 <b>Your plan</b>", "", plan_line]
    lines.append(f"• Resumes this month: <b>{used} of {lim.resumes}</b>, starts again on {plans.next_reset().strftime('%d %b')}"
                 if lim.resumes is not None else f"• Resumes this month: <b>{used}</b> (no limit)")
    lines.append("• Daily job check: " + ("included" if lim.daily_check else "not included"))
    ai, scrape = plans.used("ai", acct.id), plans.used("scrape", acct.id)
    if ai or scrape:
        lines.append(f"• Work done for you this month: {ai} AI calls" + (f", {scrape} page reads through a scraping service" if scrape else ""))
    if acct.created_at:
        lines.append(f"• Member since {acct.created_at[:10]}")
    if lim.plan != plans.UNLIMITED:
        lines += ["", f"<b>{'Renew' if lim.lapsed or lim.plan == plans.PAID else 'Upgrade'}</b>: {html.escape(config.PAY_INSTRUCTIONS, quote=False)}",
                  f"Put this reference on your payment so it can be matched to you: <code>{accounts.reference(acct.id)}</code>"]
    return "\n".join(lines)


# ---------------------------------------------------------------- /plan and /deleteaccount

def _delete_keyboard(step: int) -> InlineKeyboardMarkup:
    if step == 1:
        return InlineKeyboardMarkup([[InlineKeyboardButton("🗑 Yes, I want to delete everything", callback_data="acct:d1")],
                                     [InlineKeyboardButton("Keep my account", callback_data="acct:no")]])
    return InlineKeyboardMarkup([[InlineKeyboardButton("🗑 Delete my account forever", callback_data="acct:d2")],
                                 [InlineKeyboardButton("Keep my account", callback_data="acct:no")]])


async def dashboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/dashboard: a one-time sign-in link to your own live dashboard (sent as a button, so no link preview is fetched)."""
    from bot.services import websession
    if not config.PUBLIC_URL:
        await update.effective_message.reply_text("The web dashboard isn't set up on this server yet.")
        return
    token = await asyncio.to_thread(websession.new_login, users.current())
    await update.effective_message.reply_text(
        "🖥 Your live dashboard shows each step of your resumes as it is written.\n\nThe link works once and for 15 minutes.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Open my dashboard", url=f"{config.PUBLIC_URL}/auth/{token}")]]))


async def plan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    acct = await asyncio.to_thread(accounts.get, users.current())
    await update.effective_message.reply_text(await asyncio.to_thread(plan_text, acct), parse_mode="HTML")


INACTIVE_DAYS = int(os.getenv("INACTIVE_DELETE_DAYS", "183"))   # about six months
INACTIVE_WARN_DAYS = 7


async def retention_job(context) -> None:
    """Once a day: an account with no activity for about six months (and no paid or unlimited plan running) gets a message a week
    before it is deleted, and is then deleted with everything in it. Anything the person does in the bot starts the count again.
    This is what the Privacy Policy promises."""
    import datetime as dt
    from bot.services import watchlist
    if not users.cloud():
        return
    today = dt.date.today()
    for acct in await asyncio.to_thread(accounts.active):
        if acct.plan == "unlimited" or (acct.plan == "paid" and acct.plan_until and acct.plan_until >= today.isoformat()):
            continue
        try:
            last = dt.date.fromisoformat((acct.last_active or acct.created_at)[:10])
        except ValueError:
            continue
        idle = (today - last).days
        if idle < INACTIVE_DAYS - INACTIVE_WARN_DAYS:
            continue
        try:
            with users.use(acct.id):
                chat_id = watchlist.owner_chat_id()
                if idle >= INACTIVE_DAYS:
                    await asyncio.to_thread(accounts.delete_account, acct.id)
                    if chat_id is not None:
                        await context.bot.send_message(chat_id, "🗑 Your Internify account was inactive for about six months, so it and all its data were deleted. "
                                                       "Send /start any time to begin again.")
                elif chat_id is not None and watchlist.get_setting("retention_warned", "") != last.isoformat():
                    watchlist.set_setting("retention_warned", last.isoformat())
                    await context.bot.send_message(chat_id, f"⏳ You haven't used Internify for a while. In {INACTIVE_DAYS - idle} days your account and all its data will be "
                                                   "deleted. Send any message to keep it, or export your data first: /profile, My data.")
        except Exception:
            logger.exception("Retention check failed for %s", acct.id)


@accounts.for_each_user
async def daily_notices(context) -> None:
    """Once a day, for each person: a reminder a few days before their paid plan ends, and one when it has ended. Each is sent once
    per end date, so a renewal (a new end date) starts again."""
    import datetime as dt
    from bot.services import plans, watchlist
    if not users.cloud():
        return
    acct = accounts.get(users.current())
    if acct is None or acct.plan != plans.PAID or not acct.plan_until:
        return
    chat_id = watchlist.owner_chat_id()
    if chat_id is None:
        return
    try:
        days = (dt.date.fromisoformat(acct.plan_until) - dt.date.today()).days
    except ValueError:
        return
    if days < 0 and watchlist.get_setting("plan_ended_told", "") != acct.plan_until:
        watchlist.set_setting("plan_ended_told", acct.plan_until)
        await context.bot.send_message(chat_id, f"📅 Your paid plan ended on {acct.plan_until}, so you're on the free plan now "
                                       f"({config.FREE_RESUMES} resumes a month, no daily job check). Everything you saved is still here.\n\n{config.PAY_INSTRUCTIONS}")
    elif 0 <= days <= config.PLAN_REMINDER_DAYS and watchlist.get_setting("plan_reminded", "") != acct.plan_until:
        watchlist.set_setting("plan_reminded", acct.plan_until)
        when = "today" if days == 0 else "tomorrow" if days == 1 else f"in {days} days"
        await context.bot.send_message(chat_id, f"📅 Your paid plan ends {when} ({acct.plan_until}).\n\n{config.PAY_INSTRUCTIONS}")


async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("delete_step", None)
    await update.effective_message.reply_text(
        "🗑 <b>Delete your account?</b>\n\nThis removes <b>everything</b> I hold about you: your saved experience and bullet points, "
        "your tracker and job pages, your settings and your resume files. Files in your own Google Drive stay yours. "
        "It can't be undone (you can sign up again later, from scratch).",
        parse_mode="HTML", reply_markup=_delete_keyboard(1))


async def account_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action = (query.data or "").split(":", 1)[-1]
    if action == "no":
        context.user_data.pop("delete_step", None)
        await query.edit_message_text("👍 Okay, your account is kept.")
        return
    if action == "d1":
        context.user_data["delete_step"] = time.monotonic()
        await query.edit_message_text("⚠️ <b>Last chance.</b> Everything will be deleted for good. Are you absolutely sure?",
                                      parse_mode="HTML", reply_markup=_delete_keyboard(2))
        return
    if action == "d2":
        started = context.user_data.pop("delete_step", None)
        if started is None or time.monotonic() - started > CONFIRM_SECONDS:
            await query.edit_message_text("That has expired. Send /deleteaccount again if you still want to delete your account.")
            return
        uid = users.current()
        await query.edit_message_text("⏳ Deleting everything…")
        try:
            removed = await asyncio.to_thread(accounts.delete_account, uid)
        except Exception as err:
            logger.error("Account deletion failed for %s: %s", uid, err)
            await query.edit_message_text(f"❌ I couldn't finish deleting your account.\nReason: {err}\nNothing else has been touched; try again.")
            return
        for cache in (_hits, _told, _touched):
            cache.pop(uid, None)
        from bot.handlers import common
        common._remembered_chat.pop(uid, None)
        context.user_data.clear()
        await query.edit_message_text(f"✅ Your account and all your data are deleted ({sum(removed.values())} records, your files and settings). "
                                      "Thank you for trying Internify. Send /start any time to begin again.")
        return
    await query.edit_message_text("That button has expired.")


# ---------------------------------------------------------------- before the hosted version starts

DEFAULT_PAY = "To upgrade, message the person who runs this bot."


def preflight() -> tuple[list[str], list[str]]:
    """Check the hosted settings. Returns (problems that stop it starting, things worth fixing that do not)."""
    from bot import backup
    from bot.services import google_link
    from service import config as sc
    stop, warn = [], []
    if not config.ADMIN_TELEGRAM_ID:
        stop.append("ADMIN_TELEGRAM_ID is not set: nobody could manage plans or see who signed up.")
    if not config.TELEGRAM_BOT_TOKEN:
        stop.append("TELEGRAM_BOT_TOKEN is not set.")
    if not sc.OPENAI_API_KEY:
        stop.append("The AI key is not set (OPENAI_API_KEY or GEMINI_API_KEY): nobody could make a resume.")
    if not config.PUBLIC_URL.startswith("https://"):
        warn.append("PUBLIC_URL is empty or not https: sign-in links and the public website will not work.")
    if config.PAY_INSTRUCTIONS == DEFAULT_PAY:
        warn.append("PAY_INSTRUCTIONS is still the default: people are not told how to pay you.")
    if not google_link.enabled():
        warn.append("Connect Google is off (needs GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, PUBLIC_URL and a working GOOGLE_TOKEN_KEY): people cannot keep a sheet.")
    if config.ALLOWED_USER_IDS:
        warn.append("TELEGRAM_ALLOWED_USER_IDS is ignored in the hosted version: anyone can sign up. Remove it to avoid confusion.")
    age = backup.age_hours()
    if age is None or age > backup.STALE_HOURS:
        warn.append("There is no recent backup yet. The first one is written at 03:30 (server time zone setting SCAN_TIMEZONE).")
    return stop, warn
