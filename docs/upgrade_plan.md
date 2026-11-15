# Internify upgrade plan: keyword discovery, less friction, and Internify Cloud

Status: **draft for review** · Last updated: 15 Nov 2026

This document describes the next big upgrade: what changes, why, in what order, and how we will know each part is
done. It is written so that anyone (including a future contributor) can pick up a phase and build it.

---

## 1. Goals

1. **Find jobs from keywords.** You type what you want ("software engineer intern, data analyst intern"); Internify
   searches the places Malaysian internships are actually posted. Adding pages by hand becomes optional.
2. **No friction for students.** A student should get their first tailored resume by opening a Telegram bot and
   sending their resume, with no VPS, Docker, keys, domain or Google Cloud setup.
3. **Two ways to use it, one codebase.**
   - **Self-hosted (open source, free):** run your own copy with your own keys, as today.
   - **Internify Cloud (paid, run by the maintainer):** the maintainer runs the server, the AI, the scraper and the
     Google app; users just use the bot.
4. **Google without the weekly logout and without a domain** for self-hosters, and with one tap ("Connect Google")
   for Cloud users, storing files in **the user's own Drive**.

### Not in scope (for now)
- A web app instead of Telegram (Cloud users use Telegram only; see decisions).
- Countries other than Malaysia (the product targets Malaysia only).
- Fields other than CS / tech internships (the resume template and skill categories stay tech-focused).

---

## 2. Decisions already made

| Topic | Decision |
|---|---|
| Target users | Malaysian students looking for CS / tech internships |
| Job sources | LinkedIn, JobStreet and Hiredly keyword search, **each switchable**, with a health check; open web search and company job boards as well |
| Cloud AI costs | The maintainer's AI key, with a **monthly allowance** per plan |
| Payments | **Manual first:** users pay directly (e.g. DuitNow / bank transfer), the maintainer activates them in `/admin`. A payment gateway comes later |
| Free tier | **Small free tier** in the Cloud: 3 resumes a month, no daily check; the tidy-up and GitHub check are included |
| Paid plan | **RM9.90 a month for 30 resumes**, daily job check included (can change before launch) |
| Data retention (Cloud) | Data of an inactive user (plan ended, no activity) is **deleted after 6 months**, with a Telegram warning a week before |
| Sources in self-hosted copies | LinkedIn, JobStreet and Hiredly are **on by default**; the README explains what they do and how to switch them off |
| Interface | **Telegram only.** The web holds the marketing pages, each user's live dashboard and the Connect Google step, opened from links the bot sends |
| Domain | Keep **`internify.adyhafetz.dev`** for now; everything lives under paths so a later move is a DNS change |
| Scale | **Tens of users** at first: one VPS, SQLite and files on disk. Design so Postgres can come later |
| Google (Cloud) | One Google app owned by the maintainer, permission limited to files the bot creates (`drive.file`); files live in **the user's own Drive** |
| Google (self-hosted) | Same `drive.file` permission, so the user's own Google app can leave "Testing" mode and stop logging out every 7 days, with no domain needed |
| Domains for users | **Paths, not sub-sub-domains:** `internify.adyhafetz.dev/u/<id>/…` (Cloudflare's free certificate covers only one level of sub-domain) |
| Existing copies | The maintainer's copy and the friend's copy move into the Cloud as its first two users |

---

## 3. Where things stand today

### 3.1 Discovery
- The daily check reads a list of **job pages** (added by hand, from history, or found by the web search).
- Company boards on Greenhouse, Lever, Ashby, SmartRecruiters and Workable are read through their public APIs.
- The **web search** already uses your roles and places, but only searches those six hiring systems.
- **Evidence it misses most Malaysian jobs:** of the 13 applications in the maintainer's tracker, only 1 (Western
  Digital, SmartRecruiters) is on those systems. The rest are on company career sites (Shopee, DHL, Deloitte,
  ExxonMobil, Roche, Lenovo, Ant), BrioHR, or were found on LinkedIn.

### 3.2 One copy = one person
Everything assumes a single user. The parts that will need to know *which* user:

