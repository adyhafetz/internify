"""Connecting Google from the setup page: sign-in, then creating the Drive folder and the tracker sheet for you."""
from __future__ import annotations

import os

from bot.services.google_auth import GOOGLE_SCOPES

# oauthlib refuses plain http even for localhost unless told otherwise; the setup page only ever runs on your own computer
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

FOLDER_NAME = "Internify resumes"
SHEET_TITLE = "Internify tracker"
TAB_NAME = "Tracker"
HEADERS = ["Company", "Job Title", "Location", "Job Description", "Job Requirements", "Job Link", "Status", "Resume Link", "Resume Status", "Comment"]
STATUSES = ["New", "Applied", "In Review", "Interviewing", "Offer", "Rejected", "Withdrawn"]


def make_flow(client_id: str, client_secret: str, redirect_uri: str):
    """A sign-in flow for a Google "Desktop app" client, finished on the setup page's own address."""
    from google_auth_oauthlib.flow import Flow
    config = {"installed": {"client_id": client_id, "client_secret": client_secret, "redirect_uris": [redirect_uri],
                            "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token"}}
    return Flow.from_client_config(config, scopes=GOOGLE_SCOPES, redirect_uri=redirect_uri)


def _services(creds):
    from googleapiclient.discovery import build
    return (build("drive", "v3", credentials=creds, cache_discovery=False),
            build("sheets", "v4", credentials=creds, cache_discovery=False))


def create_tracker(creds) -> dict[str, str]:
    """Make the Drive folder for resumes and the tracker spreadsheet (right columns, a Status drop-down). Returns the IDs."""
    drive, sheets = _services(creds)
    folder = drive.files().create(body={"name": FOLDER_NAME, "mimeType": "application/vnd.google-apps.folder"}, fields="id").execute()
    sheet = sheets.spreadsheets().create(body={
        "properties": {"title": SHEET_TITLE},
        "sheets": [{"properties": {"title": TAB_NAME, "gridProperties": {"frozenRowCount": 1}}}]},
        fields="spreadsheetId,sheets.properties.sheetId").execute()
    sid, tab_id = sheet["spreadsheetId"], sheet["sheets"][0]["properties"]["sheetId"]
    sheets.spreadsheets().values().update(spreadsheetId=sid, range=f"{TAB_NAME}!A1:J1", valueInputOption="RAW", body={"values": [HEADERS]}).execute()
    status_col = HEADERS.index("Status")
    sheets.spreadsheets().batchUpdate(spreadsheetId=sid, body={"requests": [
        {"repeatCell": {"range": {"sheetId": tab_id, "startRowIndex": 0, "endRowIndex": 1},
                        "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}}, "fields": "userEnteredFormat.textFormat.bold"}},
        {"setDataValidation": {"range": {"sheetId": tab_id, "startRowIndex": 1, "endRowIndex": 2000, "startColumnIndex": status_col, "endColumnIndex": status_col + 1},
                               "rule": {"condition": {"type": "ONE_OF_LIST", "values": [{"userEnteredValue": v} for v in STATUSES]}, "showCustomUi": True, "strict": False}}},
    ]}).execute()
    return {"GOOGLE_SHEET_ID": sid, "GOOGLE_SHEET_NAME": TAB_NAME, "GOOGLE_DRIVE_FOLDER_ID": folder["id"]}
