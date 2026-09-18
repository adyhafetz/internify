# Internify 🚀

An autonomous AI agent pipeline that tailors your resume for any job application using a grounded fact store, compiles a production-ready LaTeX PDF using **Jake's Resume** format, and integrates seamlessly with **n8n** and **Telegram**.

---

## Architecture Overview

```
[ Telegram / Job URL ] ──► [ n8n Workflow ]
                                  │
                  ┌───────────────┴───────────────┐
                  ▼                               ▼
       [ Admin CRUD Commands ]          [ POST /process ]
     (/set*, /add*, /delete*, etc.)               │
                  │                               ▼
                  ▼                     [ 1. Job Extraction (Instructor) ]
       [ service/admin.py ]                       │
       (Manipulates fact store)                   ▼
                  │                     [ 2. Fact Retrieval (Embeddings) ]
                  │                               │
                  │                               ▼
                  │                     [ 3. Agentic Loop (Generator & Critic) ]
                  │                               │
                  │                               ▼
                  │                     [ 4. LaTeX Render & Tectonic Compile ]
                  │                               │
                  ▼                               ▼
         [ Fact Store JSON ] ──────────► [ Output Tailored PDF ]
```

### Key Highlights
- **Zero Hallucinations**: Every generated bullet point is strictly grounded in and cited from verified facts in your master fact store.
- **Dynamic Skill & Course Shuffling**: Automatically selects and shuffles relevant courses and technical skills based on the target job requirements.
- **Full Telegram CRUD**: Modify your name, contact info, education, courses, experiences, projects, and skills directly via Telegram chat commands.
- **Self-Healing LaTeX Engine**: LaTeX compilation powered by Tectonic with automated error recovery.

---

## Getting Started: Complete Setup Guide

Follow these 5 steps to get Internify running from scratch.

### Step 1: Gather API Keys & Credentials

You will need accounts/keys for these services:

