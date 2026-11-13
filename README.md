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
5. You receive the tailored PDF in Telegram within seconds, with how many of the job's requirements it shows, what was used and left out, and buttons to track it: **✅ I applied**, then 🎤 Interview, 🏆 Offer or ❌ Rejected. Everything is kept in your local SQLite database (or synced to Google Sheets & Drive).

Behind the bot, **9 AI agents** share the work: the **Scout, Analyst, Archivist, Scribe, Critic and Publisher** write each resume; the **Discoverer and Matcher** find and pick new jobs for you every day; and the **Curator** keeps your saved experience tidy and in step with your GitHub repos.

---

## 🖥️ Live Agent Dashboard (optional)

The bot works fully without it: everything happens in Telegram. The dashboard is only for watching the agents work (handy for a demo). On your own computer it needs no key: open it at `localhost`. A key (`DASHBOARD_KEY`) matters only if you put it on the internet with your own domain.

While the bot runs, open **http://localhost:8080/live** (also reachable as `/dashboard`; if a key is set it asks for it). It's a single picture that fits your browser window (no scrolling): a ring of ten seats, **Telegram** (you) at the top and nine agents around it. The Discoverer and Matcher are grey with a dotted outline unless a daily check is running, and the Curator unless a tidy-up is running; the others are always ready. Send a job link in Telegram and watch the work move around the ring: documents fly along arrows that appear only when work is actually handed over, the active seat lights up in brand blue, and each one says what it's doing in plain words with the numbers. When the Archivist needs you ("do you have real experience with Kubernetes?"), each question goes to the Telegram seat in coral, and your answer travels back. The finished PDF returns to Telegram, which closes the loop.

| Seat | What it does |
|---|---|
| 💬 Telegram | You, on your phone: send the link, answer questions, receive the PDF |
| 🔎 Scout | Reads the job page, and finds another way in if it's blocked |
| 📄 Analyst | Works out what the job wants |
| 🗄 Archivist | Finds your matching experience; checks your GitHub for missing tools, and asks you about what it can't find |
| ✏️ Scribe | Writes the resume lines |
| 🛡 Critic | Compares every line with the fact it came from (no new tool, number or name), and scores the match |
| 📦 Publisher | Builds the PDF (and repairs it if needed), files it, and logs it |
| 🧭 Discoverer | *Daily check:* reads your job pages and searches the web for postings that match what you look for |
| 🎯 Matcher | *Daily check:* compares the new postings against your experience and the jobs you sent lately, and keeps the best few |
| ✨ Curator | *Tidy-up:* fixes repeats and spellings, adds tools from your GitHub with a bullet point, and suggests removals and rewordings for you to approve |

