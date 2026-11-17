from __future__ import annotations
import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

# Hosted mode (MODE=cloud) only: the maintainer's Telegram ID, who is told about problems with the server's AI key, and how many
# messages or taps one person may make per minute.
_raw_admin = os.getenv("ADMIN_TELEGRAM_ID", "").strip()
ADMIN_TELEGRAM_ID = int(_raw_admin) if _raw_admin.isdigit() else 0
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "30"))

# The public site. The bot's Telegram username (filled in from Telegram at start-up when not set), the project's address, the paid
# plan's price as shown on the pricing page, and MARKETING_SITE=1 to show the public home, pricing and how-it-works pages on a
# normal copy too (the hosted version always shows them).
BOT_USERNAME = os.getenv("PUBLIC_BOT_USERNAME", "").strip().lstrip("@")
PROJECT_URL = os.getenv("PROJECT_URL", "https://github.com/adyhafetz/internify").strip().rstrip("/")
PAID_PRICE = os.getenv("PAID_PRICE", "RM9.90").strip()
MARKETING_SITE = os.getenv("MARKETING_SITE", "").strip().lower() in ("1", "true", "yes")

# Hosted mode: what each plan includes (resumes a month), the most resumes one daily check writes, a monthly ceiling on AI calls for the
# whole server (0 = no ceiling), when to remind people their plan is ending, and what /plan tells people about how to pay.
FREE_RESUMES = int(os.getenv("FREE_RESUMES", "3"))
PAID_RESUMES = int(os.getenv("PAID_RESUMES", "30"))
PAID_SCAN_MAX = int(os.getenv("PAID_SCAN_MAX", "3"))
AI_MONTHLY_BUDGET_CALLS = int(os.getenv("AI_MONTHLY_BUDGET_CALLS", "0"))
PLAN_REMINDER_DAYS = int(os.getenv("PLAN_REMINDER_DAYS", "3"))
PAY_INSTRUCTIONS = os.getenv("PAY_INSTRUCTIONS", "").strip() or "To upgrade, message the person who runs this bot."

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
# Hosted mode: people connect their own Google from /profile. GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are then a "Web application"
# client (redirect address: PUBLIC_URL/connect/google/callback), and each person's login is stored encrypted with this key
# (make one with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"). Keep it safe: it can't be recovered.
GOOGLE_TOKEN_KEY = os.getenv("GOOGLE_TOKEN_KEY", "").strip()
# "app": the login can only open files Internify created itself (the setup page's folder and sheet). "full": all Drive and
# Sheets files. A new Google sign-in from the setup page saves "app". A copy set up earlier has no value here and keeps "full",
# so nothing stops working until you choose to move.
GOOGLE_ACCESS = os.getenv("GOOGLE_ACCESS", "full").strip().lower()


# Live dashboard (served from the bot process; shows the agents working in real time)
DASHBOARD_ENABLED = os.getenv("DASHBOARD_ENABLED", "true").strip().lower() in ("1", "true", "yes")
DASHBOARD_HOST = os.getenv("DASHBOARD_HOST", "127.0.0.1").strip()
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "8080"))
# Shown on the privacy page as the way to reach the person running this site (leave empty to point to GitHub issues).
CONTACT_EMAIL = os.getenv("CONTACT_EMAIL", "").strip()
# The public https address of your website (for example https://internify.yourdomain.com). Used for the dashboard button
# in /profile → My links. Leave empty if you only use the dashboard on your own computer.
PUBLIC_URL = os.getenv("PUBLIC_URL", "").strip().rstrip("/")
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
