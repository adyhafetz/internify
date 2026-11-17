"""Signing in to the web pages (hosted mode) without a password.

In Telegram you send /dashboard. The bot gives you a link with a one-time token. Opening the link shows a button; pressing it
trades the token for a browser session (a cookie). Why a button and not straight in: Telegram and other apps fetch links in
messages to make a preview, and that must not use the token up.

Only hashes of tokens and sessions are stored, so a copy of the database can't be used to sign in. A token works once and for
LOGIN_TTL seconds; a session lasts SESSION_TTL. Deleting or pausing an account ends its sessions at once."""
from __future__ import annotations

import hashlib
import secrets
import time
from typing import Optional

from bot.services import accounts, db

LOGIN_TTL = 15 * 60
SESSION_TTL = 30 * 24 * 3600
ADMIN = "admin"      # the "user id" of the maintainer's web session (never a valid account id)


def _hash(raw: str) -> str:
    return hashlib.sha256(("internify-web:" + raw).encode()).hexdigest()


def new_login(user_id: str, role: str = "user", now: Optional[float] = None) -> str:
    """A one-time sign-in token for this person (or for the maintainer's admin page). Returns the raw token to put in a link."""
    if role not in ("user", "admin") or (role == "admin") != (user_id == ADMIN):
        raise ValueError("role and user id don't match")
    raw = secrets.token_urlsafe(32)
    with db._get_connection() as conn:
        conn.execute("DELETE FROM web_tokens WHERE expires_at < ?", ((now or time.time()),))   # tidy up old ones
        conn.execute("INSERT INTO web_tokens (token_hash, user_id, role, expires_at) VALUES (?, ?, ?, ?)",
                     (_hash(raw), user_id, role, (now or time.time()) + LOGIN_TTL))
        conn.commit()
    return raw


def peek(raw: str, now: Optional[float] = None) -> bool:
    """Is this token still good (not used, not expired)? Changes nothing."""
    with db._get_connection() as conn:
        r = conn.execute("SELECT 1 FROM web_tokens WHERE token_hash = ? AND used = 0 AND expires_at >= ?", (_hash(raw or ""), now or time.time())).fetchone()
        return r is not None


def sign_in(raw: str, now: Optional[float] = None) -> Optional[tuple[str, str, str]]:
    """Use the token (once) and start a session. Returns (session cookie value, user id, role), or None if the token is
    unknown, used or expired. The token is used up in one statement, so two tries at once can't both succeed."""
    now = now or time.time()
    with db._get_connection() as conn:
        cur = conn.execute("UPDATE web_tokens SET used = 1 WHERE token_hash = ? AND used = 0 AND expires_at >= ?", (_hash(raw or ""), now))
        if cur.rowcount != 1:
            return None
        user_id, role = conn.execute("SELECT user_id, role FROM web_tokens WHERE token_hash = ?", (_hash(raw),)).fetchone()
        if role == "user":
            acct = accounts.get(user_id)
            if acct is None or acct.status != accounts.ACTIVE:
                conn.commit()
                return None
        cookie = secrets.token_urlsafe(32)
        conn.execute("INSERT INTO web_sessions (session_hash, user_id, role, expires_at) VALUES (?, ?, ?, ?)",
                     (_hash(cookie), user_id, role, now + SESSION_TTL))
        conn.commit()
    return cookie, user_id, role


def session(cookie: str, now: Optional[float] = None) -> Optional[tuple[str, str]]:
    """Who this browser is signed in as: (user id, role), or None. A paused or deleted account is signed out."""
    if not cookie:
        return None
    with db._get_connection() as conn:
        r = conn.execute("SELECT user_id, role FROM web_sessions WHERE session_hash = ? AND expires_at >= ?", (_hash(cookie), now or time.time())).fetchone()
    if r is None:
        return None
    user_id, role = r
    if role == "user":
        acct = accounts.get(user_id)
        if acct is None or acct.status != accounts.ACTIVE:
            return None
    return user_id, role


def sign_out(cookie: str) -> None:
    with db._get_connection() as conn:
        conn.execute("DELETE FROM web_sessions WHERE session_hash = ?", (_hash(cookie or ""),))
        conn.commit()


def sign_out_everywhere(user_id: str) -> int:
    with db._get_connection() as conn:
        n = conn.execute("DELETE FROM web_sessions WHERE user_id = ?", (user_id,)).rowcount
        conn.commit()
        return n