- **Log** (top right): every success (blue ✓) and failure (coral ✕) in this run, with filters, plain-language reasons and a JSON download. Failures such as "the site blocked us (HTTP 403)" or "caught an invented claim" are listed with why.
- **Demo** (top right) plays a run with no keys: a normal run, a blocked page where the Scout finds another way in, and one where nothing works and the Scout asks you to paste the text, **The daily job check** (the Discoverer and Matcher at work, then two resumes written without questions), and **A tidy-up** (the Curator at work).
- Light by default (the brand's light canvas); the sun/moon button switches to the brand's dark mode.
- Every run is saved to `output/traces/<run_id>.json`. Replay one with `http://localhost:8080/live?replay=<run_id>` (add `&speed=2` to go faster).
- Branding (logo, colours, Inter font) lives in `bot/dashboard/static/assets/`. Inter is bundled under the SIL Open Font License.
- Offline checks (no keys needed; they also run automatically on every pull request, see `.github/workflows/tests.yml`): `python scripts/test_events.py` (agent events and wording), `python scripts/test_handler.py` (the Telegram flow with fake services), `python scripts/test_scout.py` (Scout's routes for blocked pages), `python scripts/test_import.py` (resume import), `python scripts/test_browser.py` (browsing and editing by tapping), `python scripts/test_ask.py` (the Archivist's questions), `python scripts/test_scan.py` (the daily job check), `python scripts/test_ai.py` (the AI helpers with a fake model), `python scripts/test_quality.py` (requirement matching, grounding, the score, retrieval), `python scripts/test_tracker.py` (statuses, reminders, repeat jobs, rebuilding), `python scripts/test_setup.py` (the setup page), `python scripts/test_latex.py` (the resume template), `python scripts/test_fit.py` (fitting one page), `python scripts/test_github.py` (GitHub evidence), `python scripts/test_curator.py` (tidy-up), `python scripts/test_telegram_safe.py` (no message can fail on special characters), `python scripts/test_dashboard_access.py` (who can open what) and `python scripts/test_concurrency.py` (answers are received while a job runs).
- **The resume comes first, questions after.** If a job wants a tool you have no saved experience for (say Kubernetes), you still get the resume right away, with a **🧩 I know Kubernetes: rebuild** button under it. Tap it and the Archivist asks: *do you have real experience with it?* Say yes, pick where you used it and describe it in a sentence, and it's saved as a new bullet point (and a skill) and the resume is rebuilt with it. Say no, and the resume won't claim it. Turn the button off with `ASK_ABOUT_MISSING_SKILLS=false`.
- **It finds another way in.** If a job page is blocked or empty, the Scout tries other routes and shows each attempt (and why it failed): the job data built into the page, a page-rendering service, a saved copy from the web archive, then the same posting on another site (accepted only if its text matches the job). If every route fails, it asks you to paste the job description. Telegram tells you when a copy, not the original page, was used. These routes send the (public) job link to those services; turn them off with `SCOUT_FALLBACKS=false`.
- Disable the dashboard with `DASHBOARD_ENABLED=false`; change the port with `DASHBOARD_PORT`.
- Put it on your own domain (home, privacy policy, terms and the private live dashboard): see [`docs/domain_setup.md`](docs/domain_setup.md).

---

## 🤖 Where AI is used

| Agent | Uses a model? |
|---|---|
| Scout | Plain web requests, plus a small AI check that the page is really one job posting (not a login or cookie page) and, as a last resort, whether another site's page is the same job |
| Analyst | `EXTRACTOR_MODEL` reads the posting |
| Archivist | Embeddings find your matching experience (every job or internship keeps its best two bullet points, and bullet points from resumes that got you an interview get a small nudge); missing skills are worked out in code, by tool name and its other names ("Postgres" finds "PostgreSQL"). Before asking you about a missing tool it checks your public GitHub repos (`GITHUB_USERNAME`): a tool used there is added to your skills together with one grounded bullet point (under the matching project, or a new project made from that repo), with no question. If no bullet point can be written, nothing is added and you are asked as usual |
| Scribe | `GENERATOR_MODEL` writes the lines |
| Critic | Code compares every line with its fact (a new tool, number or name sends it back for a rewrite, and anything still added is put back to your own words before the PDF is built) and counts which of the job's requirements the resume shows; `CRITIC_MODEL` gives its opinion and rewrite notes. The score you see is half each |
| Publisher | Code (a model only repairs LaTeX if the PDF fails to compile) |
| Discoverer | Boards on Greenhouse, Lever, Ashby, SmartRecruiters and Workable are read through their APIs; the web search is plain code; for any other career page a model lists the real openings (invented links are dropped) |
| Matcher | Embeddings and the extractor score each job; a model writes the one-sentence reason |
| Curator | Code does the safe fixes; a model reviews every bullet point (reword or remove, checked by code that nothing is added), drafts GitHub bullet points, and settles whether a tool is already covered by a broader skill |

Every small helper falls back to plain rules if its model call fails.

## ⚙️ Status

`/profile` → **⚙️ Status** shows, in plain words, how this copy is set up (AI service and models, whether only you can use it, GitHub username and how fresh its repo list is, Google, ScrapingAnt, dashboard) and when the daily job check and the tidy-up next run and last ran. **🔍 Test connections** checks the AI key and Google live. Useful when you run more than one copy.

## 🧹 Tidy up (the Curator)

`/profile` → **🧹 Tidy up**, every Sunday, and after each resume import, the Curator checks your saved experience.
- **Fixed automatically** (with a backup): the same skill listed twice or under two spellings ("React" and "React.js"), tag spellings, and a bullet point saved twice (exact duplicate).
- **From your GitHub** (with `GITHUB_USERNAME` set): tools your public repos use that your skills don't list are added automatically, each with one bullet point that shows it; a repo that isn't one of your projects yet becomes one. Repos changed in the last two years only, never forks or your profile repo.
- **Suggested, for you to ✅ or ✕:** things in your skills that aren't technical skills ("Teamwork"), a skill in the wrong category, tools your bullet points mention that your skills don't (a tool listed inside a broader skill such as "LLM APIs (OpenAI, Gemini)" counts, and the AI settles unclear cases), near-duplicate bullet points, bullet points that aren't needed (too vague to tell a recruiter anything, or saying the same as another bullet point of that entry, in other words), tighter wording for long or weak bullet points and for **every** project bullet point (each one is reviewed), and, from your repos, a project's tech stack line and bullet points updated to match what the repo shows. A rewrite may only use facts from the old bullet point or the repo.
- **Skipped once, gone for good:** a suggestion you ✕ isn't suggested again, until the bullet point or skill it is about changes.
- **Backups:** one is made before every tidy-up, import and undo; only the newest 20 are kept.
- **Undo:** each "Added from your GitHub" line has a ✕ to take it back (its bullet point, a project made for it, and the skill if nothing else needs it), and **↩ Undo last change** puts your saved experience back the way it was before the last tidy-up or import (what you have now is backed up first).

## ☀️ The Daily Job Check (finds jobs for you)

Once a day the bot looks for jobs on its own, so you don't have to hunt:

1. **Discoverer** finds new postings in three ways:
   - **Company job boards** on Greenhouse, Lever, Ashby, SmartRecruiters and Workable are read through their public job-listing APIs (clean data, no scraping). These are found for you: from the jobs you sent, from the web search, and from career pages (a career page that is really one of these boards is switched to its API automatically).
   - **The web search** (on by default, no key needed) searches for the roles and places you look for on the hiring systems companies post on, and adds each company it finds to your job pages (marked "found by search"). So the list of companies grows by itself; remove any with ✕ and it isn't added back.
   - **Any other page** you add (a career page, or a job board such as JobStreet from 🇲🇾 Popular in Malaysia) is read through ScrapingAnt (if you set a key) or a plain visit, and the job links are picked out.
2. **Your filters** come next, before anything is read in full: **internships only** (on by default), and your **places** (default Malaysia and Remote). After reading, jobs that have closed, are in another place, or are the same job as one you already have a resume for (even through another link) are skipped too.
3. **Matcher** compares each remaining posting with your experience and with the jobs you sent lately, and keeps the best few (default 3) that clear a minimum match.
4. Each kept job goes through the same Scout → Analyst → Archivist → Scribe → Critic → Publisher team, and you get the PDFs in Telegram with a one-line reason ("Matches Python, FastAPI. Missing: Kubernetes."), then a short summary.

A check **never asks you questions** (you may be asleep): a job that needs a tool you haven't saved experience for gets the 🧩 button under its resume.

Manage it in `/profile` → **🔭 Job pages I check**: **🎯 What I look for** (roles, places, internships only, web search on or off; with no roles set, the roles on your resume are used), every page with its link and what happened on the last visit, **➕ Add a link** or **🇲🇾 Popular in Malaysia**, remove one with its **✕**, **Check now**, or pause and resume. **🔗 My links** (also in `/profile`) opens your dashboard, tracker sheet and Drive folder with one tap; the dashboard button needs `PUBLIC_URL` (your website address) in `.env`. Settings (the setup page sets the time and timezone; the others go in `.env`):

| Setting | Default | Meaning |
|---|---|---|
| `SCAN_ENABLED` | `true` | Turn the daily scan on or off |
| `SCAN_TIME` | `08:00` | Time of day (24h) |
| `SCAN_TIMEZONE` | `UTC` | e.g. `Asia/Kuala_Lumpur` |
| `SCAN_MAX_TAILORED` | `3` | Most resumes written per scan |
| `SCAN_MIN_FIT` | `0.5` | Minimum fit (0 to 1) to be kept |

The web search reads Bing's (then DuckDuckGo's) public results pages; a day when they don't answer just means no extra jobs. The check sends results to the chat you last talked to the bot from, so send it any message once. It needs the `tzdata` package (already in `requirements.txt`; rebuild with `docker compose up --build`).

