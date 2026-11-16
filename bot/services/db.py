from __future__ import annotations
import json
import os
import re
import sqlite3
import logging
from typing import Optional

from service import users

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DATABASE_PATH", "output/internify.db")


def _get_connection() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _uid() -> str:
    """The user whose rows these queries are about (the owner in single mode; in cloud mode, whoever is set)."""
    return users.current()


# Every table has a user_id, and what must be unique is unique per user: two people may apply to the same job link, watch
# the same page or save a setting with the same name. A copy from before this (single user) keeps all its rows as the
# owner's: see _add_user_ids.
_SCHEMA = {
    "applications": """
        CREATE TABLE IF NOT EXISTS applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL DEFAULT 'owner',
            company TEXT NOT NULL,
            job_title TEXT NOT NULL,
            location TEXT DEFAULT '',
            job_description TEXT DEFAULT '',
            job_requirements TEXT DEFAULT '',
            job_link TEXT,
            status TEXT DEFAULT 'New',
            score REAL DEFAULT 0.0,
            resume_status TEXT DEFAULT '',
            pdf_path TEXT DEFAULT '',
            drive_link TEXT DEFAULT '',
            comment TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            applied_at TIMESTAMP,
            status_at TIMESTAMP,
            followup_at TIMESTAMP,
            deadline TEXT DEFAULT '',
            deadline_reminded INTEGER DEFAULT 0,
            facts_used TEXT DEFAULT '',
            details TEXT DEFAULT '',
            gaps TEXT DEFAULT '',
            UNIQUE (user_id, job_link)
        )""",
    # The daily job check: where to look, what it has already seen, and its settings
    "watch_sources": """
        CREATE TABLE IF NOT EXISTS watch_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL DEFAULT 'owner',
            url TEXT NOT NULL,
            kind TEXT NOT NULL,              -- greenhouse | lever | ashby | smartrecruiters | workable | page
            ident TEXT DEFAULT '',           -- board / company name for the job-board kinds
            label TEXT DEFAULT '',
            auto INTEGER DEFAULT 0,          -- 0 = you added it, 1 = from the jobs you sent, 2 = found by the web search
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_checked TIMESTAMP,
            last_status TEXT DEFAULT '',
            UNIQUE (user_id, url)
        )""",
    "seen_jobs": """
        CREATE TABLE IF NOT EXISTS seen_jobs (
            user_id TEXT NOT NULL DEFAULT 'owner',
            url TEXT NOT NULL,
            title TEXT DEFAULT '',
            company TEXT DEFAULT '',
            status TEXT DEFAULT 'new',       -- new | waiting | low-fit | shortlisted | tailored | failed | filtered | duplicate
            fit REAL DEFAULT 0.0,
            reason TEXT DEFAULT '',
            first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, url)
        )""",
    "settings": """
        CREATE TABLE IF NOT EXISTS settings (
            user_id TEXT NOT NULL DEFAULT 'owner',
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            PRIMARY KEY (user_id, key)
        )""",
}


# Tables about the users themselves (not rows that belong to one user, so they have no user_id and are never rebuilt)
_GLOBAL_SCHEMA = {
    "accounts": """
        CREATE TABLE IF NOT EXISTS accounts (
            id TEXT PRIMARY KEY,                       -- the random user id (users.valid)
            telegram_id INTEGER UNIQUE,                -- NULL once the account is deleted, so the person can sign up again
            name TEXT DEFAULT '',
            plan TEXT NOT NULL DEFAULT 'free',
            plan_until TEXT DEFAULT '',                -- YYYY-MM-DD, empty = no end date
            status TEXT NOT NULL DEFAULT 'active',     -- active | paused | deleted
            note TEXT DEFAULT '',                      -- the maintainer's own note (why paused, how they paid)
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_active TIMESTAMP,
            deleted_at TIMESTAMP
        )""",
    # What each person has used this month: resumes written, AI calls, paid scraping calls (hosted mode only)
    "usage": """
        CREATE TABLE IF NOT EXISTS usage (
            user_id TEXT NOT NULL,
            month TEXT NOT NULL,                       -- YYYY-MM
            kind TEXT NOT NULL,                        -- resume | ai | scrape
            n INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, month, kind)
        )""",
}


def init_db() -> None:
    """Initializes the SQLite schema if it does not already exist, and upgrades an older one in place (no rows are lost)."""
    with _get_connection() as conn:
        for sql in list(_SCHEMA.values()) + list(_GLOBAL_SCHEMA.values()):
            conn.execute(sql)
        _migrate(conn)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_apps_user_link ON applications(user_id, job_link)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_apps_user_status ON applications(user_id, status)")
        conn.commit()
    logger.info("Initialized SQLite database at %s", DB_PATH)


