from __future__ import annotations
import logging
import os
from bot import config
from service import users

logger = logging.getLogger(__name__)

# Which credential source was used last (for the startup self-check)
credential_source: str = "none"

FULL_SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]
APP_SCOPES = ["https://www.googleapis.com/auth/drive.file"]   # only files Internify created
GOOGLE_SCOPES = FULL_SCOPES   # kept for scripts that import it


def scopes() -> list[str]:
    """What to ask Google for: the limited permission when GOOGLE_ACCESS=app, otherwise the full one (the default, so a copy
    signed in earlier keeps working)."""
    return APP_SCOPES if config.GOOGLE_ACCESS == "app" else FULL_SCOPES


def ids() -> tuple[str, str, str]:
    """(spreadsheet id, tab name, Drive folder id) for the acting user: from .env in single mode, from the person's own connection in
    the hosted version (empty until they connect)."""
    if users.cloud():
        from bot.services import google_link
        link = google_link.get()
        return (link.sheet_id, link.sheet_tab, link.folder_id) if link else ("", "Tracker", "")
    return config.GOOGLE_SHEET_ID, config.GOOGLE_SHEET_NAME, config.GOOGLE_DRIVE_FOLDER_ID


def get_google_credentials():
    """
    Returns unified Google OAuth2 or Service Account credentials for both Drive and Sheets.
    Priority order:
    1. OAuth2 Refresh Token (GOOGLE_REFRESH_TOKEN, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET)
    2. token.json file (generated via scripts/get_google_token.py)
    3. Service Account JSON file (fallback)
    """
    global credential_source
    credential_source = "none"

    if users.cloud():   # hosted: each person's own connection (never the server's .env)
        from bot.services import google_link
        creds = google_link.credentials()
        credential_source = "this person's own Google connection" if creds else "none"
        return creds

    # 1. OAuth2 refresh token from environment variables
    if config.GOOGLE_REFRESH_TOKEN and config.GOOGLE_CLIENT_ID and config.GOOGLE_CLIENT_SECRET:
        try:
            from google.oauth2.credentials import Credentials
            creds = Credentials(
                token=None,
                refresh_token=config.GOOGLE_REFRESH_TOKEN,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=config.GOOGLE_CLIENT_ID,
                client_secret=config.GOOGLE_CLIENT_SECRET,
                scopes=scopes(),
            )
            credential_source = "refresh token in .env"
            return creds
        except Exception as e:
            logger.error("Failed to initialize Google OAuth2 credentials from env: %s", e)

    # 2. token.json file from one-time authorization
    token_file = config.GOOGLE_TOKEN_FILE
    if token_file and os.path.exists(token_file):
        try:
            from google.oauth2.credentials import Credentials
            creds = Credentials.from_authorized_user_file(token_file, scopes=scopes())
            credential_source = f"file {token_file}"
            return creds
        except Exception as e:
            logger.error("Failed to load Google credentials from %s: %s", token_file, e)

    # 3. Service Account JSON file (fallback)
    sa_file = config.GOOGLE_SERVICE_ACCOUNT_FILE
    if sa_file and os.path.exists(sa_file):
        try:
            from google.oauth2 import service_account
            creds = service_account.Credentials.from_service_account_file(
                sa_file,
                scopes=scopes(),
            )
            credential_source = f"service account file {sa_file}"
            return creds
        except Exception as e:
            logger.error("Failed to load Service Account credentials from %s: %s", sa_file, e)

    return None