## 📊 Tracking your applications

Every resume arrives with buttons: **✅ I applied** · **🚫 Not applying** · **📝 What changed** · and **🧩** when the job wants a tool you haven't saved.

- **What it says:** the match score (half the AI reviewer's opinion, half how many of the job's requirements the finished PDF visibly shows), "Shows 7 of 10 requirements. Not on it: Docker, SQL", how many bullet points were used and reworded, what was left out, and the closing date if the posting gives one.
- **📝 What changed:** every bullet point used (✏️ marks the reworded ones), what was left out, and **➕ Keep …** to rebuild with a left-out job or project kept.
- **After you apply:** 🎤 Interview, 🏆 Offer, ❌ Rejected, 👻 No reply. Your Google Sheet's **Status** column follows (add **Applied Date** or **Deadline** headers to row 1 to get those too).
- **Reminders:** an hour after the daily check: "Any news from …?" `FOLLOWUP_DAYS` (default 14) after you applied (⏳ Still waiting asks again a week later), and a nudge when a job you have a resume for closes within 3 days and isn't marked applied. On Sundays, a short summary of your week.
- **It learns:** bullet points on resumes that got you an interview or an offer get a small nudge up in later jobs.
- **Repeat jobs:** the same job through another link (LinkedIn, JobStreet and the company page) is recognised by company and title: when you send it, you're asked before a second resume is made; the daily check skips it.
- `/applied` lists everything by status (Open, Interviews and offers, Closed, All), each with the same buttons and ♻️ Rebuild (from the saved job details, no page visit; your status is kept).

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
- **Zero Hallucinations Guarantee**: Every bullet point cites a verified `fact_id` from your master fact store, and code compares its wording with that fact: a tool, number or name the fact doesn't have is sent back for a rewrite, and anything still added is put back to your own words before the PDF is built.
- **Every job stays on**: each job or internship keeps at least its two best bullet points, and when trimming to one page, projects and spare bullet points go before a whole job.
- **One page, measured**: after building the PDF, Internify counts the pages. If it runs over, it leaves out leadership that doesn't relate to the job, tightens long bullets (a code check refuses any rewrite that adds a number, tool or name), then leaves out the least relevant bullets and projects (a whole job only as a last resort), and finally trims courses and skills. Your saved profile is never changed, and the Telegram message says what was trimmed. If it still can't fit, it says so.
- **Strict 1-Page Layout**: Work Experience and Leadership & Activities stay distinct, and a section with nothing in it is not printed.
- **Self-Healing LaTeX Engine**: If Tectonic compilation encounters a LaTeX issue, an LLM diagnostic agent patches the TeX code automatically before retrying.
- **Zero Infrastructure Friction**: Works out-of-the-box locally with **SQLite**. No domain name, no SSL setup, no webhooks, and no Google Cloud required to start.

