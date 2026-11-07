# Internify 🚀

> **Autonomous AI Resume Tailoring Agent & Telegram Copilot**  
> Compiles pixel-perfect, ATS-optimized, 1-page LaTeX resumes grounded strictly in your verified career achievements.

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg?logo=python&logoColor=white)](https://www.python.org/)
[![Docker](https://img.shields.io/badge/Docker-Enabled-2496ED.svg?logo=docker&logoColor=white)](https://www.docker.com/)
[![Telegram](https://img.shields.io/badge/Telegram-Bot-26A5E4.svg?logo=telegram&logoColor=white)](https://telegram.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

---

## 💡 What is Internify?

When applying to dozens of software engineering roles, manually rewriting bullet points, shuffling technical skills, and reformatting LaTeX for every company is exhausting.

**Internify automates the entire process over Telegram:**
1. **Send a Job URL** to your Telegram bot.
2. Internify extracts the tech stack, hard requirements, and ATS keywords.
3. An **Agentic Retrieval & Generator-Critic Loop** selects and tailors only your real, verified achievements.
4. Tectonic renders and compiles a clean, **guaranteed 1-page LaTeX PDF** using **Jake's Resume** format.
5. You receive the tailored PDF in Telegram within seconds, automatically tracked in your local SQLite database (or synced to Google Sheets & Drive).

---

## 🖥️ Live Agent Dashboard

While the bot runs, open **http://localhost:8080/live**. It's a single picture that fits your browser window (no scrolling): a ring of nine seats, **Telegram** (you) at the top and eight agents around it. The Discoverer and Matcher are grey with a dotted outline while no daily check is running; the others are always ready. Send a job link in Telegram and watch the work move around the ring: documents fly along arrows that appear only when work is actually handed over, the active seat lights up in brand blue, and each one says what it's doing in plain words with the numbers. When the Archivist needs you ("do you have real experience with Kubernetes?"), each question goes to the Telegram seat in coral, and your answer travels back. The finished PDF returns to Telegram, which closes the loop.

| Seat | What it does |
|---|---|
| 💬 Telegram | You, on your phone: send the link, answer questions, receive the PDF |
| 🔎 Scout | Reads the job page, and finds another way in if it's blocked |
| 📄 Analyst | Works out what the job wants |
| 🗄 Archivist | Finds your matching experience, and asks you about gaps |
| ✏️ Scribe | Writes the resume lines |
| 🛡 Critic | Checks every line is true, and scores the match |
| 📦 Publisher | Builds the PDF (and repairs it if needed), files it, and logs it |
| 🧭 Discoverer | *Daily check:* visits your job pages each day and finds new postings |
| 🎯 Matcher | *Daily check:* compares the new postings against your experience and the jobs you sent lately, and keeps the best few |

- **Log** (top right): every success (blue ✓) and failure (coral ✕) in this run, with filters, plain-language reasons and a JSON download. Failures such as "the site blocked us (HTTP 403)" or "caught an invented claim" are listed with why.
- **Demo** (top right) plays a run with no keys: a normal run, a blocked page where the Scout finds another way in, and one where nothing works and the Scout asks you to paste the text, and **The daily job check** (the Discoverer and Matcher at work, then two resumes written without questions).
- Light by default (the brand's light canvas); the sun/moon button switches to the brand's dark mode.
- Every run is saved to `output/traces/<run_id>.json`. Replay one with `http://localhost:8080/live?replay=<run_id>` (add `&speed=2` to go faster).
- Branding (logo, colours, Inter font) lives in `bot/dashboard/static/assets/`. Inter is bundled under the SIL Open Font License.
- Offline checks (no keys needed): `python scripts/test_events.py` (agent events and wording), `python scripts/test_handler.py` (the Telegram flow with fake services), `python scripts/test_scout.py` (Scout's routes for blocked pages), `python scripts/test_import.py` (resume import), `python scripts/test_browser.py` (browsing and editing by tapping), `python scripts/test_ask.py` (the Archivist's questions), `python scripts/test_scan.py` (the daily job check), `python scripts/test_ai.py` (the AI helpers with a fake model), `python scripts/test_telegram_safe.py` (no message can fail on special characters), `python scripts/test_dashboard_access.py` (who can open what) and `python scripts/test_concurrency.py` (answers are received while a job runs).
- **It asks when it's unsure.** If a job wants a tool you have no saved experience for (say Kubernetes), the Archivist asks you in Telegram: *do you have real experience with it?* Say yes, pick where you used it and describe it in a sentence, and it's saved as a new bullet point (and a skill) for this resume and every later one. Say no, and the resume won't claim it. No reply within 2 minutes means skip. Turn it off with `ASK_ABOUT_MISSING_SKILLS=false`.
- **It finds another way in.** If a job page is blocked or empty, the Scout tries other routes and shows each attempt (and why it failed): the job data built into the page, a page-rendering service, a saved copy from the web archive, then the same posting on another site (accepted only if its text matches the job). If every route fails, it asks you to paste the job description. Telegram tells you when a copy, not the original page, was used. These routes send the (public) job link to those services; turn them off with `SCOUT_FALLBACKS=false`.
- Disable the dashboard with `DASHBOARD_ENABLED=false`; change the port with `DASHBOARD_PORT`.
- Put it on your own domain (home, privacy policy, terms and the private live dashboard): see [`docs/domain_setup.md`](docs/domain_setup.md).

---

## 🤖 Where AI is used

| Agent | Uses a model? |
|---|---|
| Scout | Plain web requests, plus a small AI check that the page is really one job posting (not a login or cookie page) and, as a last resort, whether another site's page is the same job |
| Analyst | `EXTRACTOR_MODEL` reads the posting |
| Archivist | Embeddings find your matching experience; missing skills are worked out in code |
| Scribe | `GENERATOR_MODEL` writes the lines |
| Critic | `CRITIC_MODEL` scores it, and plain code blocks any claim not in your saved facts |
| Publisher | Code (a model only repairs LaTeX if the PDF fails to compile) |
| Discoverer | Greenhouse and Lever are read directly; for any other career page a model lists the real openings (invented links are dropped) |
| Matcher | Embeddings and the extractor score each job; a model writes the one-sentence reason |

Every small helper falls back to plain rules if its model call fails.

## ☀️ The Daily Job Check (finds jobs for you)

Once a day the bot looks for jobs on its own, so you don't have to hunt:

1. **Discoverer** visits your *job pages*: career pages or job boards you added, plus the Greenhouse and Lever boards of companies you already sent links for. Greenhouse and Lever are read from their public listings; any other career page goes through ScrapingAnt (if you set a key) or a plain visit.
2. **Matcher** compares each new posting with your experience and with the jobs you sent lately, and keeps the best few (default 3) that clear a minimum match.
3. Each kept job goes through the same Scout → Analyst → Archivist → Scribe → Critic → Publisher team, and you get the PDFs in Telegram with a one-line reason ("Matches Python, FastAPI. Missing: Kubernetes."), then a short summary.

A check **never asks you questions** (you may be asleep): if a job needs a tool you haven't saved experience for, it says so. Send that link yourself to be asked.

Manage it in `/profile` → **🔭 Job pages I check**: add or remove pages, **Check now**, pause or resume. Settings in `.env`:

| Setting | Default | Meaning |
|---|---|---|
| `SCAN_ENABLED` | `true` | Turn the daily scan on or off |
| `SCAN_TIME` | `08:00` | Time of day (24h) |
| `SCAN_TIMEZONE` | `UTC` | e.g. `Asia/Kuala_Lumpur` |
| `SCAN_MAX_TAILORED` | `3` | Most resumes written per scan |
| `SCAN_MIN_FIT` | `0.5` | Minimum fit (0 to 1) to be kept |

The check sends results to the chat you last talked to the bot from, so send it any message once. It needs the `tzdata` package (already in `requirements.txt`; rebuild with `docker compose up --build`).

---

## 🏗️ Architecture

```
 You (Telegram) ──► send a job link ───────────────┐          Daily timer ──► Discoverer ──► Matcher ──┐
        ▲                                          ▼                                                   │
        │                                  ┌── one bot process ──────────────────────────────────────┤
        │                                  │   Scout ► Analyst ► Archivist ► Scribe ⇄ Critic ► Publisher
        │                                  │                                                         │
        └──── PDF, questions, summaries ◄──┤   saved experience (JSON) · tracker (SQLite, optional Google)
                                           └── live dashboard (same process) shows every hand-off
```

One process runs everything: `bot/` is Telegram, the dashboard and the daily check; `service/` is the pipeline
(`pipeline.py`), the agents (`agentic/`), your saved experience (`fact_store/`, `profile_data.py`, `editor.py`) and
the PDF builder (`latex/`). There is no separate web service.

### Key Highlights
- **Zero Hallucinations Guarantee**: Every bullet point generated cites a verified `fact_id` from your master fact store. The AI calibrates and highlights your true experience—it never invents jobs or skills.
- **Strict 1-Page Layout**: Work Experience (internships) and Leadership & Activities are kept distinct, with conditional section rendering that prevents whitespace overflow.
- **Self-Healing LaTeX Engine**: If Tectonic compilation encounters a LaTeX issue, an LLM diagnostic agent patches the TeX code automatically before retrying.
- **Zero Infrastructure Friction**: Works out-of-the-box locally with **SQLite**. No domain name, no SSL setup, no webhooks, and no Google Cloud required to start.

---

## ⚡ Quickstart (Clone & Run in 2 Minutes)

You only need **Docker** and two API keys: an **OpenAI API Key** and a **Telegram Bot Token**.

### 1. Clone the repository
```bash
git clone https://github.com/adyhafetz/internify.git
cd internify
```

### 2. Configure environment
```bash
cp .env.example .env
```
Open `.env` and fill in your keys:
```env
OPENAI_API_KEY=sk-...
TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
```
*(Tip: Get your Bot Token from [@BotFather](https://t.me/BotFather) on Telegram in 30 seconds).*

### 3. Launch with Docker Compose
```bash
docker compose up -d --build
```

That’s it! Open Telegram, message your bot `/start`, and you're good to go.

### 4. Fill in your experience: send your current resume

Don't type your experience in one entry at a time. **Send the bot your current resume as a file** (PDF, Word `.docx` or text, up to 5 MB):

1. The bot reads it and shows what it found: contact details, education, jobs, projects, leadership and skills.
2. You choose **Replace what's saved** (the right choice on a fresh install, which starts with a sample profile; a backup is kept in `service/fact_store/backups/`), **Add to what's saved** (keeps what you have and skips entries already saved), or **Cancel**.
3. Nothing is saved until you tap a button. Then check it with `/profile`.

Afterwards, `/profile` lets you fix anything by tapping, and the typed `/add...` commands below stay available for adding new entries. The AI is told to use only what is in your resume and to keep your wording, but always skim the preview: reading a PDF can mix up multi-column layouts. The file is read by your AI provider (OpenAI by default) and is not kept.

---

## 💻 Local Run vs. Cloud VPS

### Do I need a Cloud VPS?
**No.** Internify uses Telegram **Long Polling**. 
- It communicates *outbound* to Telegram's secure API.
- It runs seamlessly on your **local computer (Windows, macOS, Linux)** behind home Wi-Fi or NAT.
- No public IP address, domain name, or open ports required.
- If your computer goes to sleep, the bot pauses. When your computer wakes up, it resumes and catches up on any messages automatically.

### Running 24/7 on a VPS (Optional)
If you prefer the bot to run 24/7 in the cloud while your laptop is closed, deploy to any cheap \$3–\$5/month Linux VPS (Hetzner, DigitalOcean, Tencent Lighthouse, AWS EC2, etc.):
```bash
# On your remote server
git clone https://github.com/adyhafetz/internify.git
cd internify && cp .env.example .env
docker compose up -d
```
See our [Cloud VPS Deployment Guide](docs/vps_deployment_guide.md) for details.

---

## 💰 Cost & Models

Internify uses three OpenAI models, set in `.env`:

| Job | Model | List price per 1M tokens (input / output) |
|---|---|---|
| Reading postings (Analyst, Scout and Discoverer helpers, Matcher) | `gpt-5.4-nano` | $0.20 / $1.25 |
| Writing and checking (Scribe, Critic) | `gpt-5.4-mini` | $0.75 / $4.50 |
| Finding your matching experience | `text-embedding-3-small` | very low |

I haven't measured the cost per resume with these models, so check your usage on the OpenAI dashboard after the first few. To spend less, set `GENERATOR_MODEL=gpt-5.4-nano`; to write better, try a bigger model there.

---

## 📱 Telegram Bot Commands Reference

Everything on your resume is behind one command, `/profile`. Tap, don't type IDs or look anything up.

The slash menu shows these six:

| Command | What it does |
|---|---|
| `/profile` | **See and edit everything on your resume.** Contact details, education, jobs, projects, leadership and skills as buttons. Tap an entry to see its numbered bullet points, then tap ✏️ to edit, 🗑 to delete (it asks first) or ➕ to add a line. **➕ Add something** adds a whole job, project, leadership role or degree: you describe it in your own words and the AI turns it into bullet points. Skills are grouped by category: tap one to add (comma-separated) or remove. |
| `/importresume` | Fill in everything from your current resume. Send a PDF, Word or text file (or just send the file), check the preview, then choose Replace or Add. |
| `/history` | Jobs you've applied to, with match scores and links. |
| `/resume` | Download your general resume (PDF), not tailored to any job. |
| `/help` | Everything the bot can do. |
| `/cancel` | Stop the current step, for example a question the bot is waiting for you to answer. |

And the main action isn't a command: **send a job link** and you get a tailored 1-page PDF.

There are no other commands: everything else is a button inside `/profile`.

---

## 🌐 Job Scraping & Anti-Bot Bypass (ScrapingAnt)

Many modern enterprise career boards (Workday, Greenhouse, Lever, LinkedIn, Intel, etc.) use heavy anti-bot protection (Cloudflare, DataDome) or client-side JavaScript rendering that blocks standard HTTP requests.

Internify includes built-in support for **[ScrapingAnt](https://scrapingant.com/)**:
* **10,000 Free Credits/Month** (ample for hundreds of job postings per month).
* Automatically bypasses CAPTCHAs, Cloudflare walls, and renders JavaScript job descriptions.

### Setup (30 seconds):
1. Sign up for a free account at [scrapingant.com](https://scrapingant.com/).
2. Copy your API token from the dashboard.
3. Paste it into your `.env`:
   ```env
   SCRAPINGANT_API_KEY=your_scrapingant_api_token
   ```
*(Note: If left blank, Internify automatically falls back to built-in direct HTTP scraping with browser user-agent spoofing).*

---

## ☁️ Optional Cloud Sync (Google Drive & Google Sheets)

By default, Internify saves applications in **SQLite** (`output/internify.db`) and delivers PDFs directly to your Telegram chat.

If you want automated cloud backups to Google Drive and Google Sheets:

### 1. Copy the Official Application Tracker Sheet Template
We provide a pre-formatted, production-ready Google Sheet tracker complete with automated formulas and dropdown menus:
👉 **[Open Internify — Application Tracker Template](https://docs.google.com/spreadsheets/d/1ozOD9aYl43jPgfGXid_BOmQRXHy8Aiw_eT5JzSwgccQ/edit?usp=sharing)**

1. Open the link above, click **File → Make a copy** to save it to your own Google Drive.
2. Copy the **Spreadsheet ID** from your copied URL:
   ```text
   https://docs.google.com/spreadsheets/d/<YOUR_SPREADSHEET_ID>/edit
   ```
3. Set `GOOGLE_SHEET_ID=<YOUR_SPREADSHEET_ID>` in your `.env`.

#### Template Columns Explained:
| Column | Purpose | Managed By |
|---|---|---|
| **Company**, **Job Title**, **Location** | Extracted from posting | Internify |
| **Job Description**, **Requirements**, **Job Link** | Scraped source & audit link | Internify |
| **Status** | Application progress (`New`, `Applied`, `In Review`, `Interviewing`, `Offer`, `Rejected`, `Withdrawn`) | **You** (starts as `New`) |
| **Resume Status** | AI ATS evaluation score (`Excellent`, `Good`, `Fair`, `Weak`) | Internify |
| **Resume Link** | One-click web view link to the PDF in Google Drive | Internify |
| **Comment** | ATS match score & Critic feedback notes | Internify |

### 2. Google OAuth Credentials Setup
1. Obtain an OAuth 2.0 Client ID & Secret from [Google Cloud Console](https://console.cloud.google.com/) (Enable **Google Drive API** and **Google Sheets API**).
2. Authenticate once via OAuth2 or service account to obtain your refresh token.
3. Add the generated credentials to `.env`:
   ```env
   GOOGLE_CLIENT_ID=...
   GOOGLE_CLIENT_SECRET=...
   GOOGLE_REFRESH_TOKEN=...
   GOOGLE_SHEET_ID=your-google-sheet-id
   GOOGLE_SHEET_NAME=Tracker
   GOOGLE_DRIVE_FOLDER_ID=your-google-drive-folder-id
   ```
Internify will automatically dual-sync every application to both your local SQLite database and your Google Sheet/Drive.

---

## 🔒 Security & Privacy

To prevent unauthorized users on Telegram from generating resumes or modifying your fact store:
1. Message [`@userinfobot`](https://t.me/userinfobot) on Telegram to get your numeric user ID.
2. In `.env`, set:
   ```env
   TELEGRAM_ALLOWED_USER_IDS=123456789
   ```
Any messages from other users will be rejected with an `Access Denied` notification.

---

## 📄 License

This project is open-source under the [MIT License](LICENSE).
