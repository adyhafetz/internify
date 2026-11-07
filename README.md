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

While the bot runs, open **http://localhost:8080/live**. Send a job link in Telegram and watch six named agents work in real time, while the same progress streams into the chat:

| Agent | Job |
|---|---|
| 🔭 Scout | Reads the job page |
| 🧠 Analyst | Extracts stack, requirements, ATS keywords |
| 📚 Archivist | Retrieves your verified facts, flags requirements with no evidence |
| ✍️ Scribe | Writes tailored bullets, each tied to a fact |
| 🛡️ Critic | Verifies every claim and scores ATS fit, sends notes back to Scribe |
| 📎 Publisher | Compiles the PDF (self-repairing LaTeX), uploads to Drive, logs the tracker |

- **It asks when it's unsure.** If a job wants a tool you have no saved experience for (say Kubernetes), the Archivist asks you in Telegram: *do you have real experience with it?* Say yes, pick where you used it and describe it in a sentence, and it's saved as a new bullet point (and a skill) for this resume and every later one. Say no, and the resume won't claim it. No reply within 2 minutes means skip. Turn it off with `ASK_ABOUT_MISSING_SKILLS=false`.
- **It finds another way in.** If a job page is blocked or empty, the Scout tries other routes and shows each attempt (and why it failed) on the dashboard and in Telegram: the job data built into the page, a page-rendering service, a saved copy from the web archive, then the same posting on another site (accepted only if its text matches the job). If every route fails, it asks you to paste the job description. Telegram tells you when a copy, not the original page, was used. These routes send the (public) job link to those services; turn them off with `SCOUT_FALLBACKS=false`. Try **Demo: blocked page** and **Demo: paste the text** on the dashboard.
- Hand-offs between agents are drawn live, with a count for repeated loops such as Scribe ⇄ Critic.
- **No keys needed to try it:** click **Demo run** (or **Demo: blocked page**) on the dashboard.
- Every run is saved to `output/traces/<run_id>.json`. Replay one with `http://localhost:8080/live?replay=<run_id>` (add `&speed=2` to go faster).
- Offline checks (no keys needed): `python scripts/test_events.py` (agent events and wording) `python scripts/test_handler.py` (the Telegram flow with fake services) and `python scripts/test_dashboard_access.py` (who can open what), `python scripts/test_scout.py` (Scout's routes for blocked pages), `python scripts/test_import.py` (resume import and the manual commands), `python scripts/test_browser.py` (browsing and editing by tapping), `python scripts/test_telegram_safe.py` (no message can fail on special characters), `python scripts/test_ask.py` (the Archivist's questions) and `python scripts/test_concurrency.py` (answers are received while a job runs).
- Disable with `DASHBOARD_ENABLED=false`; change the port with `DASHBOARD_PORT`.
- Put it on your own domain (home, privacy policy, terms and the private live dashboard): see [`docs/domain_setup.md`](docs/domain_setup.md).

---

## 🏗️ Architecture

```
[ Telegram Chat ] ──► [ Python Telegram Bot (Long Polling) ]
                               │
            ┌──────────────────┴──────────────────┐
            ▼                                     ▼
 [ Conversational FSM & CRUD ]         [ Job URL Processing ]
  (/set*, /add*, /history, etc.)                  │
            │                                     ▼
            ▼                           [ 1. Web Scraping & AI Normalization ]
 [ Fact Store Management ]                        │
 (Facts, Experience, Skeleton)                    ▼
            │                           [ 2. Fact Retrieval (Embeddings + Cosine) ]
            │                                     │
            │                                     ▼
            │                           [ 3. Agentic Loop (Generator & Critic) ]
            │                                     │
            │                                     ▼
            │                           [ 4. LaTeX Render & Tectonic Compile ]
            │                                     │
            ▼                                     ▼
   [ Fact Store JSON ] ───────────────► [ 1-Page Tailored PDF ]
                                                  │
                                                  ▼
                               [ SQLite DB (Default) / Google Cloud Sync ]
```

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

## 💰 Cost & Model Tuning

Internify is engineered for extreme cost efficiency:
* **Extractor**: `gpt-4o-mini` (~$0.0005 per run)
* **Critic**: `gpt-4o-mini` (~$0.0005 per run)
* **Generator**: `gpt-4o` (~$0.01 per run)

**Total cost per tailored resume is roughly 1 to 2 cents (\$0.01–\$0.02).**

### Running for less than a fraction of a cent (<$0.002):
Change the generator model in `.env`:
```env
GENERATOR_MODEL=gpt-4o-mini
```

---

## 📱 Telegram Bot Commands Reference

Everything on your resume is behind one command, `/profile`. Tap, don't type IDs.

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

Typing shortcuts, if you prefer them to buttons (not in the menu):

| Shortcut | What it does |
|---|---|
| `/profile acme` | Jump straight to one entry by name (any word, any case). `/listfacts acme` does the same. |
| `/addexperience`, `/addproject`, `/addleadership`, `/addeducation` | Add an entry. Put the description after the command to save at once, or send the command alone and the bot asks. |
| `/addskill Docker, Kubernetes` | Add skills (the AI picks the category). |
| `/myprofile`, `/listskills`, `/listeducation`, `/listentries`, `/experience` | Open the matching part of `/profile`. |

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
