# Cloud VPS Deployment Guide

Deploying **Internify** on any Cloud VPS (Hetzner, DigitalOcean, Tencent Lighthouse, AWS, Linode, etc.) is remarkably simple because the Python Telegram bot uses **long polling**—meaning:
- **No domain name required**
- **No SSL certificates or Caddy/Nginx required**
- **No public webhook ports needed** (the bot communicates outbound directly to Telegram's secure API)

---

## 1. Requirements

* Any Linux VPS (Ubuntu 22.04 LTS or Debian 12 recommended)
* Minimum 1 GB RAM (2 GB recommended for fast Tectonic compilation)
* Docker and Docker Compose installed

---

## 2. Server Setup (Ubuntu / Debian)

SSH into your server:
```bash
ssh root@YOUR_SERVER_IP
```

Update packages and install Docker:
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y curl git

# Install Docker via official script
curl -fsSL https://get.docker.com | sudo sh

# Ensure Docker Compose plugin is installed
sudo apt install -y docker-compose-plugin
```

*(Optional for 1GB RAM instances)*: Add a 2GB swapfile:
```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

---

## 3. Clone & Configure Internify

```bash
git clone https://github.com/adyhafetz/internify.git
cd internify

# Create your .env file
cp .env.example .env
nano .env
```

At minimum, fill in:
```env
OPENAI_API_KEY=sk-...
TELEGRAM_BOT_TOKEN=123456:ABC...
# (Recommended) Lock bot to your Telegram ID so strangers cannot control your bot:
TELEGRAM_ALLOWED_USER_IDS=YOUR_TELEGRAM_NUMERIC_ID

# (Optional) Bypass anti-bot on Workday/LinkedIn/Lever (Free 10k/mo at scrapingant.com):
SCRAPINGANT_API_KEY=

# (Optional) Cloud Sync (Google Drive & Sheets)
# Google Sheets Tracker Template: https://docs.google.com/spreadsheets/d/1ozOD9aYl43jPgfGXid_BOmQRXHy8Aiw_eT5JzSwgccQ/edit?usp=sharing
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REFRESH_TOKEN=
GOOGLE_SHEET_ID=
GOOGLE_SHEET_NAME=Tracker
GOOGLE_DRIVE_FOLDER_ID=
```

---

## 4. Start Internify

```bash
docker compose up -d --build
```

Check that the containers are healthy:
```bash
docker compose ps
docker compose logs -f telegram-bot
```

You will see:
```text
INFO - Initializing database...
INFO - Initialized SQLite database at output/internify.db
INFO - Successfully registered bot command menu with Telegram.
INFO - Application started.
```

Your Internify bot is now running live 24/7! Open your Telegram app and send `/start`.

