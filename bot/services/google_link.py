"""Each person's own Google connection in the hosted version (MODE=cloud).

The maintainer owns one Google app (a "Web application" client) that asks for only ``drive.file``: Internify can open just the
folder and the tracker sheet it creates, nothing else in the person's Drive. Those files are created **in the person's own Drive**,
so they use the person's storage, and they stay the person's when they disconnect or delete their account.

The flow: /profile in Telegram -> a one-time link (a button; opening it shows a button, so link previews can't use it up) -> Google's
sign-in -> back to /connect/google/callback -> the folder and sheet are created, the person's tracker rows are copied into the sheet,
and the login is saved **encrypted** with GOOGLE_TOKEN_KEY. Disconnecting (or deleting the account) revokes the login with Google.
In single mode none of this exists: your own login stays in .env as before."""
from __future__ import annotations

import hashlib
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Optional

import httpx

from bot import config
from bot.services import db
from service import users

logger = logging.getLogger(__name__)

STATE_TTL = 10 * 60
CALLBACK_PATH = "/connect/google/callback"
APP_SCOPES = ["https://www.googleapis.com/auth/drive.file"]
TELEGRAM_API = "https://api.telegram.org"


class GoogleLinkError(Exception):
    """Something the person can understand and, usually, retry. The message is plain language."""


# ---------------------------------------------------------------- is it set up?

def _fernet():
    from cryptography.fernet import Fernet
    return Fernet(config.GOOGLE_TOKEN_KEY.encode())


def enabled() -> bool:
    """Can people connect Google here? Hosted mode, the maintainer's Web client, a site address and an encryption key that works."""
    if not (users.cloud() and config.GOOGLE_CLIENT_ID and config.GOOGLE_CLIENT_SECRET and config.PUBLIC_URL and config.GOOGLE_TOKEN_KEY):
        return False
    try:
        _fernet()
    except Exception:
        return False
    return True


def encrypt(text: str) -> str:
    return _fernet().encrypt(text.encode()).decode()


