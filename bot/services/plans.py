"""Plans, monthly allowance and limits for the hosted version (MODE=cloud). In single mode there are no limits: the owner has
their own keys and pays for their own AI, so every question here answers "go ahead".

  * free:       FREE_RESUMES a month, no daily check.
  * paid:       PAID_RESUMES a month, the daily check (it writes at most PAID_SCAN_MAX resumes a time, and they count toward the month).
  * unlimited:  no allowance. Only the maintainer can give this plan (for themselves, a friend, anyone they choose).
A paid plan with an end date that has passed counts as free from that day (the account keeps the plan name until the maintainer
changes it, so a renewal is just a new end date).

A resume is *reserved* before the work starts and *given back* if it fails, so a failed or cancelled run never costs anything and
a person at the limit is told before any work is done."""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from typing import Optional

from bot import config
from bot.services import accounts, db
from service import metering, users

logger = logging.getLogger(__name__)

FREE, PAID, UNLIMITED = "free", "paid", "unlimited"
PLAN_NAMES = (FREE, PAID, UNLIMITED)


@dataclass
class Limits:
    plan: str                 # the plan that counts right now (a lapsed paid plan counts as free)
    name: str
    resumes: Optional[int]    # resumes a month, None = no limit
    daily_check: bool
    scan_max: int             # most resumes one daily check may write
    lapsed: bool = False      # a paid plan whose end date has passed


def month_key(today: Optional[dt.date] = None) -> str:
    return (today or dt.date.today()).strftime("%Y-%m")


def next_reset(today: Optional[dt.date] = None) -> dt.date:
    t = today or dt.date.today()
    return (t.replace(day=1) + dt.timedelta(days=32)).replace(day=1)


def limits_for(acct: accounts.Account, today: Optional[dt.date] = None) -> Limits:
    today = today or dt.date.today()
    plan = acct.plan if acct.plan in PLAN_NAMES else FREE
    if plan == PAID and acct.plan_until:
        try:
            if dt.date.fromisoformat(acct.plan_until) < today:
                return Limits(FREE, "Free", config.FREE_RESUMES, False, 0, lapsed=True)
        except ValueError:
            pass
    if plan == UNLIMITED:
        return Limits(UNLIMITED, "Unlimited", None, True, config.PAID_SCAN_MAX)
    if plan == PAID:
        return Limits(PAID, "Paid", config.PAID_RESUMES, True, config.PAID_SCAN_MAX)
    return Limits(FREE, "Free", config.FREE_RESUMES, False, 0)


def current_limits() -> Optional[Limits]:
    """The acting user's limits, or None when there are none (single mode)."""
    if not users.cloud():
        return None
    acct = accounts.get(users.current())
    return limits_for(acct) if acct else None


# ---------------------------------------------------------------- counting

def used(kind: str, user_id: Optional[str] = None, month: Optional[str] = None) -> int:
    with db._get_connection() as conn:
        r = conn.execute("SELECT n FROM usage WHERE user_id = ? AND month = ? AND kind = ?",
                         (user_id or users.current(), month or month_key(), kind)).fetchone()
        return r[0] if r else 0


def _bump(conn, user_id: str, kind: str, delta: int, month: str, ceiling: Optional[int] = None) -> bool:
    """Add to a counter. With a ceiling, only if it stays at or under it (all in one statement, so two things at once can't both
    take the last one). Returns whether it was added."""
    conn.execute("INSERT OR IGNORE INTO usage (user_id, month, kind, n) VALUES (?, ?, ?, 0)", (user_id, month, kind))
    if ceiling is None:
        cur = conn.execute("UPDATE usage SET n = MAX(0, n + ?) WHERE user_id = ? AND month = ? AND kind = ?", (delta, user_id, month, kind))
    else:
        cur = conn.execute("UPDATE usage SET n = n + ? WHERE user_id = ? AND month = ? AND kind = ? AND n + ? <= ?",
                           (delta, user_id, month, kind, delta, ceiling))
    conn.commit()
    return cur.rowcount > 0


