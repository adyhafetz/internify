# Deployment Guide — Tencent Cloud Lighthouse

This guide covers setting up **Internify** on a **Tencent Cloud Lighthouse** instance with Docker, Caddy (for automatic free SSL), and Telegram Webhook integration.

---

## 1. Create a Lighthouse Instance

1. Log into your [Tencent Cloud Console](https://console.cloud.tencent.com/lighthouse).
2. Click **Create** (Create Instance):
   - **Region**: Choose a region close to your users (e.g. Singapore, Hong Kong, or Tokyo if outside Mainland China; Guangzhou/Shanghai if inside).
   - **Blueprint**: Select **System Image** ➔ **Ubuntu 22.04 LTS** (or **Docker CE Blueprint** if available).
   - **Package**:
     - *Minimum*: 2 vCPU, 2 GB RAM (works, but add 2GB swap).
     - *Recommended*: 2 vCPU, 4 GB RAM (smooth compilation with Tectonic).
3. Set your root password or upload your SSH key, then complete the purchase.

---

## 2. Configure Firewall Rules (Tencent Console)

By default, Lighthouse blocks incoming ports. You must open:

1. In the Lighthouse console, go to your instance ➔ **Firewall** tab.
2. Click **Add Rule**:
   - `TCP: 22` (SSH - usually open by default)
   - `TCP: 80` (HTTP for SSL verification)
   - `TCP: 443` (HTTPS for Webhook & Web UI)
   - *(Optional for testing)* `TCP: 5678` (n8n Web UI)
3. Save the rules.

---

## 3. Connect to Server & Install Dependencies

SSH into your server:
```bash
ssh ubuntu@YOUR_SERVER_IP
# or
ssh root@YOUR_SERVER_IP
```

Update packages and install Docker + Docker Compose:
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y curl git ufw

# Install Docker
curl -fsSL https://get.docker.com | sudo sh

# Add your user to the docker group
sudo usermod -aG docker $USER

# Install Docker Compose plugin
sudo apt install -y docker-compose-plugin
```

*(Optional but recommended if on 2 GB RAM)*: Add 2 GB Swap:
```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

---

## 4. Setup Domain DNS

You need a domain name (or subdomain) pointing to your Lighthouse server's Public IP:

- `A` Record: `n8n.yourdomain.com` ➔ `YOUR_SERVER_IP`
*(If using a free domain service like DuckDNS, point your duckdns domain to `YOUR_SERVER_IP`)*.

---

## 5. Clone the Repository & Configure `.env`

```bash
git clone https://github.com/adyhafetz/internify.git
cd internify

# Create your .env file
cp .env.example .env
nano .env
```

Set the following values in `.env`:
```env
OPENAI_API_KEY=sk-proj-xxxx...
WEBHOOK_URL=https://n8n.yourdomain.com/
N8N_HOST=n8n.yourdomain.com
TZ=Asia/Kuala_Lumpur
```

---

## 6. Setup Caddy for Automatic Free HTTPS (SSL)

Install Caddy on the server:
```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update
sudo apt install -y caddy
```

Edit `/etc/caddy/Caddyfile`:
```bash
sudo nano /etc/caddy/Caddyfile
```

Replace its contents with:
```caddy
n8n.yourdomain.com {
    reverse_proxy localhost:5678
}
```

Reload Caddy:
```bash
sudo systemctl reload caddy
```
> Caddy will automatically request and install a free Let's Encrypt SSL certificate!

---

## 7. Start Internify with Docker Compose

Inside the `internify` directory:
```bash
docker compose up -d --build
```

Check the status of both containers:
```bash
docker compose ps
docker compose logs -f
```

---

## 8. Connect to n8n & Setup the Telegram Bot

1. Open `https://n8n.yourdomain.com` in your browser.
2. Create your owner account on first login.
3. Import `Internify — Telegram Bot.json` (**Workflows** ➔ **Import from File**).
4. Configure Credentials:
   - **Telegram API**: Enter your Bot Token from `@BotFather`.
   - **OpenAI API**: Enter your `sk-proj-...` key.
   - **Google Sheets & Drive**: Upload your Service Account JSON.
   - **ScrapingAnt API**: Enter your ScrapingAnt token.
5. In the top right corner, toggle the workflow to **Active**!
6. Open Telegram, message your bot `/start`, and your system is 100% live 24/7!
