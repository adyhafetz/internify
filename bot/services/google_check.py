"""One-time startup check that says plainly whether Google Drive and Sheets are connected, and if not, why.

Google sync is optional, so a problem never stops the bot. Without this check a bad token or a missing
value just means files quietly stay in the local output folder."""
from __future__ import annotations

import logging

from bot import config
from service import users
from bot.services import google_auth

logger = logging.getLogger(__name__)


def _hint(err: Exception) -> str:
    text = str(err)
    low = text.lower()
    if "invalid_grant" in low or "expired or revoked" in low:
        return ("the Google login has expired or was revoked. If your Google Cloud consent screen is in "
                "'Testing' mode, tokens expire after 7 days: create a new refresh token (scripts/get_gdrive_token.py)")
    if ("404" in low or "not found" in low or "403" in low or "permission" in low) and config.GOOGLE_ACCESS == "app":
        return ("this Google login is the limited kind (GOOGLE_ACCESS=app), which can only open files Internify created. "
                "If this sheet or folder was made by hand, open the setup page's Google step and press “Move to a new tracker sheet”, "
                "or set GOOGLE_ACCESS=full in .env to go back to the full permission")
    if "404" in low or "not found" in low:
        return "Google couldn't find it. Check the ID, and that this Google account can open it"
    if "403" in low or "permission" in low:
        return "this Google account doesn't have access. Share the Sheet/folder with it"
    return text[:160]


def check_google_setup() -> list[str]:
    """Returns human-readable status lines; also logs them."""
    lines: list[str] = []
    sheet_id, sheet_tab, folder_id = google_auth.ids()      # from .env, or from the person's own connection in the hosted version
    wants_sheet, wants_drive = bool(sheet_id), bool(folder_id)

    creds = google_auth.get_google_credentials()
    if creds is None and users.cloud():
        lines.append("Google: not connected. Open /profile and tap Connect Google. Until then resumes arrive here in Telegram, and your tracker is /applied.")
        for line in lines:
            logger.info(line)
        return lines
    if creds is None:
        missing = [n for n, v in (("GOOGLE_CLIENT_ID", config.GOOGLE_CLIENT_ID),
                                  ("GOOGLE_CLIENT_SECRET", config.GOOGLE_CLIENT_SECRET),
                                  ("GOOGLE_REFRESH_TOKEN", config.GOOGLE_REFRESH_TOKEN)) if not v]
        lines.append(
            "Google sync: OFF. No Google login found, so PDFs stay in the local output folder and nothing is added "
            "to your Sheet." + (f" Not set in .env: {', '.join(missing)}." if missing else "")
            + " (Files such as token.json aren't visible inside Docker. Put the values in .env.)"
        )
        for line in lines:
            logger.warning(line)
        return lines

    lines.append(f"Google login found ({google_auth.credential_source}).")

    if wants_sheet:
        try:
            from bot.services import sheets
            client = sheets._get_client()
            sh = client.open_by_key(sheet_id)
            tabs = [w.title for w in sh.worksheets()]
            tab = sheet_tab if sheet_tab in tabs else tabs[0]
            note = "" if sheet_tab in tabs else f" (no tab named '{sheet_tab}', using '{tab}')"
            lines.append(f"Sheet: connected to '{sh.title}', tab '{tab}'{note}.")
        except Exception as e:
            lines.append(f"Sheet: NOT working. {_hint(e)}")
    else:
        lines.append("Sheet: off (GOOGLE_SHEET_ID is empty).")

    if wants_drive:
        try:
            from bot.services import drive
            svc = drive._get_drive_service()
            folder = svc.files().get(fileId=folder_id.strip().rstrip("/"),
                                     fields="name", supportsAllDrives=True).execute()
            lines.append(f"Drive: connected to folder '{folder.get('name')}'.")
        except Exception as e:
            lines.append(f"Drive: NOT working. {_hint(e)}")
    else:
        lines.append("Drive: off (GOOGLE_DRIVE_FOLDER_ID is empty).")

    for line in lines:
        (logger.warning if "NOT working" in line else logger.info)("Google sync check: %s", line)
    return lines
