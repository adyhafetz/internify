# Run a second copy on the same server (for a friend)

Internify is built for one person: one bot, one saved profile, one history, one Google connection. To let a friend
use it on your server, give them **their own separate copy**. Their data stays in their own folder, and their bot,
keys and Google are their own.

> **Trust.** You administer the server, so you can read their `.env` (keys), resumes and history. Tell them. Also
> check your server's memory (`free -h`): about 1 GB per active user is comfortable.

## 1. Their folder
```bash
cd ~
git clone https://github.com/adyhafetz/internify.git internify-friend
cd internify-friend
cp .env.example .env
```
The folder name matters: Docker names containers and networks after it, so `internify-friend` and `internify` never clash.

## 2. Their settings
They need their own **OpenAI key**, **Telegram bot token** (from @BotFather) and **Telegram ID**. The easiest way to
fill in `.env`: they run `setup.bat` / `./setup.sh` on their own computer, then send you the `.env` file privately, and
you save it as `~/internify-friend/.env`. Or edit by hand: `nano .env`.

Add one line so the two copies don't both use port 8080 on the server:
```
DASHBOARD_HOST_PORT=8081
```

## 3. Start it
```bash
docker compose up -d --build
docker compose logs --tail 20 telegram-bot
```
Check that `docker ps` now shows two `telegram-bot` containers (one per folder), then they message their bot `/start`
and send their resume.

## 4. Optional: their own address (for the dashboard and Google pages)
The bot works fully without this. Only do it if they want the dashboard or a Google connection.
1. In Cloudflare create a **second tunnel** (Zero Trust → Networks → Tunnels → Create a tunnel), named `internify-friend`.
2. Add a public hostname: subdomain `friend`, your domain, service **HTTP**, URL `telegram-bot:8080`.
   (It is the same URL as yours: each folder has its own Docker network, so the name points to *their* bot.)
3. Their `.env` additions:
   ```
   CLOUDFLARE_TUNNEL_TOKEN=...           # the new tunnel's token
   PUBLIC_URL=https://friend.yourdomain.com
   DASHBOARD_KEY=...                     # openssl rand -hex 16
   CONTACT_EMAIL=their@email.com
   ```
4. `docker compose --profile tunnel up -d --build`

## 5. Their Google
They should create **their own** Google project, consent screen and Desktop app client (see
[`domain_setup.md`](domain_setup.md), parts 3 and 4), with their own `https://friend.yourdomain.com/` home, `/privacy`
and `/terms` links. Reusing your client would mean sharing your client secret. The privacy page describes a copy run by
one person for themselves; if you host a friend's copy, say so to them.

## 6. Updating and stopping
Update each copy separately: `cd ~/internify-friend && git pull && docker compose up -d --build` (add
`--profile tunnel` if it uses a tunnel). To stop theirs: `cd ~/internify-friend && docker compose down`.
