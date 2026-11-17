"""The maintainer's commands in Telegram (hosted version only), for ADMIN_TELEGRAM_ID alone. <who> can be a user id, a Telegram id or
the INT-XXXXXX payment reference a person put on their payment:

  /admin                         who is signed up, by plan, and this month's usage
  /users [text]                  find people by name, user id or Telegram id
  /setplan <who> <plan> [date]   give a plan (free, paid or unlimited), with an end date (YYYY-MM-DD) for paid
  /pauseuser <who> [note]        stop someone using the bot (their data is kept)
  /resumeuser <who>              let them back in

Paying is manual at first: a person pays you, then you run /setplan. The person is told."""
from __future__ import annotations

import asyncio
import datetime as dt
import functools
import logging
from html import escape
from typing import Optional

from telegram import Update
from telegram.ext import ContextTypes

from bot import config
from bot.services import accounts, db, plans, watchlist
from service import users

logger = logging.getLogger(__name__)


def _h(t: object) -> str:
    return escape(str(t), quote=False)


def admin_only(func):
    """Run only for the maintainer; anyone else gets no answer at all (the commands aren't advertised to them)."""
    @functools.wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE):
        tg = update.effective_user
        if not config.ADMIN_TELEGRAM_ID or tg is None or tg.id != config.ADMIN_TELEGRAM_ID:
            return None
        return await func(update, context)
    return wrapped


def find(who: str) -> Optional[accounts.Account]:
    """The account for a user id or a Telegram id (a user id wins if both could match)."""
    who = (who or "").strip()
    if not who:
        return None
    acct = accounts.get(who) if users._ID.match(who) else None
    if acct is None and who.lstrip("-").isdigit():
        acct = accounts.by_telegram(int(who))
    if acct is None and who.lower().startswith("int-"):    # the code a person puts on their payment
        acct = accounts.by_reference(who)
    return acct if acct and acct.status != accounts.DELETED else None


def search(text: str, limit: int = 15) -> list[accounts.Account]:
    t = (text or "").strip().lower()
    with db._get_connection() as conn:
        rows = conn.execute("SELECT * FROM accounts WHERE status != 'deleted' ORDER BY rowid DESC").fetchall()
    out = [accounts._row(r) for r in rows]
    if t:
        out = [a for a in out if t in a.name.lower() or t in a.id or t == str(a.telegram_id)]
    return out[:limit]


def summary_text() -> str:
    with db._get_connection() as conn:
        rows = conn.execute("SELECT plan, status, COUNT(*) FROM accounts WHERE status != 'deleted' GROUP BY plan, status").fetchall()
    total = sum(r[2] for r in rows)
    by_plan: dict[str, int] = {}
    paused = 0
    for plan, status, n in rows:
        by_plan[plan] = by_plan.get(plan, 0) + n
        paused += n if status == accounts.PAUSED else 0
    resumes, ai, scrape = plans.server_total("resume"), plans.server_total("ai"), plans.server_total("scrape")
    cap = config.AI_MONTHLY_BUDGET_CALLS
    lines = ["🛠 <b>Admin</b>", "", f"• {total} people" + (f" ({paused} paused)" if paused else ""),
             "• " + ", ".join(f"{n} {p}" for p, n in sorted(by_plan.items())) if by_plan else "• nobody yet",
             f"• This month: {resumes} resumes, {ai} AI calls" + (f" of {cap} ({round(100 * ai / cap)}%)" if cap else "")
             + f", {scrape} scraping calls"]
    recent = search("", 8)
    if recent:
        lines += ["", "<b>Newest</b>"] + [f"• <code>{a.id}</code> {_h(a.name) or '(no name)'} · {a.plan}"
                                          + (f" until {a.plan_until}" if a.plan_until else "") + (" · paused" if a.status == accounts.PAUSED else "") for a in recent]
    from bot import backup
    lines += ["", "💾 " + backup.status_line(), "", "<i>/users text · /setplan who plan [date] · /pauseuser who · /resumeuser who</i>"]
    return "\n".join(lines)


def apply_plan(who: str, plan: str, until: str = "") -> tuple[bool, str, Optional[accounts.Account]]:
    """Validate and give a plan. Returns (ok, message for the maintainer, the account)."""
    acct = find(who)
    if acct is None:
        return False, f"I can't find “{who}”. Use a user id or a Telegram id (see /users).", None
    plan = (plan or "").strip().lower()
    if plan not in plans.PLAN_NAMES:
        return False, f"The plan must be one of: {', '.join(plans.PLAN_NAMES)}.", acct
    if until:
        try:
            end = dt.date.fromisoformat(until)
        except ValueError:
            return False, "The end date should look like 2027-01-31.", acct
        if plan != plans.PAID:
            return False, "Only the paid plan has an end date. Leave it out for free and unlimited.", acct
        if end < dt.date.today():
            return False, "That end date has already passed.", acct
    elif plan == plans.PAID:
        return False, "A paid plan needs an end date, for example: /setplan <who> paid 2027-01-31.", acct
    accounts.set_plan(acct.id, plan, until)
    return True, f"✅ {_h(acct.name) or acct.id} is now on the {plan} plan" + (f" until {until}" if until else "") + ".", accounts.get(acct.id)