# Columns added after the first release: added in place on start-up, so an existing tracker keeps all its rows.
_NEW_COLUMNS = {
    "applied_at": "TIMESTAMP",            # when you marked it applied
    "status_at": "TIMESTAMP",             # when the status last changed
    "followup_at": "TIMESTAMP",           # when to ask "any reply yet?"
    "deadline": "TEXT DEFAULT ''",        # YYYY-MM-DD if the posting gave one
    "deadline_reminded": "INTEGER DEFAULT 0",
    "facts_used": "TEXT DEFAULT ''",      # JSON list of the bullet ids on the resume
    "details": "TEXT DEFAULT ''",         # JSON: what was picked, left out, covered and missing
    "gaps": "TEXT DEFAULT ''",            # JSON list of tools the job wants that you have no saved experience of
}

# Where an application can be: the same words as the Status drop-down in your Google Sheet. The bot itself uses
# Processing / Ready / Failed (kept here only; the Sheet shows "New" until you apply); the rest you set with the buttons.
NEW, APPLIED, IN_REVIEW, INTERVIEW, OFFER, REJECTED, WITHDRAWN = ("New", "Applied", "In Review", "Interviewing", "Offer",
                                                                  "Rejected", "Withdrawn")
SHEET_STATUSES = (NEW, APPLIED, IN_REVIEW, INTERVIEW, OFFER, REJECTED, WITHDRAWN)
WAITING = (APPLIED, IN_REVIEW)                   # sent, no answer yet: the follow-up clock runs
OPEN_STATUSES = ("Ready", APPLIED, IN_REVIEW, INTERVIEW)
USER_STATUSES = (APPLIED, IN_REVIEW, INTERVIEW, OFFER, REJECTED, WITHDRAWN)   # set by you; the bot never undoes them
_RENAMED = {"Interview": INTERVIEW, "Not applying": WITHDRAWN, "No reply": REJECTED}   # names used briefly before
FOLLOWUP_DAYS = int(os.getenv("FOLLOWUP_DAYS", "14"))


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def _add_user_ids(conn: sqlite3.Connection) -> None:
    """A database from before users existed: rebuild each table that has no user_id with it (every old row becomes the
    owner's, with the same ids), because uniqueness has to become per user and SQLite can't change that in place."""
    for table, sql in _SCHEMA.items():
        old_cols = _columns(conn, table)
        if "user_id" in old_cols:
            continue
        conn.execute(f"ALTER TABLE {table} RENAME TO {table}_old")
        conn.execute(sql)
        keep = [c for c in old_cols if c in _columns(conn, table)]
        names = ", ".join(keep)
        conn.execute(f"INSERT OR IGNORE INTO {table} ({names}) SELECT {names} FROM {table}_old")
        conn.execute(f"DROP TABLE {table}_old")
        logger.info("Upgraded table %s: rows kept as the owner's", table)


def _migrate(conn: sqlite3.Connection) -> None:
    _add_user_ids(conn)
    have = set(_columns(conn, "applications"))
    for col, kind in _NEW_COLUMNS.items():
        if col not in have:
            conn.execute(f"ALTER TABLE applications ADD COLUMN {col} {kind}")
    for old, new in _RENAMED.items():
        conn.execute("UPDATE applications SET status = ? WHERE status = ?", (new, old))


def is_duplicate_url(url: str) -> bool:
    """Checks if an application with the given job URL already exists in SQLite."""
    clean_url = url.strip().lower()
    with _get_connection() as conn:
        cur = conn.execute("SELECT id FROM applications WHERE user_id = ? AND LOWER(TRIM(job_link)) = ?", (_uid(), clean_url))
        return cur.fetchone() is not None