1. **Docker**:
   - Install [Docker Desktop](https://www.docker.com/) (Windows/macOS) or Docker Engine (Linux).
2. **OpenAI API Key**:
   - Sign up at [platform.openai.com](https://platform.openai.com/) and create an API key.
3. **Telegram Bot Token**:
   - Open Telegram and message [`@BotFather`](https://t.me/BotFather).
   - Run `/newbot`, name your bot, and copy the **HTTP API Token**.
4. **ScrapingAnt API Token** (for job page scraping):
   - Sign up for a free account at [scrapingant.com](https://scrapingant.com) (includes 10,000 free requests/month).
   - Copy your **API Token** from the dashboard.
5. **Google Cloud Service Account** (for Sheets tracker and Drive resume uploads):
   - Go to [Google Cloud Console](https://console.cloud.google.com/) and create a project (e.g. `internify`).
   - Navigate to **APIs & Services** ➔ **Library**, search for and enable:
     - **Google Sheets API**
     - **Google Drive API**
   - Go to **APIs & Services** ➔ **Credentials** ➔ **Create Credentials** ➔ **Service Account**.
   - After creation, click on the service account ➔ **Keys** tab ➔ **Add Key** ➔ **Create new key** ➔ select **JSON**.
   - Download the JSON key file.
6. **Google Sheet & Drive Setup**:
   - Open the [**Internify Application Tracker Template**](https://docs.google.com/spreadsheets/d/1ozOD9aYl43jPgfGXid_BOmQRXHy8Aiw_eT5JzSwgccQ/edit?usp=sharing).
   - Click **File** ➔ **Make a copy** in Google Sheets. (Ensure the active tab is named `Tracker`).
   - Create a dedicated folder in your Google Drive (e.g. `Internify Resumes`).
   - **Important**: Click **Share** on both the Google Sheet and the Google Drive folder, and add your Service Account email address (e.g. `internify@project.iam.gserviceaccount.com`) as **Editor**.

---

### Step 2: Clone Repository & Configure Environment

```bash
git clone https://github.com/adyhafetz/internify.git
cd internify

# Create your .env file
cp .env.example .env
```

Open `.env` and fill in your variables:

```env
# Required: OpenAI API Key
OPENAI_API_KEY=sk-proj-your-key-here

# Timezone
TZ=Asia/Kuala_Lumpur

# Optional if running locally with ngrok (see Step 4)
WEBHOOK_URL=http://localhost:5678/
N8N_HOST=localhost
```

---

### Step 3: Start Services with Docker Compose

Run the following command to build and start both the **FastAPI Core** and **n8n Orchestrator**:

```bash
docker compose up -d --build
```

Verify that both containers are running:
```bash
docker compose ps
```
- **FastAPI Core Backend**: Accessible at `http://localhost:8000`
- **n8n Automation Engine**: Accessible at `http://localhost:5678`

---

### Step 4: Configure n8n & Telegram Webhook

#### A. Expose n8n Webhook for Telegram (If Testing Locally)
Telegram requires a public **HTTPS** URL to send webhook events. If you are running locally on your computer, use **ngrok**:

```bash
# In a separate terminal
ngrok http 5678
```
Copy the generated HTTPS URL (e.g. `https://xyz.ngrok-free.app`). 

Update `.env`:
```env
WEBHOOK_URL=https://xyz.ngrok-free.app/
```
Then restart n8n:
```bash
docker compose restart n8n
```

*(Note: If deploying on a cloud VPS with a domain, see the [Production Deployment](#production-deployment-cloud-vps) section instead).*

#### B. Import the Workflow into n8n
1. Open `http://localhost:5678` in your browser and complete the initial account setup.
2. In n8n, click **Workflows** ➔ **Import from File**.
3. Select the file [`Internify — Telegram Bot.json`](Internify%20—%20Telegram%20Bot.json) included in this repository.

#### C. Set Up Credentials in n8n
Inside n8n, go to **Settings** ➔ **Credentials** and configure the 4 accounts:
- **Telegram Account**: Name it `Internify — Telegram Account` and enter your Bot Token.
- **OpenAI Account**: Name it `Internify — OpenAI Account` and enter your OpenAI API key.
- **Google Service Account**: Name it `Internify — Google Service Account` and upload your Service Account JSON file.
- **ScrapingAnt Account**: Name it `Internify — ScrapingAnt Account` and enter your ScrapingAnt API Token.

#### D. Link Your Sheet and Drive Folder
In the n8n canvas:
- Open each Google Sheet node (`Lookup`, `Append Row`, `Get Row Number`, `Delete Failed Row`, `Update Resume Link`), click on **Document**, and select your copied `Internify — Application Tracker` spreadsheet.
- Open each Google Drive node (`Upload Resume`, `Upload Master Resume`), click on **Folder**, and select your `Internify Resumes` folder.

#### E. (Recommended) Restrict Bot to Your Personal Telegram Account
By default, anyone who knows your bot's username could interact with it. To lock it exclusively to you:
1. Message [`@userinfobot`](https://t.me/userinfobot) on Telegram to get your numeric user ID (e.g. `123456789`).
2. In n8n, add an **If** node right after `Telegram Trigger` checking:
   `{{ $json.message.from.id }} equals YOUR_NUMERIC_ID`
3. Connect the **True** output to `Switch Mode`.

#### F. Activate Workflow
Click **Save** and toggle the workflow from **Inactive** to **Active** (top right corner).

---

### Step 5: Test Your Setup

1. Open Telegram, open your bot, and send:
   ```
   /start
   ```
2. Your bot will reply with the full command menu!
3. Customize your initial profile using:
   ```
   /setname Your Name
   /setphone +1 234 567 890
   /setemail your.email@example.com
   ```
4. Test full resume export with `/resume` or drop any job URL to test automatic tailoring!

---

## Standalone Backend Testing

You can also test the resume generation pipeline directly via curl without n8n or Telegram:

```bash
curl -X POST http://localhost:8000/process \
  -H "Content-Type: application/json" \
  -d '{
    "company": "Stripe",
    "job_title": "Software Engineering Intern",
    "location": "Remote",
    "job_description": "Looking for an intern experienced in Python, REST APIs, and Docker.",
    "job_requirements": "- Proficiency with Python and FastAPI\n- Familiarity with containerization\n- Strong database fundamentals",
    "job_link": "https://stripe.com/jobs"
  }'
```

Response:
```json
{
  "resume_status": "Excellent",
  "score": 88.0,
  "pdf_path": "/app/output/resume_stripe_20260918.pdf",
  "pdf_download_url": "http://fastapi-service:8000/files/resume_stripe_20260918.pdf",
  "summary": "Emphasized FastAPI, Docker, and PostgreSQL backend projects matching Stripe requirements."
}
```

---

## Telegram Bot Commands Reference

| Category | Command | Description |
|---|---|---|
| **Start** | `/start` | Show welcome message & command cheat-sheet |
| **Master Resume** | `/resume` | Render & export full untailored master resume PDF |
| **Profile** | `/myprofile` | View current contact info & links |
| | `/setname <name>` | Update your full name |
| | `/setphone <phone>` | Update your phone number |
| | `/setemail <email>` | Update your email address |
| | `/setlinkedin <url>` | Update LinkedIn URL & display text |
| | `/setgithub <url>` | Update GitHub URL & display text |
| **Education** | `/listeducation` | View all education entries and IDs |
| | `/addeducation <text>` | Add education details via plain English |
| | `/deleteeducation <id>` | Remove an education entry by ID |
| | `/addcourse <id> <name>` | Add a course to an education entry |
| | `/deletecourse <id> <name>` | Remove a course from an education entry |
| **Experience** | `/addexperience <text>` | Add work experience via plain English (auto-extracts skills) |
| | `/deleteexperience <id>` | Delete an experience entry and all its bullets |
| **Projects** | `/addproject <text>` | Add a project via plain English (auto-extracts skills) |
| | `/deleteproject <id>` | Delete a project and all its bullets |
| **Leadership** | `/addleadership <text>` | Add leadership/extracurricular activity |
| | `/deleteleadership <title>` | Delete a leadership entry by title |
| **Facts** | `/listfacts <entry_id>` | Inspect all fact bullets for an entry |
| | `/updatefact <id> <text>` | Edit bullet text (auto re-indexes embeddings) |
| | `/deletefact <fact_id>` | Remove a single fact bullet |
| **Skills** | `/listskills` | View master skills list by category |
| | `/addskill <text>` | Add skills into fixed categories |
| | `/deleteskill <item>` | Remove a skill from all categories |
| **Entries** | `/listentries` | View all entry IDs for projects, experiences, and leadership |
| **Job Tailoring** | *Paste any URL* | Automatically scrapes posting, tailors resume, and returns PDF |

---

## Production Deployment (Cloud VPS)

To run Internify 24/7 without keeping your computer on, deploy it to any cloud VPS (e.g. Tencent Cloud Lighthouse, DigitalOcean, Hetzner, AWS Lightsail) with Docker and Caddy (for automatic free SSL):

- 📖 **[Tencent Cloud Lighthouse Deployment Guide](docs/tencent_lighthouse_deployment.md)** (Full walk-through with firewall settings, automated HTTPS with Caddy, and zero-downtime updates).

---

## Directory Structure

```
.
├── Dockerfile                    # Multi-stage build with Python 3.11 & Tectonic LaTeX
├── docker-compose.yml            # Runs FastAPI backend and n8n together
├── Internify — Telegram Bot.json # Complete n8n workflow (ready to import)
├── output/                       # Generated PDFs and rendered .tex files
├── requirements.txt              # Python dependencies
├── docs/                         # Deployment & configuration guides
└── service/
    ├── admin.py                  # Full CRUD Admin API for resume skeleton & facts
    ├── clients.py                # Shared singletons for OpenAI and Instructor
    ├── config.py                 # Tunables (models, thresholds, limits)
    ├── models.py                 # Pydantic schemas across the pipeline
    ├── main.py                   # FastAPI entrypoint (/process, /health, /files)
    ├── agentic/                  # Generator, Critic, and iteration loop
    ├── extraction/               # Structured job posting extraction
    ├── fact_store/               # resume_skeleton.json & facts.json
    ├── latex/                    # Jinja2 template & Tectonic compiler
    └── retrieval/                # Cosine similarity retrieval over facts
```

---

## License

MIT License. Free to use, customize, and deploy.