def record(kind: str) -> None:
    """Count one AI call or paid scraping call for the acting user (installed as the metering sink in cloud mode)."""
    if not users.cloud():
        return
    try:
        uid = users.current()
    except users.NoUserError:
        return          # a call made for the server itself, not for a person
    with db._get_connection() as conn:
        _bump(conn, uid, kind, 1, month_key())


def server_total(kind: str = "ai", month: Optional[str] = None) -> int:
    with db._get_connection() as conn:
        r = conn.execute("SELECT COALESCE(SUM(n), 0) FROM usage WHERE month = ? AND kind = ?", (month or month_key(), kind)).fetchone()
        return int(r[0])


# ---------------------------------------------------------------- asking and reserving

@dataclass
class Decision:
    ok: bool
    message: str = ""         # plain words for the person when ok is False
    reserved: bool = False    # a resume was taken from the month: give it back (refund) if the work fails


def _upgrade_note() -> str:
    return f"\n\n{config.PAY_INSTRUCTIONS}"


def capacity() -> str:
    """"" if the server has room, "free" if only paid people may start new work, "all" if it is out of room this month. The ceiling is
    AI_MONTHLY_BUDGET_CALLS (0 = none): at 90% free-tier resumes wait, at 100% everyone but unlimited waits."""
    cap = config.AI_MONTHLY_BUDGET_CALLS
    if cap <= 0:
        return ""
    total = server_total("ai")
    return "all" if total >= cap else "free" if total >= cap * 0.9 else ""


def reserve_resume() -> Decision:
    """Before writing a resume: is there allowance (and room on the server)? Takes one from the month if so."""
    lim = current_limits()
    if lim is None:
        return Decision(True)
    month, uid = month_key(), users.current()
    room = capacity()
    if lim.plan != UNLIMITED and (room == "all" or (room == "free" and lim.plan == FREE)):
        return Decision(False, "⏳ Internify is very busy this month and is holding back new resumes for a while. Please try again in a few days.")
    with db._get_connection() as conn:
        ok = _bump(conn, uid, "resume", 1, month, lim.resumes)
    if ok:
        return Decision(True, reserved=True)
    if lim.lapsed:
        why = f"Your paid plan ended on {accounts.get(uid).plan_until}, so you're back on the free plan, which has {lim.resumes} resumes a month and you've used them."
    else:
        why = f"You've used all {lim.resumes} resumes of your {lim.name} plan this month."
    return Decision(False, f"📭 {why} The count starts again on {next_reset().strftime('%d %b')}.{_upgrade_note()}")


def refund_resume() -> None:
    """Give back a reserved resume (the work failed or was cancelled)."""
    if not users.cloud():
        return
    with db._get_connection() as conn:
        _bump(conn, users.current(), "resume", -1, month_key())


def resumes_left() -> Optional[int]:
    """Resumes the acting user may still write this month: None = no limit (single mode and the unlimited plan)."""
    lim = current_limits()
    if lim is None or lim.resumes is None:
        return None
    return max(0, lim.resumes - used("resume"))


def allows_daily_check() -> bool:
    lim = current_limits()
    return True if lim is None else lim.daily_check


def daily_check_refusal() -> str:
    """What to tell someone whose plan has no daily check and who tapped Check now."""
    lim = current_limits()
    if lim is None or lim.daily_check:
        return ""
    return "🔭 The daily job check is part of the paid plan. On the free plan you can still send me any job link and I'll write the resume." + _upgrade_note()


def scan_budget() -> Optional[int]:
    """The most resumes the next daily check may write: the plan's per-check maximum, and no more than are left this month.
    None = no limit."""
    lim = current_limits()
    if lim is None:
        return None
    left = resumes_left()
    return min(lim.scan_max, left) if left is not None else lim.scan_max


def short_summary() -> str:
    """One line for ⚙️ Status: the plan and this month's resumes. "" in single mode."""
    lim = current_limits()
    if lim is None:
        return ""
    n = used("resume")
    return f"{lim.name} plan · " + (f"{n} of {lim.resumes} resumes used this month" if lim.resumes is not None else f"{n} resumes this month (no limit)")


def install() -> None:
    """Start counting AI and scraping calls (cloud mode)."""
    metering.sink = record