def upsert_application(
    company: str,
    job_title: str,
    location: str = "",
    job_description: str = "",
    job_requirements: str = "",
    job_link: str = "",
    status: str = "Processing",
) -> int:
    """Re-tailoring a link that is already tracked refreshes its row instead of violating the UNIQUE job_link."""
    clean_url = job_link.strip().lower()
    with _get_connection() as conn:
        row = conn.execute("SELECT id FROM applications WHERE user_id = ? AND LOWER(TRIM(job_link)) = ?", (_uid(), clean_url)).fetchone()
        if row is None:
            cur = conn.execute(
                """
                INSERT INTO applications (user_id, company, job_title, location, job_description, job_requirements, job_link, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (_uid(), company, job_title, location, job_description, job_requirements, job_link, status),
            )
            conn.commit()
            return cur.lastrowid
        app_id = row[0]
        # Re-tailoring a job you already applied to keeps where it stands (Applied, Interview, ...).
        conn.execute(
            f"""
            UPDATE applications SET company = ?, job_title = ?, location = ?, job_description = ?,
                job_requirements = ?, updated_at = CURRENT_TIMESTAMP,
                status = CASE WHEN status IN ({', '.join('?' for _ in USER_STATUSES)}) THEN status ELSE ? END
            WHERE id = ? AND user_id = ?
            """,
            (company, job_title, location, job_description, job_requirements, *USER_STATUSES, status, app_id, _uid()),
        )
        conn.commit()
        return app_id


def update_application(
    app_id: int,
    status: Optional[str] = None,
    score: Optional[float] = None,
    resume_status: Optional[str] = None,
    pdf_path: Optional[str] = None,
    drive_link: Optional[str] = None,
    comment: Optional[str] = None,
    **extra,
) -> None:
    """Updates fields of an existing application record. ``extra`` may set the newer columns (deadline, facts_used,
    details, gaps); lists and dicts are stored as JSON."""
    updates = ["updated_at = CURRENT_TIMESTAMP"]
    params = []
    for col, value in extra.items():
        if col not in _NEW_COLUMNS or value is None:
            continue
        updates.append(f"{col} = ?")
        params.append(json.dumps(value) if isinstance(value, (list, dict)) else value)

    if status is not None:
        updates.append("status = ?")
        params.append(status)
    if score is not None:
        updates.append("score = ?")
        params.append(score)
    if resume_status is not None:
        updates.append("resume_status = ?")
        params.append(resume_status)
    if pdf_path is not None:
        updates.append("pdf_path = ?")
        params.append(pdf_path)
    if drive_link is not None:
        updates.append("drive_link = ?")
        params.append(drive_link)
    if comment is not None:
        updates.append("comment = ?")
        params.append(comment)

    params += [app_id, _uid()]   # only ever this user's own row
    query = f"UPDATE applications SET {', '.join(updates)} WHERE id = ? AND user_id = ?"

    with _get_connection() as conn:
        conn.execute(query, params)
        conn.commit()


def get_recent_applications(limit: int = 10) -> list[dict]:
    """Returns the most recent application records."""
    with _get_connection() as conn:
        cur = conn.execute(
            """
            SELECT id, company, job_title, status, score, resume_status, drive_link, created_at
            FROM applications
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (_uid(), limit),
        )
        return [dict(row) for row in cur.fetchall()]



def recent_job_texts(limit: int = 15) -> list[str]:
    """Title and summary of the jobs you sent most recently: what you tend to go for."""
    with _get_connection() as conn:
        cur = conn.execute(
            "SELECT job_title, company, job_description FROM applications WHERE user_id = ? ORDER BY id DESC LIMIT ?", (_uid(), limit))
        return [f"{r['job_title']} at {r['company']}. {(r['job_description'] or '')[:240]}".strip() for r in cur.fetchall()]


def application_links() -> list[str]:
    with _get_connection() as conn:
        return [r["job_link"] for r in conn.execute("SELECT job_link FROM applications WHERE user_id = ? AND job_link != ''", (_uid(),)).fetchall()]


# ---------------------------------------------------------------- the tracker: where each application stands

def _row(r) -> dict:
    d = dict(r)
    for col in ("facts_used", "gaps"):
        try:
            d[col] = json.loads(d.get(col) or "[]")
        except ValueError:
            d[col] = []
    try:
        d["details"] = json.loads(d.get("details") or "{}")
    except ValueError:
        d["details"] = {}
    return d


def get_application(app_id: int) -> Optional[dict]:
    with _get_connection() as conn:
        r = conn.execute("SELECT * FROM applications WHERE id = ? AND user_id = ?", (app_id, _uid())).fetchone()
        return _row(r) if r else None


def set_status(app_id: int, status: str) -> Optional[dict]:
    """Move an application on (Applied, In Review, Interviewing, Offer, Rejected, Withdrawn). Applied or In Review
    (re)starts the follow-up clock; any other move stops it. Returns the updated application."""
    with _get_connection() as conn:
        if status in WAITING:
            conn.execute(
                f"""UPDATE applications SET status = ?, status_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP,
                       applied_at = COALESCE(applied_at, CURRENT_TIMESTAMP),
                       followup_at = datetime('now', '+{FOLLOWUP_DAYS} days') WHERE id = ? AND user_id = ?""", (status, app_id, _uid()))
        else:
            conn.execute("""UPDATE applications SET status = ?, status_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP,
                               followup_at = NULL WHERE id = ? AND user_id = ?""", (status, app_id, _uid()))
        conn.commit()
    return get_application(app_id)


