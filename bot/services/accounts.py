"""The people who use a hosted Internify (MODE=cloud): who they are, their plan and status, and deleting an account.

An account ties a Telegram user to a random user id (service/users.py). In single mode there are no accounts: the one user
is the owner. Accounts are created only when someone sends /start, so a stranger sending any other message creates nothing."""
from __future__ import annotations

import logging
import secrets
import shutil
from dataclasses import dataclass
from typing import Optional

from bot.services import db
from service import users

logger = logging.getLogger(__name__)

ACTIVE, PAUSED, DELETED = "active", "paused", "deleted"
_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"
# Tables with rows that belong to one user: everything of theirs goes when the account is deleted
_USER_TABLES = ("applications", "watch_sources", "seen_jobs", "settings")


@dataclass
class Account:
    id: str
    telegram_id: Optional[int]
    name: str
    plan: str
    plan_until: str
    status: str
    note: str
    created_at: str
    last_active: str

    @property
    def active(self) -> bool:
        return self.status == ACTIVE


def _row(r) -> Account:
    return Account(r["id"], r["telegram_id"], r["name"] or "", r["plan"], r["plan_until"] or "", r["status"], r["note"] or "",
                   str(r["created_at"] or ""), str(r["last_active"] or ""))


def new_id() -> str:
    """A random user id nobody can guess (10 lower-case letters and digits, about 50 bits)."""
    with db._get_connection() as conn:
        while True:
            uid = "".join(secrets.choice(_ALPHABET) for _ in range(10))
            if users._ID.match(uid) and not conn.execute("SELECT 1 FROM accounts WHERE id = ?", (uid,)).fetchone():
                return uid


def get(user_id: str) -> Optional[Account]:
    with db._get_connection() as conn:
        r = conn.execute("SELECT * FROM accounts WHERE id = ?", (user_id,)).fetchone()
        return _row(r) if r else None


def by_telegram(telegram_id: int) -> Optional[Account]:
    """The account of this Telegram user, if they have one (a deleted account has no Telegram id any more)."""
    with db._get_connection() as conn:
        r = conn.execute("SELECT * FROM accounts WHERE telegram_id = ?", (telegram_id,)).fetchone()
        return _row(r) if r else None


def create(telegram_id: int, name: str = "") -> Account:
    """Sign someone up (or return the account they already have). New accounts start on the free plan."""
    existing = by_telegram(telegram_id)
    if existing:
        return existing
    uid = new_id()
    with db._get_connection() as conn:
        conn.execute("INSERT INTO accounts (id, telegram_id, name) VALUES (?, ?, ?)", (uid, telegram_id, (name or "")[:80]))
        conn.commit()
    logger.info("New account %s", uid)
    return get(uid)


def touch(user_id: str) -> None:
    with db._get_connection() as conn:
        conn.execute("UPDATE accounts SET last_active = CURRENT_TIMESTAMP WHERE id = ?", (user_id,))
        conn.commit()


def set_status(user_id: str, status: str, note: Optional[str] = None) -> bool:
    if status not in (ACTIVE, PAUSED):
        raise ValueError("status must be active or paused (use delete_account to delete)")
    with db._get_connection() as conn:
        cur = conn.execute("UPDATE accounts SET status = ?, note = COALESCE(?, note) WHERE id = ? AND status != 'deleted'",
                           (status, note, user_id))
        conn.commit()
        return cur.rowcount > 0


def set_plan(user_id: str, plan: str, until: str = "") -> bool:
    with db._get_connection() as conn:
        cur = conn.execute("UPDATE accounts SET plan = ?, plan_until = ? WHERE id = ? AND status != 'deleted'", (plan, until, user_id))
        conn.commit()
        return cur.rowcount > 0


def active() -> list[Account]:
    with db._get_connection() as conn:
        return [_row(r) for r in conn.execute("SELECT * FROM accounts WHERE status = 'active' ORDER BY rowid").fetchall()]


def user_ids() -> list[str]:
    """Who the scheduled jobs (the daily check, reminders, the weekly tidy-up) run for: the owner in single mode,
    every active account in cloud mode."""
    return [users.OWNER] if not users.cloud() else [a.id for a in active()]


def delete_account(user_id: str) -> dict[str, int]:
    """Delete everything of this user: their rows, their folder (saved experience, PDFs, backups, exports) and the link to
    their Telegram. The account row stays as a bare record (no name, no Telegram id) so the id is never used again.
    Returns how many rows were removed from each table."""
    if not users.cloud() or not users._ID.match(user_id or ""):
        raise ValueError("accounts only exist in cloud mode")
    removed: dict[str, int] = {}
    with db._get_connection() as conn:
        for table in _USER_TABLES:
            removed[table] = conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,)).rowcount
        conn.execute("UPDATE accounts SET status = 'deleted', telegram_id = NULL, name = '', note = '', plan_until = '', "
                     "deleted_at = CURRENT_TIMESTAMP WHERE id = ?", (user_id,))
        conn.commit()
    folder = users.user_root(user_id)
    shutil.rmtree(folder, ignore_errors=True)
    # Google login and Sheet/Drive: disconnected here once Connect Google exists (B5); the user's own files stay theirs
    logger.info("Deleted account %s", user_id)
    return removed


def for_each_user(job):
    """Decorator for a scheduled job (the daily check, reminders, the weekly tidy-up): run it once for each user, as that
    user. In single mode that is exactly once, as the owner. One user's job failing never stops the others."""
    import functools

    @functools.wraps(job)
    async def wrapper(*args, **kwargs):
        for uid in user_ids():
            try:
                with users.use(uid):
                    await job(*args, **kwargs)
            except Exception:
                logger.exception("Scheduled job %s failed for user %s", job.__name__, uid)
    return wrapper
