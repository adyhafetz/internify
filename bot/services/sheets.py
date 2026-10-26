from __future__ import annotations
import logging
import os
from typing import Optional
from bot import config
from bot.services.google_auth import get_google_credentials

logger = logging.getLogger(__name__)

_gspread_client = None


def _get_client():
    global _gspread_client
    if _gspread_client is not None:
        return _gspread_client

    creds = get_google_credentials()
    if not creds:
        return None

    try:
        import gspread
        _gspread_client = gspread.authorize(creds)
        logger.info("Initialized Google Sheets client with unified Google credentials.")
        return _gspread_client
    except Exception as e:
        logger.error("Failed to initialize gspread client: %s", e)
        return None


def _get_worksheet():
    client = _get_client()
    if not client or not config.GOOGLE_SHEET_ID:
        return None
    try:
        sh = client.open_by_key(config.GOOGLE_SHEET_ID)
        try:
            return sh.worksheet(config.GOOGLE_SHEET_NAME)
        except Exception:
            logger.info("Tab '%s' not found in spreadsheet. Falling back to first sheet tab.", config.GOOGLE_SHEET_NAME)
            return sh.get_worksheet(0)
    except Exception as e:
        logger.error("Failed to open spreadsheet '%s': %s", config.GOOGLE_SHEET_ID, e)
        return None


def is_duplicate_job(job_url: str) -> bool:
    """Checks if the given job URL is already logged in the tracker sheet."""
    ws = _get_worksheet()
    if not ws:
        return False
    try:
        # Find header column index for 'Job Link'
        headers = ws.row_values(1)
        if "Job Link" not in headers:
            return False
        col_idx = headers.index("Job Link") + 1
        existing_urls = ws.col_values(col_idx)[1:]  # Skip header
        clean_target = job_url.strip().lower()
        return any(clean_target == u.strip().lower() for u in existing_urls if u)
    except Exception as e:
        logger.warning("Error checking duplicate in Google Sheet: %s", e)
        return False


def append_application(
    company: str,
    job_title: str,
    location: str,
    job_description: str,
    job_requirements: str,
    job_link: str,
    status: str = "New",
    resume_link: str = "",
    resume_status: str = "",
    comment: str = "",
) -> Optional[int]:
    """Appends a new job application row and returns the new row number, or None."""
    ws = _get_worksheet()
    if not ws:
        return None
    try:
        headers = ws.row_values(1)
        row_dict = {
            "Company": company,
            "Job Title": job_title,
            "Location": location,
            "Job Description": job_description[:500],
            "Job Requirements": job_requirements[:500],
            "Job Link": job_link,
            "Status": status,
            "Resume Link": resume_link,
            "Resume Status": resume_status,
            "Comment": comment,
        }
        # Build row in header order if headers exist
        if headers:
            row_vals = [row_dict.get(h, "") for h in headers]
        else:
            row_vals = list(row_dict.values())

        res = ws.append_row(row_vals, value_input_option="USER_ENTERED")
        import re
        updated_range = (res or {}).get("updates", {}).get("updatedRange", "")
        match = re.search(r"[A-Z]+(\d+)", updated_range)
        if match:
            return int(match.group(1))
        return len(ws.col_values(1))
    except Exception as e:
        logger.error("Failed to append row to Google Sheet: %s", e)
        return None


def update_application_status(
    row_num: int,
    resume_link: str = "",
    resume_status: str = "",
    comment: str = "",
) -> None:
    """Updates the status and resume link for an existing row."""
    ws = _get_worksheet()
    if not ws or row_num <= 1:
        return
    try:
        headers = ws.row_values(1)
        header_map = {h: idx + 1 for idx, h in enumerate(headers)}
        if "Resume Link" in header_map and resume_link:
            ws.update_cell(row_num, header_map["Resume Link"], resume_link)
        if "Resume Status" in header_map and resume_status:
            ws.update_cell(row_num, header_map["Resume Status"], resume_status)
        if "Comment" in header_map and comment:
            ws.update_cell(row_num, header_map["Comment"], comment)
    except Exception as e:
        logger.error("Failed to update row %d in Google Sheet: %s", row_num, e)