def snooze_followup(app_id: int, days: int = 7) -> None:
    with _get_connection() as conn:
        conn.execute(f"UPDATE applications SET followup_at = datetime('now', '+{int(days)} days') WHERE id = ? AND user_id = ?", (app_id, _uid()))
        conn.commit()


def due_followups() -> list[dict]:
    """Applied a while ago and no news since: time to ask."""
    with _get_connection() as conn:
        rows = conn.execute("""SELECT * FROM applications WHERE user_id = ? AND status IN (?, ?) AND followup_at IS NOT NULL
                                 AND followup_at <= datetime('now') ORDER BY followup_at""", (_uid(), *WAITING)).fetchall()
        return [_row(r) for r in rows]


def deadlines_soon(days: int = 3) -> list[dict]:
    """Resumes ready but not marked applied, whose posting closes within ``days`` days (and not reminded yet)."""
    with _get_connection() as conn:
        rows = conn.execute("""SELECT * FROM applications WHERE user_id = ? AND status = 'Ready' AND deadline != '' AND deadline_reminded = 0
                                 AND date(deadline) >= date('now') AND date(deadline) <= date('now', ?)
                                 ORDER BY deadline""", (_uid(), f"+{int(days)} days")).fetchall()
        return [_row(r) for r in rows]


def mark_deadline_reminded(app_id: int) -> None:
    with _get_connection() as conn:
        conn.execute("UPDATE applications SET deadline_reminded = 1 WHERE id = ? AND user_id = ?", (app_id, _uid()))
        conn.commit()


def list_applications(statuses: Optional[tuple[str, ...]] = None, limit: int = 8, offset: int = 0) -> tuple[list[dict], int]:
    """(one page of applications, newest first, how many there are in all)."""
    where, params = "WHERE user_id = ?", [_uid()]
    if statuses:
        where += f" AND status IN ({', '.join('?' for _ in statuses)})"
        params += list(statuses)
    with _get_connection() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM applications {where}", params).fetchone()[0]
        rows = conn.execute(f"SELECT * FROM applications {where} ORDER BY id DESC LIMIT ? OFFSET ?", params + [limit, offset]).fetchall()
        return [_row(r) for r in rows], total


def all_applications() -> list[dict]:
    with _get_connection() as conn:
        return [_row(r) for r in conn.execute("SELECT * FROM applications WHERE user_id = ? ORDER BY id", (_uid(),)).fetchall()]


def status_counts() -> dict[str, int]:
    with _get_connection() as conn:
        return {r[0]: r[1] for r in conn.execute("SELECT status, COUNT(*) FROM applications WHERE user_id = ? GROUP BY status", (_uid(),)).fetchall()}


def week_stats() -> dict[str, int]:
    """The last 7 days: resumes written, applications sent, replies (interview, offer or rejection)."""
    with _get_connection() as conn:
        one = lambda sql, *p: conn.execute(sql.replace("FROM applications WHERE", "FROM applications WHERE user_id = ? AND", 1), (_uid(), *p)).fetchone()[0]
        return {
            "written": one("SELECT COUNT(*) FROM applications WHERE created_at >= datetime('now', '-7 days')"),
            "applied": one("SELECT COUNT(*) FROM applications WHERE applied_at >= datetime('now', '-7 days')"),
            "interviews": one("SELECT COUNT(*) FROM applications WHERE status IN (?, ?) AND status_at >= datetime('now', '-7 days')",
                              INTERVIEW, OFFER),
            "rejected": one("SELECT COUNT(*) FROM applications WHERE status = ? AND status_at >= datetime('now', '-7 days')", REJECTED),
            "waiting": one("SELECT COUNT(*) FROM applications WHERE status IN (?, ?)", *WAITING),
        }


# ---------------------------------------------------------------- the same job, found through another link

_COMPANY_NOISE = re.compile(r"\b(sdn\.?\s*bhd\.?|bhd|berhad|inc|ltd|limited|llc|plc|pte|corp(oration)?|co|company|group|"
                            r"holdings?|technologies|technology|malaysia|\(m\)|m)\b\.?")