def decrypt(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()


def redirect_uri() -> str:
    return config.PUBLIC_URL + CALLBACK_PATH


# ---------------------------------------------------------------- the stored link

@dataclass
class Link:
    user_id: str
    sheet_id: str
    sheet_tab: str
    folder_id: str
    connected_at: str

    @property
    def sheet_url(self) -> str:
        return f"https://docs.google.com/spreadsheets/d/{self.sheet_id}/edit" if self.sheet_id else ""

    @property
    def folder_url(self) -> str:
        return f"https://drive.google.com/drive/folders/{self.folder_id}" if self.folder_id else ""


def get(user_id: Optional[str] = None) -> Optional[Link]:
    with db._get_connection() as conn:
        r = conn.execute("SELECT user_id, sheet_id, sheet_tab, folder_id, connected_at FROM google_links WHERE user_id = ?",
                         (user_id or users.current(),)).fetchone()
    return Link(r[0], r[1] or "", r[2] or "Tracker", r[3] or "", str(r[4] or "")) if r else None


def refresh_token(user_id: Optional[str] = None) -> Optional[str]:
    """The person's saved login, decrypted. None if they haven't connected, or the key can't open it (the key changed: they must
    connect again)."""
    with db._get_connection() as conn:
        r = conn.execute("SELECT token_enc FROM google_links WHERE user_id = ?", (user_id or users.current(),)).fetchone()
    if not r:
        return None
    try:
        return decrypt(r[0])
    except Exception:
        logger.warning("A saved Google login could not be decrypted (was GOOGLE_TOKEN_KEY changed?)")
        return None


def save(user_id: str, token: str, sheet_id: str, folder_id: str, tab: str = "Tracker") -> None:
    with db._get_connection() as conn:
        conn.execute("INSERT INTO google_links (user_id, token_enc, sheet_id, sheet_tab, folder_id) VALUES (?, ?, ?, ?, ?) "
                     "ON CONFLICT(user_id) DO UPDATE SET token_enc = excluded.token_enc, sheet_id = excluded.sheet_id, "
                     "sheet_tab = excluded.sheet_tab, folder_id = excluded.folder_id, connected_at = CURRENT_TIMESTAMP",
                     (user_id, encrypt(token), sheet_id, tab, folder_id))
        conn.commit()


def credentials(user_id: Optional[str] = None):
    """Google credentials for this person (limited to the files Internify created), or None if they haven't connected."""
    token = refresh_token(user_id)
    if not token or not (config.GOOGLE_CLIENT_ID and config.GOOGLE_CLIENT_SECRET):
        return None
    from google.oauth2.credentials import Credentials
    return Credentials(token=None, refresh_token=token, token_uri="https://oauth2.googleapis.com/token",
                       client_id=config.GOOGLE_CLIENT_ID, client_secret=config.GOOGLE_CLIENT_SECRET, scopes=APP_SCOPES)


def forget_clients(user_id: str) -> None:
    """Drop the cached Drive and Sheets clients of this person (after a disconnect or a new login)."""
    from bot.services import drive, sheets
    sheets._clients.pop(user_id, None)
    drive._services.pop(user_id, None)


def disconnect(user_id: Optional[str] = None, revoke: bool = True) -> bool:
    """Forget the person's Google login and, best effort, revoke it with Google. Their folder and sheet stay in their own Drive.
    Returns whether there was a connection."""
    uid = user_id or users.current()
    token = refresh_token(uid)
    with db._get_connection() as conn:
        had = conn.execute("DELETE FROM google_links WHERE user_id = ?", (uid,)).rowcount > 0
        conn.execute("DELETE FROM google_states WHERE user_id = ?", (uid,))
        conn.commit()
    forget_clients(uid)
    if token and revoke:
        try:
            httpx.post("https://oauth2.googleapis.com/revoke", data={"token": token}, timeout=8.0)
        except Exception as err:   # Google being unreachable must never block a disconnect or a deletion
            logger.info("Could not revoke a Google login: %s", err)
    return had


# ---------------------------------------------------------------- the sign-in

def _hash(raw: str) -> str:
    return hashlib.sha256(("internify-google-state:" + raw).encode()).hexdigest()


def new_state(user_id: str, now: Optional[float] = None) -> str:
    raw = secrets.token_urlsafe(32)
    with db._get_connection() as conn:
        conn.execute("DELETE FROM google_states WHERE expires_at < ?", (now or time.time(),))
        conn.execute("INSERT INTO google_states (state_hash, user_id, expires_at) VALUES (?, ?, ?)", (_hash(raw), user_id, (now or time.time()) + STATE_TTL))
        conn.commit()
    return raw


def use_state(raw: str, now: Optional[float] = None) -> Optional[str]:
    """Use up the "state" Google sent back and return the person it was made for (None if unknown, used or expired)."""
    with db._get_connection() as conn:
        cur = conn.execute("UPDATE google_states SET used = 1 WHERE state_hash = ? AND used = 0 AND expires_at >= ?", (_hash(raw or ""), now or time.time()))
        if cur.rowcount != 1:
            return None
        row = conn.execute("SELECT user_id FROM google_states WHERE state_hash = ?", (_hash(raw),)).fetchone()
        conn.commit()
    return row[0] if row else None


def make_flow(state: Optional[str] = None):
    """Google's sign-in for the maintainer's Web client, coming back to the callback address."""
    from google_auth_oauthlib.flow import Flow
    cfg = {"web": {"client_id": config.GOOGLE_CLIENT_ID, "client_secret": config.GOOGLE_CLIENT_SECRET, "redirect_uris": [redirect_uri()],
                   "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token"}}
    return Flow.from_client_config(cfg, scopes=APP_SCOPES, redirect_uri=redirect_uri(), state=state)


def authorization_url(user_id: str) -> str:
    """Where to send the person to sign in to Google (asks for a lasting login and only the limited permission)."""
    state = new_state(user_id)
    url, _ = make_flow(state).authorization_url(access_type="offline", prompt="consent", include_granted_scopes="false")
    return url


def tracker_rows(apps: list[dict]) -> list[list[str]]:
    """The person's tracker as rows of the template's columns (what the new sheet starts with)."""
    from bot.services import sheet_layout
    rows = []
    for a in apps:
        status = a.get("status") or ""
        values = {"Company": a.get("company"), "Job Title": a.get("job_title"), "Location": a.get("location"),
                  "Job Description": (a.get("job_description") or "")[:500], "Job Requirements": (a.get("job_requirements") or "")[:500],
                  "Job Link": a.get("job_link"), "Resume Link": a.get("drive_link"), "Resume Status": a.get("resume_status"),
                  "Comment": a.get("comment"), "Status": status if status in sheet_layout.STATUSES else "New"}
        rows.append(["" if values[h] is None else str(values[h]) for h in sheet_layout.HEADERS])
    return rows


def _usable(creds, sheet_id: str, folder_id: str) -> bool:
    """After a reconnect: can the files from the earlier connection still be opened? (Then they are kept, not made again.)"""
    if not (sheet_id and folder_id):
        return False
    try:
        from googleapiclient.discovery import build
        drive = build("drive", "v3", credentials=creds, cache_discovery=False)
        for fid in (sheet_id, folder_id):
            if drive.files().get(fileId=fid, fields="id,trashed", supportsAllDrives=True).execute().get("trashed"):
                return False
        return True
    except Exception:
        return False


@dataclass
class Connected:
    user_id: str
    sheet_url: str
    folder_url: str
    copied: int
    reused: bool


def finish(code: str, state: str) -> Connected:
    """The person came back from Google. Check who they are (the one-time state), trade the code for a login, create their folder and
    sheet in their own Drive (or keep the earlier ones if they still open), copy their tracker into the new sheet, and save the login
    encrypted. Blocking: call it from a worker thread. Raises GoogleLinkError with a plain message."""
    uid = use_state(state)
    if uid is None:
        raise GoogleLinkError("This sign-in link is not valid any more. Open Telegram, go to /profile and tap Connect Google again.")
    try:
        flow = make_flow(state)
        flow.fetch_token(code=code)
        creds = flow.credentials
    except Exception as err:
        logger.info("Google sign-in did not complete: %s", err)
        raise GoogleLinkError("Google did not accept the sign-in. Try again from /profile in Telegram.")
    if not getattr(creds, "refresh_token", None):
        raise GoogleLinkError("Google did not give a lasting login. Remove Internify at myaccount.google.com/permissions, then connect again from /profile.")
    from bot.setup import google_setup
    with users.use(uid):
        old = get(uid)
        reused = bool(old and _usable(creds, old.sheet_id, old.folder_id))
        copied = 0
        try:
            if reused:
                sheet_id, folder_id, tab = old.sheet_id, old.folder_id, old.sheet_tab
            else:
                ids = google_setup.create_tracker(creds)
                sheet_id, folder_id, tab = ids["GOOGLE_SHEET_ID"], ids["GOOGLE_DRIVE_FOLDER_ID"], ids["GOOGLE_SHEET_NAME"]
                apps = db.all_applications()
                if apps:
                    copied = google_setup.write_rows(creds, sheet_id, tracker_rows(apps))
        except Exception as err:
            logger.warning("Creating the Google folder and sheet failed: %s", err)
            raise GoogleLinkError("I couldn't create your folder and sheet in Google Drive. Nothing was saved; please try again from /profile in a few minutes.")
        save(uid, creds.refresh_token, sheet_id, folder_id, tab)
        forget_clients(uid)
    return Connected(uid, f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit", f"https://drive.google.com/drive/folders/{folder_id}", copied, reused)


def tell(user_id: str, text: str) -> bool:
    """A message in the person's own Telegram chat (from the web server, which has no bot object of its own)."""
    from bot.services import watchlist
    with users.use(user_id):
        chat_id = watchlist.owner_chat_id()
    if chat_id is None or not config.TELEGRAM_BOT_TOKEN:
        return False
    try:
        r = httpx.post(f"{TELEGRAM_API}/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage", data={"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"}, timeout=10.0)
        return r.status_code == 200
    except Exception as err:
        logger.info("Couldn't message %s: %s", user_id, err)
        return False
