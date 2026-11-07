# Go live on your own subdomain (Cloudflare, Google, a clean server)

You bought `adyhafetz.dev` and want to keep the main address free for your portfolio. So everything for Internify
lives on one subdomain, `internify.adyhafetz.dev`, and the main domain is never touched.

| Address | Who can open it | What it is |
|---|---|---|
| `https://internify.adyhafetz.dev/` | everyone | Home page (the one Google asks for) |
| `https://internify.adyhafetz.dev/privacy` and `/terms` | everyone | Privacy policy and terms (Google asks for these too) |
| `https://internify.adyhafetz.dev/dashboard` (same as `/live`) | only you | The live agent dashboard; it asks for your key |

Why the home page is not the dashboard itself: Google (and anyone) must be able to open the home page without a
key, and the dashboard shows your job text and resume lines. So `/` is a public page that explains Internify and has an
"Open the live dashboard" button; `/dashboard` takes you to the dashboard and asks for your key if this browser
does not have it yet.

**New to domains? Four words.** *Domain*: `adyhafetz.dev`, which you own. *Subdomain*: a name in front of it,
`internify.adyhafetz.dev`, which costs nothing and does not affect the main domain. *DNS record*: a line in Cloudflare
that says where a name points. *Tunnel*: a private connection from Cloudflare to your server, so you never open a
port.

The bot's own web server serves all of it. A Cloudflare Tunnel connects the subdomain to your server, so no port
has to be opened. Menu names in Cloudflare and Google change now and then; the idea of each step stays the same.

Do the parts in order: **1** clean the server, **2** the subdomain, **3** Google, **4** the refresh token.

---

## 1. Clean the server and start fresh

Run these on the server (SSH in first). Lines starting with `#` are comments.

```bash
# 1. Keep a copy of anything you may want (your .env and saved experience)
mkdir -p ~/backup
cd ~/internify 2>/dev/null && cp .env ~/backup/env.old; cp -r service/fact_store ~/backup/ ; cp -r output ~/backup/output; cd ~

# 2. Stop and remove the old containers
cd ~/internify 2>/dev/null && docker compose --profile tunnel down --remove-orphans; cd ~

# 3. Remove old images, build cache and volumes (asks you to confirm)
docker system prune -a --volumes

# 4. Delete the old project folder (sudo, because Docker may have created root-owned files in it)
sudo rm -rf ~/internify

# 5. Get the new code
git clone https://github.com/adyhafetz/internify.git
cd internify
cp .env.example .env
nano .env
```

In `.env` fill in (save with Ctrl+O, Enter, Ctrl+X):

```env
OPENAI_API_KEY=sk-...
TELEGRAM_BOT_TOKEN=123456:ABC...
TELEGRAM_ALLOWED_USER_IDS=YOUR_TELEGRAM_NUMBER      # from @userinfobot
SCRAPINGANT_API_KEY=                                 # optional
DASHBOARD_KEY=                                       # see part 2, step 1
CONTACT_EMAIL=you@example.com                        # shown on your privacy page
CLOUDFLARE_TUNNEL_TOKEN=                             # part 2
# Google values come in part 4
```

Start it (add `--profile tunnel` once you have the tunnel token):

```bash
docker compose up -d --build --remove-orphans
docker compose logs -f telegram-bot        # Ctrl+C to leave the logs
```

Then message your bot `/start`. Your saved experience is now kept in `output/data/` (outside the code, so updating
the code never touches it). To start empty, send your resume with `/importresume`. To bring back the old
data, copy `~/backup/fact_store/facts.json` and `resume_skeleton.json` into `~/internify/output/data/` and restart.

Also remove access you no longer need: <https://myaccount.google.com/permissions> (old Internify tokens) and, if you
ever pasted a key anywhere public, create a new one.

---

## 2. The subdomain with a Cloudflare Tunnel

**Before you start (one minute).** Because you bought the domain at Cloudflare, it already uses Cloudflare's DNS. Check:
Cloudflare dashboard → **Websites** (or **Account home**) → click `adyhafetz.dev`. The Overview page should say
**Active**. That is all the setup the domain needs; you do not change nameservers or add any record by hand.

1. **Dashboard key.** On the server: `openssl rand -hex 16`. Put the result in `.env` as `DASHBOARD_KEY=...`.
2. **Zero Trust.** In the Cloudflare dashboard open **Zero Trust** (left menu). The first time it asks you to pick a
   team name (anything) and the **Free** plan; it may ask for a card but charges nothing.
3. **Create the tunnel.** **Networks → Tunnels → Create a tunnel → Cloudflared**, name it `internify`, Save.
   On the next screen choose **Docker**; copy only the long token (the text after `--token`).
4. **Public hostname** (shown as "Published application routes" in newer menus): Subdomain `internify`,
   Domain `adyhafetz.dev`, Path empty, Service type **HTTP**, URL `telegram-bot:8080`. Save. Cloudflare creates the
   DNS record for you; you do not add one by hand.
