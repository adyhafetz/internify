from __future__ import annotations
import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

# Optional comma-separated Telegram User IDs to restrict access
_raw_allowed = os.getenv("TELEGRAM_ALLOWED_USER_IDS", "").strip()
ALLOWED_USER_IDS: set[int] = {
    int(uid.strip()) for uid in _raw_allowed.split(",") if uid.strip().isdigit()
}

# Scraping
SCRAPINGANT_API_KEY = os.getenv("SCRAPINGANT_API_KEY", "").strip()

# Google Cloud Integrations
GOOGLE_SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json").strip()
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "").strip()
GOOGLE_SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "Tracker").strip()
GOOGLE_DRIVE_FOLDER_ID = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "").strip()

# Google Drive OAuth2 (Required for personal @gmail.com accounts to avoid 0-byte quota error)
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "").strip()
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
GOOGLE_REFRESH_TOKEN = os.getenv("GOOGLE_REFRESH_TOKEN", "").strip()
GOOGLE_TOKEN_FILE = os.getenv("GOOGLE_TOKEN_FILE", "token.json").strip()


# Live dashboard (served from the bot process; shows the agents working in real time)
DASHBOARD_ENABLED = os.getenv("DASHBOARD_ENABLED", "true").strip().lower() in ("1", "true", "yes")
DASHBOARD_HOST = os.getenv("DASHBOARD_HOST", "127.0.0.1").strip()
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "8080"))
# Key that protects the live dashboard when it is reachable from the internet (empty = open, fine on localhost).
# Open https://<your-domain>/live?key=<DASHBOARD_KEY> once; the browser then remembers it.
DASHBOARD_KEY = os.getenv("DASHBOARD_KEY", "").strip()

# Archivist asks you (in Telegram) about tools the job wants that are missing from your saved experience.
ASK_ABOUT_MISSING_SKILLS = os.getenv("ASK_ABOUT_MISSING_SKILLS", "true").strip().lower() in ("1", "true", "yes")
ASK_TIMEOUT_SECONDS = int(os.getenv("ASK_TIMEOUT_SECONDS", "120"))

# Scout: if the job page can't be read directly, try other routes (a page-rendering service, a saved copy from the
# web archive, the same posting on another site) and finally ask you to paste the text. Off = only direct routes.
SCOUT_FALLBACKS = os.getenv("SCOUT_FALLBACKS", "true").strip().lower() in ("1", "true", "yes")
SCOUT_BUDGET_SECONDS = int(os.getenv("SCOUT_BUDGET_SECONDS", "150"))

# The daily job check: finds new jobs on the sources you watch, picks the best fits, tailors a resume for each.
SCAN_ENABLED = os.getenv("SCAN_ENABLED", "true").strip().lower() in ("1", "true", "yes")
SCAN_TIME = os.getenv("SCAN_TIME", "08:00").strip()                 # HH:MM, in SCAN_TIMEZONE
SCAN_TIMEZONE = os.getenv("SCAN_TIMEZONE", "UTC").strip()           # for example Asia/Kuala_Lumpur
SCAN_MAX_TAILORED = int(os.getenv("SCAN_MAX_TAILORED", "3"))        # resumes written per scan
SCAN_MIN_FIT = float(os.getenv("SCAN_MIN_FIT", "0.5"))              # 0 to 1: how well a job must fit to get a resume