---

## ⚡ Quickstart (about 10 minutes, no files to edit)

You need **Docker** and two things: an **AI key** and a **Telegram bot**. A setup page asks for them, tests each one, and writes the settings file for you. It also explains how to get each key, so you can start with nothing.

### 1. Install Docker and download Internify
- Install [Docker Desktop](https://docs.docker.com/get-docker/) and **start it** (wait until it says it is running).
- Download this project: `git clone https://github.com/adyhafetz/internify.git`, or press **Code → Download ZIP** on GitHub and unzip it.

### 2. Run the setup
- **Windows:** double-click `setup.bat` in the project folder.
- **Mac or Linux:** open a terminal in the project folder and run `./setup.sh`.

Your browser opens the setup page at `http://localhost:8765`. Choose **I already have my keys** or **Help me get them**, then:

1. **Choose your AI:** [OpenAI](https://platform.openai.com/api-keys) (best quality, pay as you go) or Google Gemini (free tier, experimental; on the free tier Google may use what you send to improve its products). Paste the key and press **Check key**. Models are chosen for you from what your account offers.
2. **Your Telegram bot:** make one with [@BotFather](https://t.me/BotFather) (`/newbot`), paste its token, then press Start in your bot and send "hi". The page finds **your Telegram ID** itself, so only you can use the bot.
3. **Extras (all optional):** the time and timezone of the daily job check, a [ScrapingAnt](https://scrapingant.com/) key for blocked job pages, and (under Advanced) a dashboard key and contact email. You do **not** need the dashboard key on your own computer; it matters only if you put the dashboard on the internet.
4. **Google Drive and Sheets (optional):** the page walks you through creating a Google project, then you press **Connect Google** and **Create my folder and sheet**. It signs you in, makes a Drive folder and a tracker spreadsheet with the right columns, and saves the login, so there is no refresh token to copy. Skip it and resumes still arrive in Telegram.
5. **Save and start Internify.** The setup page closes by itself and Internify starts.

Everything stays on your computer. The page only writes a `.env` file next to the project (your old one is kept as `.env.bak`) and talks to the services your keys belong to.

### 3. Say hello
Open Telegram, find your bot and send `/start`.

> **Keep it running.** The bot only works while Docker is running it. If you close Docker, shut the laptop or it goes to sleep, the bot is offline: messages wait and are answered when it comes back, but **the daily job check will not run** while it is off (it does not catch up on a missed time). Want it always on? Run it on a small server: see the [VPS guide](docs/vps_deployment_guide.md).

<details><summary>Prefer to do it by hand, or don't use Docker?</summary>

```bash
cp .env.example .env        # then edit it: OPENAI_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_USER_IDS
docker compose up -d --build
```
Without Docker (needs Python 3.11, [Tectonic](https://tectonic-typesetting.github.io/) and qpdf installed): `pip install -r requirements.txt`, then `python -m bot.setup` (the same setup page), then `python -m bot.main`.
To change settings later, run `./setup.sh` again: anything you leave blank keeps its current value.
</details>

### 4. Fill in your experience: send your current resume

Don't type your experience in one entry at a time. **Send the bot your current resume as a file** (PDF, Word `.docx` or text, up to 5 MB):

1. The bot reads it and shows what it found: contact details, education, jobs, projects, leadership and skills.
2. You choose **Replace what's saved** (the right choice on a fresh install, which starts with a sample profile; a backup is kept in `output/data/backups/`), **Add to what's saved** (keeps what you have and skips entries already saved), or **Cancel**.
3. Nothing is saved until you tap a button. Then check it with `/profile`.

Afterwards, `/profile` lets you fix anything by tapping and add new entries. The AI is told to use only what is in your resume and to keep your wording, but always skim the preview: reading a PDF can mix up multi-column layouts. The file is read by your AI provider (OpenAI by default) and is not kept.

### 5. Let it find jobs for you (optional)

Open `/profile` → **🔭 Job pages I check**. Tap **🇲🇾 Popular in Malaysia** to add well-known job pages, or **➕ Add a link** for any career page or job board (Greenhouse and Lever boards work best). Tap **Check now** to try it and watch it on the dashboard. After that it runs every day at `SCAN_TIME`. Details: [The Daily Job Check](#️-the-daily-job-check-finds-jobs-for-you).

### 6. Put the dashboard on your own subdomain (optional)

Home page, privacy policy, terms and the private dashboard on `https://internify.yourdomain.dev`, plus moving Google from Testing to Production and getting a non-expiring refresh token: [`docs/domain_setup.md`](docs/domain_setup.md).

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
A server has no browser, so run the setup page on your own computer first and copy the settings file across:
```bash
# On the server
git clone https://github.com/adyhafetz/internify.git
# On your computer, after ./setup.sh or setup.bat has written .env
scp .env you@your-server:~/internify/.env
# On the server
cd internify && docker compose up -d --build
```
See our [Cloud VPS Deployment Guide](docs/vps_deployment_guide.md) for details.

---

## 💰 Cost & Models

With **OpenAI**, the setup page picks these models (falling back to older ones your account has, such as `gpt-4o-mini` and `gpt-4o`):

| Job | Model | List price per 1M tokens (input / output) |
|---|---|---|
| Reading postings (Analyst, Scout and Discoverer helpers, Matcher) | `gpt-5.4-nano` | $0.20 / $1.25 |
| Writing and checking (Scribe, Critic) | `gpt-5.4-mini` | $0.75 / $4.50 |
| Finding your matching experience | `text-embedding-3-small` | very low |

I haven't measured the cost per resume with these models, so check your usage on the OpenAI dashboard after the first few, and set a monthly limit there. To spend less, set `GENERATOR_MODEL=gpt-5.4-nano`; to write better, try a bigger model there.

**Gemini (free tier)** is experimental: it works through Google's OpenAI-compatible endpoint and the setup page picks the newest Flash models from your account. I could not test it against the real service, so try one job first. Free-tier rate limits are low, so the daily job check may be slow, and Google may use free-tier content to improve its products.

---|---|---|
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
| `/applied` | Your applications by status, with buttons: applied, interview, offer, rejected, rebuild, what changed (`/history` still works). |
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
3. Paste it into the setup page (Extras), or into your `.env` as `SCRAPINGANT_API_KEY=...`.
*(Note: If left blank, Internify automatically falls back to built-in direct HTTP scraping with browser user-agent spoofing).*

---

## ☁️ Optional Cloud Sync (Google Drive & Google Sheets)

By default, Internify saves applications in **SQLite** (`output/internify.db`) and delivers PDFs directly to your Telegram chat.

If you want automated cloud backups to Google Drive and Google Sheets:

### The easy way: the setup page
Run `./setup.sh` (or `setup.bat`) and open step 4, **Google Drive and Sheets**. Follow its short guide to create a Google project and a **Desktop app** client, paste the Client ID and secret, press **Connect Google**, then **Create my folder and sheet**. It creates a Drive folder "Internify resumes" and a spreadsheet "Internify tracker" (with the columns below and a Status drop-down) and writes everything into `.env`.

Important: in the Google consent screen press **Publish app** (otherwise Google cancels the connection after 7 days). Google then shows an "unverified app" warning when you sign in; for your own app press **Advanced → Go to Internify**. Details: [`docs/domain_setup.md`](docs/domain_setup.md), part 3.

### Or by hand: copy the template
We also provide a pre-formatted tracker with automated formulas and dropdown menus:
👉 **[Open Internify — Application Tracker Template](https://docs.google.com/spreadsheets/d/1ozOD9aYl43jPgfGXid_BOmQRXHy8Aiw_eT5JzSwgccQ/edit?usp=sharing)**

1. Open the link above, click **File → Make a copy** to save it to your own Google Drive.
2. Copy the **Spreadsheet ID** from your copied URL:
   ```text
   https://docs.google.com/spreadsheets/d/<YOUR_SPREADSHEET_ID>/edit
   ```
3. Set `GOOGLE_SHEET_ID=<YOUR_SPREADSHEET_ID>` in your `.env`, with the other Google values (see `.env.example`).

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
2. Authenticate once with `python scripts/get_gdrive_token.py` to obtain your refresh token. Full steps, including moving the Google consent screen from Testing to Production so the token does not expire after 7 days: [`docs/domain_setup.md`](docs/domain_setup.md).
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

Your saved experience is kept in `output/data/` (outside the code, so updates never touch it), and resumes uploaded to Google Drive stay private to your account.

---

## 📄 License

This project is open-source under the [MIT License](LICENSE).