_TITLE_NOISE = re.compile(r"\b(20\d\d|19\d\d|summer|winter|fall|spring|intake|batch|remote|hybrid|on-?site|"
                          r"kuala lumpur|kl|malaysia|selangor|penang|johor|singapore|full[- ]time|part[- ]time)\b")


def _norm_company(name: str) -> str:
    low = re.sub(r"\([^)]*\)", " ", (name or "").lower())   # "Deloitte (Deloitte in Malaysia)" -> "deloitte"
    return re.sub(r"[^a-z0-9]+", " ", _COMPANY_NOISE.sub(" ", low)).strip()


def _same_company(a: str, b: str) -> bool:
    """Equal once legal endings and bracketed notes are dropped, or one is the other's leading words
    ("DHL" and "DHL Information Services (Asia-Pacific) Sdn. Bhd.")."""
    ca, cb = _norm_company(a), _norm_company(b)
    if not ca or not cb:
        return False
    if ca == cb:
        return True
    short, long_ = sorted((ca, cb), key=len)
    return len(short) >= 3 and (long_ + " ").startswith(short + " ")


def _title_words(title: str) -> set[str]:
    low = _TITLE_NOISE.sub(" ", (title or "").lower())
    low = re.sub(r"\binternship\b", "intern", low)
    return {w for w in re.findall(r"[a-z0-9+#]+", low) if w not in {"and", "the", "of", "a", "an", "for", "to", "in"}}


def same_job(company_a: str, title_a: str, company_b: str, title_b: str) -> bool:
    if not _same_company(company_a, company_b):
        return False
    wa, wb = _title_words(title_a), _title_words(title_b)
    return bool(wa and wb) and len(wa & wb) / len(wa | wb) >= 0.75


def find_similar(company: str, job_title: str, exclude_link: str = "") -> Optional[dict]:
    """An application you already made for the same job at the same company (through another link), or None."""
    target = (exclude_link or "").strip().lower()
    with _get_connection() as conn:
        rows = conn.execute("SELECT * FROM applications WHERE user_id = ? AND status != 'Failed' ORDER BY id DESC LIMIT 500", (_uid(),)).fetchall()
    for r in rows:
        if (r["job_link"] or "").strip().lower() == target:
            continue
        if same_job(company, job_title, r["company"], r["job_title"]):
            return _row(r)
    return None


def get_application_by_link(url: str) -> Optional[dict]:
    with _get_connection() as conn:
        r = conn.execute("SELECT * FROM applications WHERE user_id = ? AND LOWER(TRIM(job_link)) = ?", (_uid(), url.strip().lower())).fetchone()
        return _row(r) if r else None


# ---------------------------------------------------------------- moving your data (export / import)

_PORTABLE_SKIP = {"user_id"}   # who a row belongs to is decided where it is imported, never by the file


def export_tracker() -> dict:
    """Everything in the tracker part of the database for the acting user as plain lists: applications, the job pages
    watched and the jobs already seen. (Settings are handled by the caller, which knows which ones are safe to move.)"""
    with _get_connection() as conn:
        return {name: [{k: v for k, v in dict(r).items() if k not in _PORTABLE_SKIP}
                       for r in conn.execute(f"SELECT * FROM {name} WHERE user_id = ? ORDER BY rowid", (_uid(),)).fetchall()]
                for name in ("applications", "watch_sources", "seen_jobs")}


def replace_tracker(data: dict) -> dict[str, int]:
    """Replace the acting user's applications, watched pages and seen jobs with these rows, all or nothing. Columns this
    database doesn't have are ignored, a row that isn't a dict is skipped, and the rows always become this user's: row ids
    and user ids in the file are not used (they could collide with another user's). Returns how many rows went in."""
    counts: dict[str, int] = {}
    uid = _uid()
    with _get_connection() as conn:
        conn.execute("BEGIN")
        try:
            for name in ("applications", "watch_sources", "seen_jobs"):
                cols = [c for c in _columns(conn, name) if c not in ("id", "user_id")]
                conn.execute(f"DELETE FROM {name} WHERE user_id = ?", (uid,))
                n = 0
                for row in data.get(name, []):
                    if not isinstance(row, dict):
                        continue
                    use = [c for c in cols if c in row and row[c] is not None]
                    if not use:
                        continue
                    conn.execute(f"INSERT OR REPLACE INTO {name} (user_id, {', '.join(use)}) VALUES (?, {', '.join('?' for _ in use)})",
                                 [uid] + [row[c] if isinstance(row[c], (int, float, str)) else json.dumps(row[c]) for c in use])
                    n += 1
                counts[name] = n
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return counts
