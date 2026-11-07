# Put Internify on your own domain (Cloudflare)

Goal: everything lives under one address, for example `https://internify.adyhafetz.dev`:

| Address | Who can open it | What it is |
|---|---|---|
| `/` | everyone | Home page |
| `/privacy`, `/terms` | everyone | The pages Google asks for |
| `/live` | only you (key) | The live agent dashboard |

The bot's own web server serves all of it, so there is nothing else to host. A Cloudflare Tunnel connects your
domain to the server without opening any port.

## 1. Set a dashboard key

```bash
openssl rand -hex 16
```

Put the result in `.env` on the server as `DASHBOARD_KEY=...`. Without it, `/live` is open to the internet.

## 2. Create the tunnel (Cloudflare dashboard)

1. Cloudflare dashboard, **Zero Trust**, **Networks**, **Tunnels**, **Create a tunnel**, type **Cloudflared**.
2. Name it `internify`. Copy the **tunnel token** it shows.
3. Add a **public hostname**: subdomain `internify`, domain `adyhafetz.dev`, service type **HTTP**, URL `telegram-bot:8080`.
   Cloudflare creates the DNS record for you.
4. Put the token in `.env` as `CLOUDFLARE_TUNNEL_TOKEN=...`.

(Menu names change now and then. The idea stays the same: a tunnel whose hostname points to `telegram-bot:8080`.)

## 3. Start it on the server

```bash
git pull
docker compose --profile tunnel up -d --build
```

Check: open `https://internify.adyhafetz.dev/` (home page), then
`https://internify.adyhafetz.dev/live?key=YOUR_KEY` once. The browser remembers the key for 30 days.

## 4. Google consent screen

Use these values:

| Field | Value |
|---|---|
| Application home page | `https://internify.adyhafetz.dev/` |
| Privacy policy link | `https://internify.adyhafetz.dev/privacy` |
| Terms of service link | `https://internify.adyhafetz.dev/terms` |
| Authorized domain | `adyhafetz.dev` |

Then set the publishing status to **In production**, create a new refresh token (`python scripts/get_gdrive_token.py`)
and put it in `.env`. Google needs to be able to open the pages above, so keep the tunnel running.

## Sharing the dashboard with someone

Send them `https://internify.adyhafetz.dev/live?key=YOUR_KEY`. Anyone with that link can see your runs, which
include job text and resume lines, so share it only when needed, and change `DASHBOARD_KEY` afterwards.
