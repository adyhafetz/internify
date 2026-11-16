"""Connecting Google from the setup page: sign-in, then creating the Drive folder and the tracker sheet for you."""
from __future__ import annotations

import os

# oauthlib refuses plain http even for localhost unless told otherwise; the setup page only ever runs on your own computer
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

FOLDER_NAME = "Internify resumes"
from bot.services import sheet_layout

HEADERS, SHEET_TITLE, STATUSES, TAB_NAME = sheet_layout.HEADERS, sheet_layout.SHEET_TITLE, sheet_layout.STATUSES, sheet_layout.TAB_NAME

# The limited permission: only files Internify itself creates (its folder and its tracker sheet). It is the default for a
# new sign-in; installs that signed in earlier keep the broader one until you choose to move (see copy_rows).
LIMITED_SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def make_flow(client_id: str, client_secret: str, redirect_uri: str, scopes: list[str] | None = None):
    """A sign-in flow for a Google "Desktop app" client, finished on the setup page's own address."""
    from google_auth_oauthlib.flow import Flow
    config = {"installed": {"client_id": client_id, "client_secret": client_secret, "redirect_uris": [redirect_uri],
                            "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token"}}
    return Flow.from_client_config(config, scopes=scopes or LIMITED_SCOPES, redirect_uri=redirect_uri)


def _services(creds):
    from googleapiclient.discovery import build
    return (build("drive", "v3", credentials=creds, cache_discovery=False),
            build("sheets", "v4", credentials=creds, cache_discovery=False))


def create_tracker(creds) -> dict[str, str]:
    """Make the Drive folder for resumes and the tracker spreadsheet (the template layout). Returns the IDs."""
    drive, sheets = _services(creds)
    folder = drive.files().create(body={"name": FOLDER_NAME, "mimeType": "application/vnd.google-apps.folder"}, fields="id").execute()
    sid, _ = sheet_layout.create_sheet(sheets)
    return {"GOOGLE_SHEET_ID": sid, "GOOGLE_SHEET_NAME": TAB_NAME, "GOOGLE_DRIVE_FOLDER_ID": folder["id"]}


def old_credentials(env: dict[str, str]):
    """The login from an earlier setup (full access), built from the saved .env values, or None if there isn't one."""
    cid, secret, token = env.get("GOOGLE_CLIENT_ID", ""), env.get("GOOGLE_CLIENT_SECRET", ""), env.get("GOOGLE_REFRESH_TOKEN", "")
    if not (cid and secret and token):
        return None
    from google.oauth2.credentials import Credentials
    return Credentials(token=None, refresh_token=token, client_id=cid, client_secret=secret,
                       token_uri="https://oauth2.googleapis.com/token",
                       scopes=["https://www.googleapis.com/auth/drive", "https://www.googleapis.com/auth/spreadsheets"])


def copy_rows(old_creds, old_sheet_id: str, old_tab: str, new_creds, new_sheet_id: str) -> int:
    """Copy every row of an older tracker sheet into a new one, rearranged into the template layout by header name,
    using the old (full access) login to read and the new login to write. The old sheet is not changed.
    Returns how many rows were copied."""
    _, old_sheets = _services(old_creds)
    got = old_sheets.spreadsheets().values().get(spreadsheetId=old_sheet_id, range=old_tab or TAB_NAME).execute()
    rows = sheet_layout.map_rows(got.get("values", []))
    if rows:
        _, new_sheets = _services(new_creds)
        new_sheets.spreadsheets().values().update(
            spreadsheetId=new_sheet_id, range=f"{TAB_NAME}!A2", valueInputOption="USER_ENTERED", body={"values": rows}).execute()
    return len(rows)