5. Put the token in `.env` as `CLOUDFLARE_TUNNEL_TOKEN=...`, then:

   ```bash
   docker compose --profile tunnel up -d --build
   docker compose logs cloudflared | tail      # look for "Registered tunnel connection"
   ```
6. **Close the server.** Only SSH needs to be open: `sudo ufw allow OpenSSH && sudo ufw --force enable`. If your
   provider has its own firewall (Tencent, AWS, Hetzner), leave only port 22 open there too. The dashboard port is
   already published only on the server itself.
7. **Check.** Open `https://internify.adyhafetz.dev/` (home), `/privacy`, `/terms`, then
   `https://internify.adyhafetz.dev/live?key=YOUR_KEY` once. The browser remembers the key for 30 days.

The main domain `adyhafetz.dev` is untouched. Later, point it at your portfolio however you like (for example
Cloudflare Pages); Internify keeps working on its subdomain.

---

## 3. Google: from Testing to Production

While the consent screen is in **Testing**, Google expires your refresh token after **7 days**, so Drive and Sheets
stop working. In **Production** it does not expire.

**Do you have to prove you own the domain to Google (Search Console) first? No, not to switch to Production for your
own use.** Ownership proof is only required if you press "Prepare for verification" to remove the "unverified app"
warning, and that route also needs a security review for the Drive permission, which is not worth it for a personal
tool. So: do steps 1, 3, 4 and 5 below, and treat step 2 as optional.

1. <https://console.cloud.google.com> → your project → **APIs & Services → Library**: make sure **Google Drive API**
   and **Google Sheets API** are enabled.
2. **Optional: prove you own the domain.** Skip this unless Google's page shows a "domain not verified" notice you
   want to clear.
   - Open <https://search.google.com/search-console> with the same Google account, **Add property → Domain**, enter
     `adyhafetz.dev`. It shows a TXT record starting `google-site-verification=`.
   - In Cloudflare: **Websites → adyhafetz.dev → DNS → Records → Add record**: Type `TXT`, Name `@`, Content = that
     text, Save. This does not affect your website or your portfolio later.
   - Back in Search Console press **Verify** (it can take a few minutes).
3. **Consent screen**: **APIs & Services → OAuth consent screen** (called *Google Auth Platform* in newer menus).
   - **Branding**: App name `Internify`; your support email; **App home page** `https://internify.adyhafetz.dev/`;
     **Privacy policy** `https://internify.adyhafetz.dev/privacy`; **Terms of service**
     `https://internify.adyhafetz.dev/terms`; **Authorized domain** `adyhafetz.dev`; developer contact email.
     Leave the logo empty (adding one triggers extra review). If a field complains the domain is not verified,
     that is the optional step 2; you can still save and publish.
   - **Audience**: user type **External**.
   - **Data access / Scopes**: add `https://www.googleapis.com/auth/drive` and
     `https://www.googleapis.com/auth/spreadsheets`.
4. **Audience → Publish app → Confirm**. The status becomes **In production**.
5. Do **not** press "Prepare for verification". Verification is only for apps with many users. For
   your own use, Google shows an **"unverified app"** warning when you sign in: choose **Advanced → Go to Internify
   (unsafe)**. It is your own app, so that is expected. (Limit: 100 users, far more than you need.)
6. **Get a new refresh token** (part 4). Tokens issued while in Testing keep expiring; only a new one is permanent.

The privacy page already contains what Google checks for: what Google data is used and why, the exact **Limited
Use** sentence, who receives data, retention and deletion, and a contact address (`CONTACT_EMAIL`).

---

## 4. Get the refresh token

Do this on **your own computer** (it opens a browser), not on the server.

1. Cloud Console → **APIs & Services → Credentials** (or **Clients**) → **Create credentials → OAuth client ID** →
   Application type **Desktop app** → Create → **Download JSON**. Save it in the project folder as `client_secret.json`.
2. In the project folder:

   ```bash
   pip install -r requirements.txt     # once (a virtual environment is fine)
   python scripts/get_gdrive_token.py
   ```
3. A browser opens. Sign in, press **Advanced → Go to Internify (unsafe)**, tick both permissions, **Continue**.
4. The script prints three lines: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN`.
   Paste them into the server's `.env` (`nano .env`), together with `GOOGLE_SHEET_ID` and `GOOGLE_DRIVE_FOLDER_ID`.
5. Reload the settings (a plain restart does not re-read `.env`):

   ```bash
   docker compose up -d --force-recreate
   docker compose logs telegram-bot | grep "Google sync"     # expect "Drive: connected" and the sheet connected
   ```
6. Delete `client_secret.json` and `token.json` from your computer. Never commit them (they are git-ignored).

If no refresh token is printed: open <https://myaccount.google.com/permissions>, remove Internify, run the script
again (it forces a fresh consent).

Note: uploaded resumes now stay **private** to your Google account (earlier versions made each file readable by
anyone with the link). Open the Drive link while signed in.

---

## Sharing the dashboard with someone

Send them `https://internify.adyhafetz.dev/live?key=YOUR_KEY`. Anyone with that link can see your runs, which
include job text and resume lines, so share it only when needed and change `DASHBOARD_KEY` afterwards.
