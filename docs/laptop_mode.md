# Running Internify on your own laptop (no server)

You don't need a server. Internify talks *out* to Telegram, so it works behind home Wi-Fi with no public address, domain or
open ports. The only catch: **it works while your computer is on and the bot is running.**

## Set up
1. Install [Docker Desktop](https://www.docker.com/products/docker-desktop/) (Windows, macOS or Linux) and start it.
2. Download Internify (**Code → Download ZIP** on GitHub, or `git clone`), and run `setup.bat` (Windows) or `./setup.sh`
   (macOS / Linux). The setup page asks for your AI key and Telegram bot token.
3. When the setup page finishes, start the bot:
   ```bash
   docker compose pull
   docker compose up -d
   ```
   `pull` downloads a ready-made image (Intel/AMD and ARM, so Apple Silicon Macs and Raspberry Pis work). If that doesn't work
   for any reason, `docker compose up -d --build` builds it on your computer instead (a few minutes the first time).

## Keep it running
- **Start with the computer:** in Docker Desktop, *Settings → General → Start Docker Desktop when you sign in to your
  computer*. The bot has `restart: unless-stopped`, so it comes back by itself whenever Docker starts.
- **Sleep:** while the computer sleeps or is off, the bot is paused. Telegram keeps your messages and the bot answers them when it
  wakes.
- **The daily job check** runs at `SCAN_TIME`. If the computer was off then, the bot notices when it starts (about a minute
  and a half after) and **runs today's check right away**, once. A copy that has never run a check waits for its first
  scheduled one.
- **Reminders** (follow-ups, deadlines) are sent the next time the bot is running at their time, so they may arrive late, not lost.

## Update
```bash
git pull
docker compose up -d
```
The code in `service/` and `bot/` is what runs, so a `git pull` and a restart are enough. If an update changes the Python
packages or LaTeX tools, run `docker compose pull` (or `docker compose up -d --build`) as well.

## When to use a server instead
If you want the daily check to run while your laptop is shut, or a link in your friends' hands, use a cheap server
([`vps_deployment_guide.md`](vps_deployment_guide.md)). Or wait for Internify Cloud (see [`upgrade_plan.md`](upgrade_plan.md)).