| Where | What is single-user today |
|---|---|
| `service/config.py`, `service/fact_store/store.py`, `service/profile_data.py`, `service/editor.py`, `service/importer.py` | One `output/data/facts.json`, `resume_skeleton.json`, embeddings cache and backups folder |
| `service/github_evidence.py` | One `github_index.json`, one `GITHUB_USERNAME` from `.env` |
| `service/outcomes.py` | One `outcomes.json` |
| `service/latex/compile.py`, `service/events.py` | One `output/` folder for PDFs and run traces |
| `bot/services/db.py` | `applications`, `watch_sources`, `seen_jobs`, `settings` tables with no user column |
| `bot/services/watchlist.py` | Settings such as `owner_chat_id`, `scan_roles`, `scan_places`, `scan_interns_only`, `scan_web_search`, `scan_paused`, `scan_last`, `search_last`, `sources_removed`, `tidy_*` are global |
| `bot/scanner.py` | One daily check (`_running` flag), one schedule |
| `bot/tidy.py`, `bot/tracker.py` | One weekly tidy-up, one reminders job, one Sheet sync |
| `bot/handlers/common.py` | `TELEGRAM_ALLOWED_USER_IDS` lets several people in, but they would **share one profile** |
| `bot/services/google_auth.py`, `sheets.py`, `drive.py` | One Google login from `.env`, one Sheet, one Drive folder |
| `bot/dashboard/server.py` | One dashboard protected by one `DASHBOARD_KEY`; `/live`, `/api/stream`, `/pdf/{filename}` show everyone's runs |

### 3.3 Google
- The bot asks for **full Drive** (`auth/drive`) and **full Sheets** (`auth/spreadsheets`) access
  (`bot/services/google_auth.py`). Google treats these as risky permissions, so the user's Google app stays in
  "Testing" mode, where **the login expires after 7 days**. Leaving Testing mode means Google's verification, which
  is why a domain, homepage and privacy page were needed.

### 3.4 Installing
- A self-hoster needs: a server or an always-on computer, Docker, a Telegram bot, an AI key, and optionally Google
  Cloud, a domain and a Cloudflare tunnel.
