# Setting up Internify Cloud (the hosted version)

This is for the person who runs the paid, shared bot. If you only want your own copy, use the normal setup instead.

Run the hosted bot in its **own folder** with its **own bot** and **own `.env`**, separate from any personal copy.

## 1. Before you start

- A server (a small VPS is enough for tens of users) with Docker.
- A new Telegram bot from @BotFather. This is the public one; do not reuse a personal copy's token.
- Your Telegram id (message @userinfobot). This becomes `ADMIN_TELEGRAM_ID`.
- A domain name for the site, pointed at the server (for example through a Cloudflare tunnel).
- An AI key. You pay for every resume, so set a monthly ceiling (`AI_MONTHLY_BUDGET_CALLS`).
- A Google "Web application" OAuth client (see `docs/domain_setup.md`, part 6).

## 2. The `.env` file

Create `~/internify-cloud/.env`:

```
MODE=cloud
TELEGRAM_BOT_TOKEN=<new bot token>
ADMIN_TELEGRAM_ID=<your Telegram id>
OPENAI_API_KEY=<AI key>            # or GEMINI_API_KEY with the provider settings
PUBLIC_URL=https://internify.example.com
PAY_INSTRUCTIONS=Pay RM9.90 by DuitNow to 01X-XXXXXXX (Your Name). Then wait for a message here.
PAID_PRICE=RM9.90
GOOGLE_CLIENT_ID=<web client id>
GOOGLE_CLIENT_SECRET=<web client secret>
GOOGLE_TOKEN_KEY=<see below>
SCAN_TIMEZONE=Asia/Kuala_Lumpur
AI_MONTHLY_BUDGET_CALLS=<your ceiling>
```

Make the token key once and keep it safe:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

**Back up `.env` and `GOOGLE_TOKEN_KEY` somewhere private, away from the server.** Without the key, every saved Google connection becomes unreadable and everyone must connect again.

## 3. Start it

```bash
docker compose up -d --build
docker compose logs -f telegram-bot
```

The bot refuses to start if the admin id, bot token or AI key is missing. Anything else that still needs fixing (site address, payment text, Google, backups) is logged and sent to you in Telegram when it starts.

## 4. Check it works

1. Send `/start` to the bot from another Telegram account. You should get a welcome and a payment reference.
2. Send `/admin` from your account: you should see the person, plan counts and the backup line.
3. Open `PUBLIC_URL`: the public site loads. `PUBLIC_URL/healthz` answers `ok`.

## 5. Taking payments

Paying is manual at first. A person pays you using the reference `INT-XXXXXX` from `/plan`, then you run:

```
/setplan INT-XXXXXX paid 2027-01-31
```

The reference, the user id or the Telegram id all work. The person is told automatically.

## 6. Backups and alerts

- The bot writes a backup every day at 03:30 (`SCAN_TIMEZONE`) into `output/server_backups/`, keeps 14 and tells you if one fails. `/admin` shows how old the last one is.
- That folder is on the same server. **Copy it off the server** every day, for example with a cron job and `rclone copy ~/internify-cloud/output/server_backups remote:internify-backups`.
- Test a restore once (before you need it):

```bash
docker compose stop telegram-bot
docker compose run --rm -v ~/internify-cloud/output/server_backups:/backups telegram-bot python scripts/restore_backup.py /backups/<file>.tar.gz
docker compose start telegram-bot
```

The current database is kept as `internify.db.before-restore`.
- Uptime: add `PUBLIC_URL/healthz` to a free monitor (UptimeRobot or Cloudflare health checks) and send alerts to your phone or email.
