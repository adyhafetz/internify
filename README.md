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

When you open the bot, click the native **`Menu`** button or type `/` to access all commands with autocompletion:

| Category | Command | Description |
|---|---|---|
| **Start** | `/start` | Welcome message & quick overview |
| | `/help` | Detailed command cheatsheet |
| **Tracker** | `/history` | View recent applications tracked in SQLite |
| **Master Resume**| `/resume` | Compile full untailored master resume PDF |
| **Profile** | `/myprofile` | Interactive contact card with one-click edit buttons |
| | `/setname [name]` | Update full name (prompted interactively if blank) |
| | `/setphone [phone]` | Update phone number |
| | `/setemail [email]` | Update email address |
| | `/setlinkedin [url]` | Update LinkedIn profile URL |
| | `/setgithub [url]` | Update GitHub profile URL |
| **Education** | `/listeducation` | List all education entries & courses |
| | `/addeducation [text]` | Add education details (AI-extracted from plain English) |
| | `/deleteeducation` | Delete an education entry (with interactive buttons) |
| | `/addcourse [id] [name]`| Add relevant coursework to a degree |
| | `/deletecourse [id] [name]`| Remove coursework from a degree |
| **Work Experience**| `/addexperience [text]` | Add work experience/internship (auto-extracts bullets & tech tags) |
| | `/deleteexperience` | Delete experience entry (with interactive buttons) |
| **Projects** | `/addproject [text]` | Add project (auto-extracts bullets & tech stack) |
| | `/deleteproject` | Delete project (with interactive buttons) |
| **Leadership** | `/addleadership [text]` | Add extracurricular or leadership role |
| | `/deleteleadership` | Delete leadership entry |
| **Skills & Facts** | `/listskills` | View master technical skills grouped by category |
| | `/addskill [text]` | Add new skills (automatically categorized) |
| | `/deleteskill [name]` | Remove a skill |
| | `/listentries` | View all entry IDs for experiences & projects |
| | `/listfacts [id]` | View all verified fact bullets for an entry |
| | `/updatefact [id] [text]`| Edit bullet text (auto re-indexes embeddings) |
| | `/deletefact [id]` | Delete a fact bullet |
| **General** | `/cancel` | Cancel any active multi-step prompt |
| **Tailor** | *Paste any Job URL* | Automatically scrapes, tailors, compiles, and sends PDF |

---

## ☁️ Optional Cloud Sync (Google Drive & Google Sheets)

By default, Internify saves applications in **SQLite** (`output/internify.db`) and delivers PDFs directly to your Telegram chat.

If you want automated cloud backups:
1. Obtain an OAuth 2.0 Client ID & Secret from [Google Cloud Console](https://console.cloud.google.com/).
2. Run our one-command setup script to authenticate:
   ```bash
   python scripts/get_gdrive_token.py
   ```
3. Add the generated credentials to `.env`:
   ```env
   GOOGLE_CLIENT_ID=...
   GOOGLE_CLIENT_SECRET=...
   GOOGLE_REFRESH_TOKEN=...
   GOOGLE_SHEET_ID=your-google-sheet-id
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
