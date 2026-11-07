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
print("dashboard, API and PDFs locked ✓")
r = c.get("/live?key=s3cret key", headers={"x-forwarded-proto": "https"})
assert r.status_code == 303 and r.headers["location"] == "/live", r.headers
cookie = r.headers["set-cookie"]
assert "HttpOnly" in cookie and "Secure" in cookie and "s3cret" not in cookie, cookie   # cookie never contains the key itself
c2 = TestClient(app); c2.cookies.set("internify_key", cookie.split("=")[1].split(";")[0])
assert c2.get("/live").status_code == 200 and c2.get("/api/runs").status_code == 200
print("valid key -> cookie -> access ✓ (key not exposed in cookie)")
print("\n✅ dashboard access test passed")