async def _tell(context, acct: accounts.Account, text: str) -> bool:
    """Send the person a message in their own chat (the one remembered for them)."""
    with users.use(acct.id):
        chat_id = watchlist.owner_chat_id()
    if chat_id is None:
        return False
    try:
        await context.bot.send_message(chat_id, text)
        return True
    except Exception as err:
        logger.info("Couldn't tell %s: %s", acct.id, err)
        return False


@admin_only
async def adminweb_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/adminweb: a one-time link that signs this browser in to the read-only admin page."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    from bot.services import websession
    if not config.PUBLIC_URL:
        await update.effective_message.reply_text("Set PUBLIC_URL in .env first: the web pages need your site's address.")
        return
    token = await asyncio.to_thread(websession.new_login, websession.ADMIN, "admin")
    await update.effective_message.reply_text("🛠 Admin page. The link works once and for 15 minutes.", reply_markup=InlineKeyboardMarkup(
        [[InlineKeyboardButton("Open admin", url=f"{config.PUBLIC_URL}/auth/{token}")]]))


@admin_only
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(await asyncio.to_thread(summary_text), parse_mode="HTML")


@admin_only
async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    found = await asyncio.to_thread(search, " ".join(context.args or []))
    lines = [f"• <code>{a.id}</code> · tg {a.telegram_id} · {_h(a.name) or '(no name)'} · {a.plan}" + (" · paused" if a.status == accounts.PAUSED else "")
             for a in found] or ["Nobody found."]
    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")


@admin_only
async def setplan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args or []
    if len(args) < 2:
        await update.effective_message.reply_text("Use: /setplan <user id or Telegram id> <free|paid|unlimited> [end date 2027-01-31]")
        return
    ok, message, acct = await asyncio.to_thread(apply_plan, args[0], args[1], args[2] if len(args) > 2 else "")
    await update.effective_message.reply_text(message, parse_mode="HTML")
    if ok and acct is not None:
        lim = plans.limits_for(acct)
        told = await _tell(context, acct, f"💳 Your plan is now {lim.name}" + (f" until {acct.plan_until}" if acct.plan_until else "")
                           + ". Send /plan to see what it includes.")
        if not told:
            await update.effective_message.reply_text("(I couldn't message them, probably because they haven't written to the bot lately.)")


async def _set_status(update, context, status: str, note: Optional[str] = None):
    args = context.args or []
    acct = find(args[0]) if args else None
    if acct is None:
        await update.effective_message.reply_text("Use: /pauseuser <who> [note]  or  /resumeuser <who>  (a user id or a Telegram id).")
        return
    await asyncio.to_thread(accounts.set_status, acct.id, status, note)
    await update.effective_message.reply_text(f"✅ {_h(acct.name) or acct.id} is {'paused' if status == accounts.PAUSED else 'active again'}.", parse_mode="HTML")


@admin_only
async def pause_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _set_status(update, context, accounts.PAUSED, " ".join((context.args or [])[1:]) or None)


@admin_only
async def resume_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _set_status(update, context, accounts.ACTIVE)


# ---------------------------------------------------------------- the web page (read-only)

def _backup_line() -> str:
    from bot import backup
    return backup.status_line()


def dashboard_data() -> dict:
    """Everything the /admin web page shows, as plain data."""
    month = plans.month_key()
    with db._get_connection() as conn:
        accts = [accounts._row(r) for r in conn.execute("SELECT * FROM accounts WHERE status != 'deleted' ORDER BY rowid DESC LIMIT 500").fetchall()]
        used: dict[tuple[str, str], int] = {(r[0], r[1]): r[2] for r in conn.execute("SELECT user_id, kind, n FROM usage WHERE month = ?", (month,)).fetchall()}
    by_plan: dict[str, int] = {}
    for a in accts:
        by_plan[a.plan] = by_plan.get(a.plan, 0) + 1
    cap = config.AI_MONTHLY_BUDGET_CALLS
    ai = plans.server_total("ai")
    return {
        "total": len(accts), "paused": sum(1 for a in accts if a.status == accounts.PAUSED), "by_plan": sorted(by_plan.items()),
        "resumes": plans.server_total("resume"), "ai": ai, "scrape": plans.server_total("scrape"), "cap": cap, "backup": _backup_line(),
        "percent": round(100 * ai / cap) if cap else 0,
        "users": [{"id": a.id, "name": a.name, "plan": a.plan, "until": a.plan_until, "status": a.status, "joined": a.created_at[:10],
                   "last_active": (a.last_active or "")[:16] or "never", "resumes": used.get((a.id, "resume"), 0), "ai": used.get((a.id, "ai"), 0)} for a in accts],
    }
