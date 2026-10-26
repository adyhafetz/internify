from __future__ import annotations
import os
import sqlite3
import logging
from datetime import datetime
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
        conn.commit()
    logger.info("Initialized SQLite database at %s", DB_PATH)


def is_duplicate_url(url: str) -> bool:
    """Checks if an application with the given job URL already exists in SQLite."""
    clean_url = url.strip().lower()
    with _get_connection() as conn:
        cur = conn.execute("SELECT id FROM applications WHERE LOWER(TRIM(job_link)) = ?", (clean_url,))
        return cur.fetchone() is not None


def add_application(
    company: str,
    job_title: str,
    location: str = "",
    job_description: str = "",
    job_requirements: str = "",
    job_link: str = "",
    status: str = "Processing",
) -> int:
    """Inserts a new application record and returns its ID."""
    with _get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO applications (company, job_title, location, job_description, job_requirements, job_link, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (company, job_title, location, job_description, job_requirements, job_link, status),
        )
        conn.commit()
        return cur.lastrowid


def update_application(
    app_id: int,
    status: Optional[str] = None,
    score: Optional[float] = None,
    resume_status: Optional[str] = None,
    pdf_path: Optional[str] = None,
    drive_link: Optional[str] = None,
    comment: Optional[str] = None,
) -> None:
    """Updates fields of an existing application record."""
    updates = ["updated_at = CURRENT_TIMESTAMP"]
    params = []

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