- The Docker image is built on the user's machine and downloads an **x86-only** Tectonic, so it does not build on ARM
  (Apple Silicon, Raspberry Pi, Oracle's free ARM servers).

---

## 4. Target architecture

```
                         internify.adyhafetz.dev  (one Cloudflare tunnel)
                                     │
        ┌────────────────────────────┼──────────────────────────────────────┐
        │ /            marketing (home, pricing, how it works)              │
        │ /privacy /terms                                                   │
        │ /u/<id>/live         a user's live dashboard (sign-in link)       │
        │ /connect/google      Connect Google (OAuth start + callback)      │
        │ /admin               maintainer: users, plans, usage, health      │
        └────────────────────────────┬──────────────────────────────────────┘
                                     │
   Telegram ───────► one bot process (MODE=single or MODE=cloud)
                                     │
        ┌────────────────────────────┼──────────────────────────────────────┐
        │ per-user context: who is this? → their data folder, settings,     │
        │ Google login, limits                                              │
        │                                                                   │
        │ Discovery: keywords → sources (LinkedIn, JobStreet, Hiredly,      │
        │            open web, company boards) → merge → filters → Matcher  │
        │ Pipeline:  Scout → Analyst → Archivist → Scribe ⇄ Critic → Publisher
        │ Tracker, Curator, reminders: as today, per user                   │
        └───────────────────────────────────────────────────────────────────┘
                                     │
            output/users/<id>/  (profile, embeddings, PDFs, GitHub index, backups)
            output/internify.db (every table has user_id)
            The user's own Google Drive / Sheet (Cloud: via Connect Google)
```

### 4.1 Two modes, one codebase
- `MODE=single` (default): self-hosted, one user, keys in `.env`. Behaves exactly as today. `user_id` is always the
  owner, so no self-hoster notices any change.
- `MODE=cloud`: many users, one profile per Telegram user, the maintainer's keys, limits and plans, Connect Google.

---

## 5. Phase A: improvements for everyone (both modes)

Estimated effort: **1 to 2 weeks**. Each item is its own pull request.

### A1. Keyword discovery with Malaysian sources

**What the user sees**
- `/profile` → 🔭 Job search (renamed from "Job pages I check") → **🎯 What I look for**: keywords (roles), places
  (Malaysian states + Remote), internships only, and a switch per source.
- The daily check searches every switched-on source for every keyword. Pages added by hand still work as an extra.
- ⚙️ Status shows each source's health: "LinkedIn: ok, 34 jobs today" / "JobStreet: not answering since 12 Nov".

**Design**
- New module `bot/services/sources/` with one file per source, all with the same shape:

  ```python
  class Source(Protocol):
      name: str                      # "LinkedIn"
      key: str                       # "linkedin" (the setting that switches it on/off)
      async def search(self, keyword: str, places: list[str], limit: int) -> list[Candidate]: ...
  ```
- Sources, in order of value for Malaysia:

  | Source | How | Notes |
  |---|---|---|
  | LinkedIn | Its public, no-login job search pages (the ones its own site loads for visitors), filtered to Malaysia | Most Malaysian postings appear here. Each job also has a public, no-login page, which the Scout can use too |
  | JobStreet | The search its own website uses (`my.jobstreet.com`), filtered to Malaysia | Malaysia's biggest board |
  | Hiredly | The search its own website uses | Popular with Malaysian graduates |
  | Open web | Bing, then DuckDuckGo, with **no site restriction** (`"<keyword>" Malaysia internship`); the AI picks single postings out of the results | Safety net; finds company career sites |
  | Company boards | Existing Greenhouse / Lever / Ashby / SmartRecruiters / Workable readers | Kept as today, including boards found automatically |

- Merge all results, then remove repeats by link and by company + title (`db.same_job`), then apply the filters,
  then the Matcher, exactly as today.
- Malaysia only: places become Malaysian states + Remote; keywords may be searched in Malay too
  ("latihan industri", "pelatih", "praktikal") when "internships only" is on.
- Politeness: at most a few requests per source per keyword per day, with pauses; one source failing never stops
  the others; a source that keeps failing is marked unhealthy and skipped for a day.
- The Scout learns the new sources: a LinkedIn job link is read through LinkedIn's public job page first.

**Risks**
- LinkedIn, JobStreet and Hiredly have no official public API. Their search can change or block us at any time,
  and automated access is a grey area in their terms. Mitigations: switch per source, health check, low volume,
  the open web search as a fallback, and a note in the README and privacy page.

**Done when**
- Typing keywords with no pages added gives jobs from at least two sources in a daily check.
- Each source has an offline test with recorded sample answers.
- Status shows per-source health; a failing source is reported, not fatal.

### A2. Google: limited permission, no weekly logout, no domain

- Change the permissions in `bot/services/google_auth.py` from `auth/drive` + `auth/spreadsheets` to
  **`auth/drive.file`** (only files the bot creates or the user opens with it). Check the Sheets API works with it
  for the sheet the setup page created.
- Update the setup page and `docs/domain_setup.md`: after creating the Google app, **publish it** (leave Testing
  mode). With only a low-risk permission this should not need Google's verification, so no domain is needed.
  *To confirm in the Google Cloud console before we rely on it (see open questions).*
- Existing users: one re-connect (the setup page's Google step) to get a login with the new permission.

**Done when**: a self-hosted copy stays connected for more than 7 days without a domain.

### A3. Google becomes optional: Excel export

- `/applied` → **📊 Send as Excel**: the tracker as an `.xlsx` file in Telegram (same columns as the Sheet).
- The README says plainly: Google is an optional extra; the tracker works without it.

### A4. Export / Import my data

- `/profile` → **📦 My data** → *Export*: one `.zip` with the profile (facts, skeleton), the tracker, settings and
  the GitHub index. *Import*: restores from that file (with a backup first).
- A `schema_version` field in the saved files so later changes can upgrade old data safely.
- This is also how the existing copies move into the Cloud (C-phase migration).

### A5. Easier self-hosting

- Publish a **prebuilt image** for x86 and ARM on GitHub's container registry (built by GitHub Actions on each
  release), and switch `docker-compose.yml` to `image:` with `build:` as a fallback.
- Download the matching Tectonic for the machine's architecture.
- A documented **laptop mode** (Docker Desktop on Windows / macOS): the daily check runs when the laptop is on and
  catches up after start-up if it missed a day.
- Startup checks: a wrong key, an unknown model name or a missing Telegram token gives one clear message.

---

## 6. Phase B: Internify Cloud (multi-user mode)

Estimated effort: **2 to 4 weeks**.

### B1. Per-user data

- A `users` table: `id` (random public code, e.g. `k3f9x2`), `telegram_id` (unique), `plan`, `status`
  (`active` / `paused` / `deleted`), `created_at`, `limits`, `google` (encrypted login), `github_username`.
- Every other table gets a `user_id` column (`applications`, `watch_sources`, `seen_jobs`, `settings`), with indexes
  and a migration that assigns existing rows to the owner.
- Files move to `output/users/<id>/` (profile, embeddings, backups, GitHub index, outcomes, PDFs, traces).
- One **current user** context: the Telegram handler sets it from `update.effective_user.id`; jobs on a schedule set
  it per user. Functions that read paths or settings get them from the context instead of `config` constants.
- In `MODE=single`, the context is always the owner, and paths stay where they are today
  (`output/data/…`), so self-hosters need no migration.

### B2. Sign-up in Telegram

- `/start` from an unknown Telegram user in Cloud mode: a short welcome, the free tier explained, and "send me your
  resume to begin" (the existing import flow).
- No allow-list in Cloud mode; instead: plan limits, a per-user rate limit, and the maintainer can pause a user.
- New commands: `/plan` (your plan and usage this month), `/dashboard` (sends a sign-in link),
  `/deleteaccount` (asks twice, then deletes all your data and disconnects Google).

### B3. Plans, allowance and limits

| | Free | Paid (RM9.90 / month) |
|---|---|---|
| Resumes per month | 3 | 30 |
| Daily job check | No | Yes, up to 3 resumes per check (they count toward the 30) |
| Tidy-up, GitHub check | Yes | Yes |
| Google Drive / Sheet | Yes | Yes |

- Usage is counted per user per month (resumes written, AI calls, scraping calls), shown in `/plan` and ⚙️ Status.
- When a limit is reached: a friendly message with how to upgrade; nothing half-done.
- The maintainer's cost guard: a global monthly AI budget; when it is near, new free-tier resumes wait.

### B4. Web paths and sign-in links

- `/u/<id>/live`: that user's dashboard only (their runs, their PDFs). `/api/stream` and `/pdf/...` filter by user.
- `/dashboard` in Telegram sends a link with a short-lived one-time code; opening it sets a cookie for that user.
  The `<id>` alone never grants access.
- `/admin`: the maintainer's view (users, plan, usage, failures, source health, activate / pause a user). Protected
  by the maintainer's own sign-in link plus `ADMIN_TELEGRAM_ID`.
- `/` becomes the marketing site (home, how it works, pricing, FAQ, privacy, terms).

### B5. Connect Google (Cloud)

- The maintainer creates **one** Google app with the `drive.file` permission, published and verified for the
  domain (homepage, privacy and terms already exist).
- `/profile` → 🔗 Connect Google → link to `/connect/google?t=<one-time code>` → Google sign-in → back to
  `/connect/google/callback` → the bot creates the **Internify** folder and the tracker Sheet **in the user's own
  Drive** and saves their login (encrypted with a key from the server's `.env`).
- Disconnect at any time (`/profile` or the user's Google account settings). Files stay theirs.
- The Sheet stays in charge of statuses, per user, as today.

### B6. GitHub per user

- `/profile` → GitHub username (instead of `GITHUB_USERNAME` in `.env`); the index lives in the user's folder.

### B7. Running it safely

- Encrypt Google logins at rest; never log resume text or logins.
- Daily backup of the database and `output/users/`, kept for 14 days; a tested restore.
- One daily check and one AI call at a time per user; a small global worker limit so one user can't slow the rest.
- Privacy page updated for Cloud: what is stored (resumes, job links, Google login), where, for how long, and how to
  delete it.
- Retention: a user with no active plan and no activity for 6 months gets a Telegram warning, then 7 days later all
  their data is deleted and Google is disconnected (the same as `/deleteaccount`).

---

## 7. Phase C: paid launch

1. **Manual payments first:** `/plan` shows how to pay (DuitNow / bank transfer details) and a reference code; the
   maintainer marks the user paid in `/admin`, which sets the plan and its end date. Reminders before a plan ends.
2. **Move the existing copies in:** export from `~/internify` and `~/internify-sofea` (A4), import into the Cloud
   as the first two users, connect Google again, then retire the `internify-sofea` folder, its tunnel and its
   sub-domain.
3. **Marketing pages and launch:** home page, pricing, FAQ, demo video; launch at Malaysian university CS clubs.
4. **Later:** a payment gateway (ToyyibPay, Billplz or Stripe Malaysia) to automate sign-up and renewals.

---

## 8. Migration and compatibility

- Self-hosted copies keep working on every update: `MODE=single` is the default and keeps today's paths.
- Database changes are added in place on start-up (as with the tracker columns), never by deleting data.
- The Google permission change (A2) needs one re-connect; the setup page will say so.
- The rename "Job pages I check" → "Job search" keeps every existing page.

---

## 9. Testing

- Every source (A1) gets an offline test with recorded sample answers, and a "live" check script the maintainer can
  run on the VPS to see whether each source still answers.
- Multi-user (B1): a test that two users' profiles, trackers, settings, dashboards and PDFs never mix.
- Limits (B3): a test that the free tier stops at its allowance and the paid tier does not.
- Connect Google (B5): a test with a fake Google that the folder and Sheet are created in the user's account and
  the login is stored encrypted.
- All existing offline tests keep passing in `MODE=single`.

---

## 10. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| LinkedIn / JobStreet / Hiredly change their search or block the server | Fewer jobs found | Switch per source, health check, open web fallback, quick fixes |
| Their terms forbid automated access | Possible complaint or block | Low volume, no login, respect blocks, clear note in README and privacy page |
| Google verification takes time or asks for more | Cloud launch delayed | Start verification early in Phase B; `drive.file` keeps it light; Google stays optional |
| AI costs from free users | Money | Free tier allowance, global monthly budget, cheap models for extraction |
| Holding other people's resumes and Google logins | Trust and legal duty (Malaysia's PDPA) | Encryption, minimal data, delete-my-account, honest privacy page, backups |
| Multi-user change breaks self-hosters | Bad updates | `MODE=single` default, tests run in both modes |
| One VPS goes down | Everyone's bot stops | Daily backups, a written restore guide, uptime alert |

---

## 11. Open questions

These still need an answer before or during the phase they belong to.

1. **Plan length:** monthly only, or also per semester (about 4 months, matching internship season)?
2. **Google `drive.file` publishing (A2):** confirm in the Google Cloud console that an app with only `drive.file`
   can be published without verification, and that the Sheets API accepts it for the tracker sheet.
3. **Maintainer contact and payment details** shown in `/plan` and on the pricing page.
4. **Brand:** keep the name "Internify" and `internify.adyhafetz.dev`, or plan a move to a new domain before the
   public launch?
5. **Allowance top-ups:** when a paid user uses all 30 resumes, can they buy more that month, or wait?

### Answered
- Paid plan: RM9.90 / month, 30 resumes, daily check included.
- Free tier: 3 resumes / month, no daily check, tidy-up and GitHub check included.
- Retention: delete an inactive user's data after 6 months, with a warning a week before.
- Self-hosted sources: LinkedIn, JobStreet and Hiredly on by default.
