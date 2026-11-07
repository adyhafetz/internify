"""Where the daily job check looks, what it has already seen, and its settings.

A source is a career page or a job board. Job boards on Greenhouse or Lever are recognised from the link and read
through their public job-listing APIs (structured data, no scraping). Anything else is a "page": the Discoverer reads
it and picks out the job links. Sources are also found automatically from the jobs you have already sent."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

from bot.services import db as _db_module
from bot.services.db import application_links

_ready = False


def _get_connection():
    """A connection with the scan's tables guaranteed to exist (created on first use, so startup order never matters)."""
    global _ready
    if not _ready:
        _db_module.init_db()
        _ready = True
    return _db_module._get_connection()


GREENHOUSE_HOSTS = ("boards.greenhouse.io", "job-boards.greenhouse.io", "boards.eu.greenhouse.io", "job-boards.eu.greenhouse.io")
LEVER_HOSTS = ("jobs.lever.co", "jobs.eu.lever.co")


@dataclass(frozen=True)
class Source:
    id: int
    url: str
    kind: str      # greenhouse | lever | page
    ident: str
    label: str
    auto: bool
    last_status: str = ""


class SourceError(ValueError):
    """Something the user can fix. The message is plain language."""


def classify(url: str) -> tuple[str, str, str]:
    """(kind, ident, label) for a link. Boards on Greenhouse / Lever are recognised; anything else is a page."""
    u = urlparse(url)
    host = (u.netloc or "").lower()
    first = next((seg for seg in u.path.split("/") if seg), "")
    if host in GREENHOUSE_HOSTS and first:
        return "greenhouse", first, first.replace("-", " ").replace("_", " ").title()
    if host in LEVER_HOSTS and first:
        return "lever", first, first.replace("-", " ").replace("_", " ").title()
    return "page", "", host.removeprefix("www.") or url


def _clean(url: str) -> str:
    url = (url or "").strip()
    if url and not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    u = urlparse(url)
    if not u.netloc or "." not in u.netloc:
        raise SourceError("That doesn't look like a web address. Send a career page or job-board link, for example https://boards.greenhouse.io/acme")
    return url.rstrip("/")


def _row(r) -> Source:
    return Source(r["id"], r["url"], r["kind"], r["ident"], r["label"], bool(r["auto"]), r["last_status"] or "")


def add_source(url: str, auto: bool = False) -> Source:
    url = _clean(url)
    kind, ident, label = classify(url)
    if kind in ("greenhouse", "lever"):
        url = f"https://boards.greenhouse.io/{ident}" if kind == "greenhouse" else f"https://jobs.lever.co/{ident}"
    with _get_connection() as conn:
        existing = conn.execute("SELECT * FROM watch_sources WHERE url = ?", (url,)).fetchone()
        if existing:
            if existing["auto"] and not auto:   # you added it yourself: it's yours now, not an automatic one
                conn.execute("UPDATE watch_sources SET auto = 0 WHERE id = ?", (existing["id"],)); conn.commit()
            return _row(conn.execute("SELECT * FROM watch_sources WHERE id = ?", (existing["id"],)).fetchone())
        cur = conn.execute("INSERT INTO watch_sources (url, kind, ident, label, auto) VALUES (?, ?, ?, ?, ?)",
                           (url, kind, ident, label, 1 if auto else 0))
        conn.commit()
        return _row(conn.execute("SELECT * FROM watch_sources WHERE id = ?", (cur.lastrowid,)).fetchone())


def list_sources() -> list[Source]:
    with _get_connection() as conn:
        return [_row(r) for r in conn.execute("SELECT * FROM watch_sources ORDER BY auto, id").fetchall()]


def remove_source(source_id: int) -> bool:
    with _get_connection() as conn:
        cur = conn.execute("DELETE FROM watch_sources WHERE id = ?", (source_id,)); conn.commit()
        return cur.rowcount > 0


def mark_checked(source_id: int, status: str) -> None:
    with _get_connection() as conn:
        conn.execute("UPDATE watch_sources SET last_checked = CURRENT_TIMESTAMP, last_status = ? WHERE id = ?", (status[:200], source_id))
        conn.commit()


def sources_from_history() -> int:
    """Add the job boards behind the jobs you have already sent, so the scan starts with companies you like.
    Returns how many were new."""
    before = {s.url for s in list_sources()}
    found: set[str] = set()
    for link in application_links():
        kind, ident, _ = classify(link)
        if kind in ("greenhouse", "lever"):
            found.add(link)
    added = 0
    for link in sorted(found):
        try:
            src = add_source(link, auto=True)
            added += src.url not in before
            before.add(src.url)
        except SourceError:
            continue
    return added


# ---------------------------------------------------------------- jobs already seen

def seen(url: str) -> bool:
    with _get_connection() as conn:
        return conn.execute("SELECT 1 FROM seen_jobs WHERE url = ?", (url,)).fetchone() is not None


def mark_seen(url: str, title: str = "", company: str = "", status: str = "new", fit: float = 0.0, reason: str = "") -> None:
    with _get_connection() as conn:
        conn.execute(
            """INSERT INTO seen_jobs (url, title, company, status, fit, reason) VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(url) DO UPDATE SET title = excluded.title, company = excluded.company, status = excluded.status,
                                              fit = excluded.fit, reason = excluded.reason""",
            (url, title[:200], company[:120], status, fit, reason[:300]))
        conn.commit()


# ---------------------------------------------------------------- settings

def get_setting(key: str, default: str = "") -> str:
    with _get_connection() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with _get_connection() as conn:
        conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
        conn.commit()


def scan_paused() -> bool:
    return get_setting("scan_paused", "0") == "1"


def owner_chat_id() -> Optional[int]:
    v = get_setting("owner_chat_id", "")
    return int(v) if v.lstrip("-").isdigit() else None
