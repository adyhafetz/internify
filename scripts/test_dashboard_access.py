"""Offline check of who can open what on the dashboard server."""
import os, sys
sys.path.insert(0, os.path.abspath("."))
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
from fastapi.testclient import TestClient
from bot import config
from bot.dashboard.server import app

c = TestClient(app, follow_redirects=False)

# No key configured (local use): everything open
config.DASHBOARD_KEY = ""
assert c.get("/live").status_code == 200 and c.get("/api/runs").status_code == 200
print("no key set: open ✓")

# Key configured (public domain)
config.DASHBOARD_KEY = "s3cret key"
for path in ("/", "/privacy", "/terms", "/site/style.css", "/healthz"):
    assert c.get(path).status_code == 200, path
print("public pages open without a key ✓")
for path in ("/live", "/api/runs", "/api/agents", "/api/stream", "/pdf/x.pdf"):
    assert c.get(path).status_code == 401, path
assert c.post("/api/simulate").status_code == 401
assert c.get("/live?key=wrong").status_code == 401
form = c.get("/live")
assert form.status_code == 401 and 'name="key"' in form.text and 'action="/live"' in form.text, "an unauthenticated visitor gets a key form"
r = c.get("/dashboard", follow_redirects=False)
assert r.status_code == 307 and r.headers["location"] == "/live", "/dashboard is a friendly address for /live"
print("dashboard, API and PDFs locked ✓")
r = c.get("/live?key=s3cret key", headers={"x-forwarded-proto": "https"})
assert r.status_code == 303 and r.headers["location"] == "/live", r.headers
cookie = r.headers["set-cookie"]
assert "HttpOnly" in cookie and "Secure" in cookie and "s3cret" not in cookie, cookie   # cookie never contains the key itself
c2 = TestClient(app); c2.cookies.set("internify_key", cookie.split("=")[1].split(";")[0])
assert c2.get("/live").status_code == 200 and c2.get("/api/runs").status_code == 200
print("valid key -> cookie -> access ✓ (key not exposed in cookie)")

# brand assets are public (the home and privacy pages use them); the dashboard is the new agents-only room, light first
for path, kind in (("/assets/brand/logo-mark.svg", "image/svg+xml"), ("/assets/brand/wordmark.svg", "image/svg+xml"),
                   ("/assets/fonts/inter-latin-700-normal.woff2", "font/woff2")):
    r = c.get(path)
    assert r.status_code == 200 and r.headers["content-type"].startswith(kind.split("/")[0]), (path, r.status_code, r.headers.get("content-type"))
for page in ("/", "/privacy", "/terms"):
    assert "/assets/brand/logo-mark.svg" in c.get(page).text, page
for page in ("/", "/privacy", "/terms"):
    body = c.get(page).text
    assert "{{" not in body and "github.com/adyhafetz/internify/issues" in body, page          # the contact placeholder is always filled in
priv = c.get("/privacy").text
for must in ("Limited Use", "Google API Services User Data Policy", "OpenAI", "Telegram", "myaccount.google.com/permissions", "Google Drive", "Google Sheets"):
    assert must in priv, must
assert "Limited Use" in c.get("/").text, "the home page states the Google data policy too"
from bot import config as _bc
_bc.CONTACT_EMAIL = "me@example.com"
assert 'mailto:me@example.com' in c.get("/privacy").text
_bc.CONTACT_EMAIL = ""
live = c2.get("/live").text
assert 'data-theme="light"' in live, "light mode first"
for must in ("Telegram", "Scout", "Analyst", "Archivist", "Scribe", "Critic", "Publisher", "Curator", "Discoverer", "Matcher", 'data-s="tidy"', 'id="logBtn"', 'id="demoMenu"', "#2563EB", "#F97316", "Inter"):
    assert must in live, must
for gone in ("Show technical details", "Requirements matched", "Invented claims blocked", "Resume draft", "Your verified facts"):
    assert gone not in live, f"the old panel text {gone!r} should be gone"
print("brand assets public; /live is the agents-only room (7 seats incl. Telegram, Log button, light first)")
print("\n✅ dashboard access test passed")
