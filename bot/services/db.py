from __future__ import annotations
import json
import os
import re
import sqlite3
import logging
from typing import Optional

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DATABASE_PATH", "output/internify.db")


def _get_connection() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Initializes the SQLite schema if it does not already exist."""
    with _get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company TEXT NOT NULL,
                job_title TEXT NOT NULL,
                location TEXT DEFAULT '',
                job_description TEXT DEFAULT '',
                job_requirements TEXT DEFAULT '',
                job_link TEXT UNIQUE,
                status TEXT DEFAULT 'New',
                score REAL DEFAULT 0.0,
                resume_status TEXT DEFAULT '',
                pdf_path TEXT DEFAULT '',
                drive_link TEXT DEFAULT '',
                comment TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_apps_job_link ON applications(job_link)")
        # The daily job check: where to look, what it has already seen, and its settings
        conn.execute("""
            CREATE TABLE IF NOT EXISTS watch_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT UNIQUE NOT NULL,
                kind TEXT NOT NULL,              -- greenhouse | lever | page
                ident TEXT DEFAULT '',           -- board / company name for the job-board kinds
                label TEXT DEFAULT '',
                auto INTEGER DEFAULT 0,          -- 1 = found from the jobs you already sent
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_checked TIMESTAMP,
                last_status TEXT DEFAULT ''
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS seen_jobs (
                url TEXT PRIMARY KEY,
                title TEXT DEFAULT '',
                company TEXT DEFAULT '',
                status TEXT DEFAULT 'new',       -- new | low-fit | shortlisted | tailored | failed | rejected
                fit REAL DEFAULT 0.0,
                reason TEXT DEFAULT '',
                first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        _migrate(conn)
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


def _migrate(conn: sqlite3.Connection) -> None:
    have = {r[1] for r in conn.execute("PRAGMA table_info(applications)").fetchall()}
    for col, kind in _NEW_COLUMNS.items():
        if col not in have:
            conn.execute(f"ALTER TABLE applications ADD COLUMN {col} {kind}")
    for old, new in _RENAMED.items():
        conn.execute("UPDATE applications SET status = ? WHERE status = ?", (new, old))


def is_duplicate_url(url: str) -> bool:
    """Checks if an application with the given job URL already exists in SQLite."""
    clean_url = url.strip().lower()
    with _get_connection() as conn:
        cur = conn.execute("SELECT id FROM applications WHERE LOWER(TRIM(job_link)) = ?", (clean_url,))
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
        row = conn.execute("SELECT id FROM applications WHERE LOWER(TRIM(job_link)) = ?", (clean_url,)).fetchone()
        if row is None:
            cur = conn.execute(
                """
                INSERT INTO applications (company, job_title, location, job_description, job_requirements, job_link, status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (company, job_title, location, job_description, job_requirements, job_link, status),
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
            WHERE id = ?
            """,
            (company, job_title, location, job_description, job_requirements, *USER_STATUSES, status, app_id),
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

    params.append(app_id)
    query = f"UPDATE applications SET {', '.join(updates)} WHERE id = ?"

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
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(row) for row in cur.fetchall()]



def recent_job_texts(limit: int = 15) -> list[str]:
    """Title and summary of the jobs you sent most recently: what you tend to go for."""
    with _get_connection() as conn:
        cur = conn.execute(
            "SELECT job_title, company, job_description FROM applications ORDER BY id DESC LIMIT ?", (limit,))
        return [f"{r['job_title']} at {r['company']}. {(r['job_description'] or '')[:240]}".strip() for r in cur.fetchall()]


def application_links() -> list[str]:
    with _get_connection() as conn:
        return [r["job_link"] for r in conn.execute("SELECT job_link FROM applications WHERE job_link != ''").fetchall()]


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
        r = conn.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()
        return _row(r) if r else None


def set_status(app_id: int, status: str) -> Optional[dict]:
    """Move an application on (Applied, In Review, Interviewing, Offer, Rejected, Withdrawn). Applied or In Review
    (re)starts the follow-up clock; any other move stops it. Returns the updated application."""
    with _get_connection() as conn:
        if status in WAITING:
            conn.execute(
                f"""UPDATE applications SET status = ?, status_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP,
                       applied_at = COALESCE(applied_at, CURRENT_TIMESTAMP),
                       followup_at = datetime('now', '+{FOLLOWUP_DAYS} days') WHERE id = ?""", (status, app_id))
        else:
            conn.execute("""UPDATE applications SET status = ?, status_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP,
                               followup_at = NULL WHERE id = ?""", (status, app_id))
        conn.commit()
    return get_application(app_id)


def snooze_followup(app_id: int, days: int = 7) -> None:
    with _get_connection() as conn:
        conn.execute(f"UPDATE applications SET followup_at = datetime('now', '+{int(days)} days') WHERE id = ?", (app_id,))
        conn.commit()


def due_followups() -> list[dict]:
    """Applied a while ago and no news since: time to ask."""
    with _get_connection() as conn:
        rows = conn.execute("""SELECT * FROM applications WHERE status IN (?, ?) AND followup_at IS NOT NULL
                                 AND followup_at <= datetime('now') ORDER BY followup_at""", WAITING).fetchall()
        return [_row(r) for r in rows]


def deadlines_soon(days: int = 3) -> list[dict]:
    """Resumes ready but not marked applied, whose posting closes within ``days`` days (and not reminded yet)."""
    with _get_connection() as conn:
        rows = conn.execute("""SELECT * FROM applications WHERE status = 'Ready' AND deadline != '' AND deadline_reminded = 0
                                 AND date(deadline) >= date('now') AND date(deadline) <= date('now', ?)
                                 ORDER BY deadline""", (f"+{int(days)} days",)).fetchall()
        return [_row(r) for r in rows]


def mark_deadline_reminded(app_id: int) -> None:
    with _get_connection() as conn:
        conn.execute("UPDATE applications SET deadline_reminded = 1 WHERE id = ?", (app_id,))
        conn.commit()


def list_applications(statuses: Optional[tuple[str, ...]] = None, limit: int = 8, offset: int = 0) -> tuple[list[dict], int]:
    """(one page of applications, newest first, how many there are in all)."""
    where, params = "", []
    if statuses:
        where = f"WHERE status IN ({', '.join('?' for _ in statuses)})"
        params = list(statuses)
    with _get_connection() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM applications {where}", params).fetchone()[0]
        rows = conn.execute(f"SELECT * FROM applications {where} ORDER BY id DESC LIMIT ? OFFSET ?", params + [limit, offset]).fetchall()
        return [_row(r) for r in rows], total


def all_applications() -> list[dict]:
    with _get_connection() as conn:
        return [_row(r) for r in conn.execute("SELECT * FROM applications ORDER BY id").fetchall()]


def status_counts() -> dict[str, int]:
    with _get_connection() as conn:
        return {r[0]: r[1] for r in conn.execute("SELECT status, COUNT(*) FROM applications GROUP BY status").fetchall()}


def week_stats() -> dict[str, int]:
    """The last 7 days: resumes written, applications sent, replies (interview, offer or rejection)."""
    with _get_connection() as conn:
        one = lambda sql, *p: conn.execute(sql, p).fetchone()[0]
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
        rows = conn.execute("SELECT * FROM applications WHERE status != 'Failed' ORDER BY id DESC LIMIT 500").fetchall()
    for r in rows:
        if (r["job_link"] or "").strip().lower() == target:
            continue
        if same_job(company, job_title, r["company"], r["job_title"]):
            return _row(r)
    return None


def get_application_by_link(url: str) -> Optional[dict]:
    with _get_connection() as conn:
        r = conn.execute("SELECT * FROM applications WHERE LOWER(TRIM(job_link)) = ?", (url.strip().lower(),)).fetchone()
        return _row(r) if r else None
