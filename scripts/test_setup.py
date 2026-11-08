"""Offline test of the setup wizard: writing .env, testing keys, finding your Telegram ID, and the Gemini switch.
Every outgoing request is replaced by a fake."""
from __future__ import annotations
import json, os, subprocess, sys, tempfile
import httpx

sys.path.insert(0, os.path.abspath("."))
tmp = tempfile.mkdtemp()
os.environ["SETUP_DIR"] = tmp
os.environ["OPENAI_API_KEY"] = "sk-test"
from fastapi.testclient import TestClient
from bot.setup import envfile, server

env_path = os.path.join(tmp, ".env")
open(os.path.join(tmp, ".env.example"), "w").write(open(".env.example").read())
c = TestClient(server.app)

# ---- E: the .env writer
envfile.write_env(env_path, os.path.join(tmp, ".env.example"), {"OPENAI_API_KEY": "sk-abc", "SCAN_TIME": "09:30", "CONTACT_EMAIL": "me@x.com"})
got = envfile.read_env(env_path)
assert got["OPENAI_API_KEY"] == "sk-abc" and got["SCAN_TIME"] == "09:30" and got["CONTACT_EMAIL"] == "me@x.com"
assert got["TELEGRAM_BOT_TOKEN"] == "", "example placeholders are not treated as real values"
assert "your-telegram-bot-token-here" not in open(env_path).read()
assert "# AI models" in open(env_path).read(), "starts from the example, comments included"
open(env_path, "a").write("MY_OWN=keep me\n")
envfile.write_env(env_path, os.path.join(tmp, ".env.example"), {"OPENAI_API_KEY": "sk-new", "NOTE": "has space # and hash"})
got = envfile.read_env(env_path)
assert got["OPENAI_API_KEY"] == "sk-new" and got["MY_OWN"] == "keep me" and got["NOTE"] == "has space # and hash"
assert os.path.exists(env_path + ".bak") and "sk-abc" in open(env_path + ".bak").read(), "the old file is kept"
try: envfile.write_env(env_path, "", {"A": "x\ny"}); raise SystemExit("newline must be refused")
except ValueError: pass
print("E ok: .env is written from the example, keeps your own lines, quotes odd values, keeps a backup")

# ---- M: choosing models
assert server.pick_openai_models({"gpt-5.4-nano", "gpt-5.4-mini", "text-embedding-3-small"}) == {
    "EXTRACTOR_MODEL": "gpt-5.4-nano", "CRITIC_MODEL": "gpt-5.4-mini", "GENERATOR_MODEL": "gpt-5.4-mini", "EMBEDDING_MODEL": "text-embedding-3-small"}
older = server.pick_openai_models({"gpt-4o", "gpt-4o-mini"})
assert older["EXTRACTOR_MODEL"] == "gpt-4o-mini" and older["GENERATOR_MODEL"] == "gpt-4o", "falls back when the account lacks the new ones"
items = [{"name": f"models/{n}", "supportedGenerationMethods": m} for n, m in [
    ("gemini-2.0-flash", ["generateContent"]), ("gemini-2.5-flash", ["generateContent"]), ("gemini-2.5-flash-lite", ["generateContent"]),
    ("gemini-2.5-flash-image", ["generateContent"]), ("gemini-2.5-pro", ["generateContent"]), ("gemini-2.5-flash-preview-tts", ["generateContent"]),
    ("gemini-embedding-001", ["embedContent"]), ("text-embedding-004", ["embedContent"])]]
g = server.pick_gemini_models(items)
assert g == {"EXTRACTOR_MODEL": "gemini-2.5-flash-lite", "CRITIC_MODEL": "gemini-2.5-flash", "GENERATOR_MODEL": "gemini-2.5-flash", "EMBEDDING_MODEL": "gemini-embedding-001"}, g
assert server.pick_gemini_models([{"name": "models/other", "supportedGenerationMethods": []}]) is None
print("M ok: models are chosen from what the account really offers, with a fallback")

# ---- T: testing keys (fake the network)
calls = []
def fake_get(url, **kw):
    calls.append((url, kw))
    def R(code, data): return httpx.Response(code, json=data, request=httpx.Request("GET", url))
    if "api.openai.com" in url:
        return R(200, {"data": [{"id": "gpt-5.4-nano"}, {"id": "gpt-5.4-mini"}, {"id": "text-embedding-3-small"}]}) if "good" in kw["headers"]["Authorization"] else R(401, {})
    if "generativelanguage" in url:
        return R(200, {"models": items}) if kw["params"]["key"] == "good" else R(400, {})
    if "getMe" in url:
        return R(200, {"ok": True, "result": {"username": "my_internify_bot"}}) if "123456789:" in url else R(401, {"ok": False})
    if "getUpdates" in url:
        if "offset" in kw.get("params", {}): return R(200, {"ok": True, "result": []})
        return R(200, {"ok": True, "result": SEEN})
    if "scrapingant" in url:
        return R(200, {}) if kw["params"]["x-api-key"] == "good" else R(403, {})
    raise AssertionError(url)
server._get = fake_get
SEEN = []
r = c.post("/api/test/ai", json={"provider": "openai", "key": "good-key"}).json()
assert r["ok"] and r["models"]["GENERATOR_MODEL"] == "gpt-5.4-mini"
assert not c.post("/api/test/ai", json={"provider": "openai", "key": "bad"}).json()["ok"]
r = c.post("/api/test/ai", json={"provider": "gemini", "key": "good"}).json()
assert r["ok"] and r["models"]["CRITIC_MODEL"] == "gemini-2.5-flash"
assert "rejected" in c.post("/api/test/ai", json={"provider": "gemini", "key": "nope"}).json()["message"]
assert not c.post("/api/test/ai", json={"provider": "openai", "key": " "}).json()["ok"]
TOKEN = "123456789:" + "A" * 30
assert c.post("/api/test/telegram", json={"token": TOKEN}).json()["username"] == "my_internify_bot"
assert "doesn't look like" in c.post("/api/test/telegram", json={"token": "hello"}).json()["message"]
assert not c.post("/api/test/telegram", json={"token": "999:" + "B" * 30}).json()["ok"]
assert c.post("/api/test/scrapingant", json={"key": "good"}).json()["ok"] and not c.post("/api/test/scrapingant", json={"key": "x"}).json()["ok"]
print("T ok: OpenAI, Gemini, Telegram and ScrapingAnt keys are checked, bad ones get a plain explanation")

# ---- D: finding your Telegram ID
r = c.post("/api/telegram/detect", json={"token": TOKEN}).json()
assert r["ok"] and not r["found"] and "press Start" in r["message"]
SEEN = [{"update_id": 7, "message": {"chat": {"type": "private"}, "from": {"id": 424242, "first_name": "Ady", "last_name": "H"}}}]
r = c.post("/api/telegram/detect", json={"token": TOKEN}).json()
assert r["found"] and r["user_id"] == "424242" and r["name"] == "Ady H"
assert any("offset" in kw.get("params", {}) and kw["params"]["offset"] == 8 for _, kw in calls), "the message is marked read so the bot won't answer it later"
print("D ok: your Telegram ID is found from your first message")

# ---- S: saving
base = {"provider": "openai", "ai_key": "sk-live-1", "telegram_token": TOKEN, "user_id": "424242", "scan_time": "07:45", "scan_timezone": "Asia/Kuala_Lumpur",
        "models": {"EXTRACTOR_MODEL": "gpt-5.4-nano", "CRITIC_MODEL": "gpt-5.4-mini", "GENERATOR_MODEL": "gpt-5.4-mini", "EMBEDDING_MODEL": "text-embedding-3-small"},
        "scrapingant_key": "ant-1", "contact_email": "me@x.com", "google": {"GOOGLE_SHEET_ID": "sheet1", "NOT_ALLOWED": "x"}}
def save(**over): return c.post("/api/save", json={**base, **over}).json()
assert "AI key" in save(ai_key="")["message"] or os.path.exists(env_path)   # an earlier key is kept, so this may succeed
os.remove(env_path)
assert "AI key" in save(ai_key="")["message"]
assert "bot token" in save(telegram_token="")["message"]
assert "Telegram ID" in save(user_id="")["message"]
assert "numbers" in save(user_id="abc")["message"]
assert "time" in save(scan_time="25:99")["message"] and "timezone" in save(scan_timezone="Mars/Base")["message"]
assert save(user_id="", anyone_can_use=True)["ok"]
assert not c.post("/api/finish").json()["ok"] or server.state["saved"]
r = save()
assert r["ok"]
got = envfile.read_env(env_path)
assert got["AI_PROVIDER"] == "openai" and got["OPENAI_API_KEY"] == "sk-live-1" and got["TELEGRAM_ALLOWED_USER_IDS"] == "424242"
assert got["SCAN_TIME"] == "07:45" and got["SCAN_TIMEZONE"] == "Asia/Kuala_Lumpur" and got["SCRAPINGANT_API_KEY"] == "ant-1" and got["GOOGLE_SHEET_ID"] == "sheet1"
assert "NOT_ALLOWED" not in got and got["GENERATOR_MODEL"] == "gpt-5.4-mini"
assert oct(os.stat(env_path).st_mode & 0o777) == "0o600", "only you can read the file"
# running the wizard again keeps what you already have when you leave a box empty
r = save(ai_key="", telegram_token="", user_id=""); assert r["ok"]
assert envfile.read_env(env_path)["OPENAI_API_KEY"] == "sk-live-1"
st = c.get("/api/state").json()
assert st["has"]["OPENAI_API_KEY"] and st["has"]["TELEGRAM_BOT_TOKEN"] and "sk-live-1" not in json.dumps(st), "the page is told what is set, never the secrets"
r = save(provider="gemini", ai_key="AIza-g", models={"EXTRACTOR_MODEL": "gemini-2.5-flash-lite", "CRITIC_MODEL": "gemini-2.5-flash", "GENERATOR_MODEL": "gemini-2.5-flash", "EMBEDDING_MODEL": "gemini-embedding-001"}); assert r["ok"]
assert envfile.read_env(env_path)["AI_PROVIDER"] == "gemini" and envfile.read_env(env_path)["GEMINI_API_KEY"] == "AIza-g"
# G: a model name from the wrong service is never saved
r = save(provider="gemini", ai_key="AIza-g", models={"EXTRACTOR_MODEL": "gpt-5.4-nano", "CRITIC_MODEL": "gpt-5.4-mini", "GENERATOR_MODEL": "gpt-5.4-mini", "EMBEDDING_MODEL": "text-embedding-3-small"})
assert not r["ok"] and "Gemini model names" in r["message"], r
r = save(provider="gemini", ai_key="AIza-g", models={}); assert not r["ok"], "no models for Gemini: asks you to check the key again"
r = save(provider="openai", ai_key="sk-back", models={"EXTRACTOR_MODEL": "gemini-2.5-flash-lite", "CRITIC_MODEL": "gemini-2.5-flash", "GENERATOR_MODEL": "gemini-2.5-flash", "EMBEDDING_MODEL": "gemini-embedding-001"})
got = envfile.read_env(env_path)
assert r["ok"] and got["AI_PROVIDER"] == "openai" and got["EXTRACTOR_MODEL"] == "gpt-5.4-nano" and got["EMBEDDING_MODEL"] == "text-embedding-3-small", got
r = save(provider="gemini", ai_key="AIza-g", models={"EXTRACTOR_MODEL": "gemini-2.5-flash-lite", "CRITIC_MODEL": "gemini-2.5-flash", "GENERATOR_MODEL": "gemini-2.5-flash", "EMBEDDING_MODEL": "gemini-embedding-001"})
got = envfile.read_env(env_path)
assert r["ok"] and got["GENERATOR_MODEL"] == "gemini-2.5-flash" and got["EMBEDDING_MODEL"] == "gemini-embedding-001"
print("G ok: switching service never leaves the other service's model names behind")
# L: Google sign-in on the setup page (the real Google is replaced by fakes)
from bot.setup import google_setup
class FakeCreds: refresh_token = "1//refresh-xyz"
class FakeFlow:
    credentials = FakeCreds()
    def authorization_url(self, **kw): assert kw == {"access_type": "offline", "prompt": "consent"}; return "https://accounts.google.com/o/oauth2/auth?x=1", "STATE1"
    def fetch_token(self, code): assert code == "good-code"
flows = []
google_setup.make_flow = lambda cid, secret, redirect: (flows.append((cid, secret, redirect)), FakeFlow())[1]
google_setup.create_tracker = lambda creds: {"GOOGLE_SHEET_ID": "SHEET9", "GOOGLE_SHEET_NAME": "Tracker", "GOOGLE_DRIVE_FOLDER_ID": "FOLDER9"}
assert not c.post("/api/google/start", json={"client_id": "", "client_secret": "x"}).json()["ok"]
assert not c.post("/api/google/create").json()["ok"], "connect first"
r = c.post("/api/google/start", json={"client_id": "cid.apps.googleusercontent.com", "client_secret": "GOCSPX-s"}).json()
assert r["ok"] and r["auth_url"].startswith("https://accounts.google.com") and flows[-1][2] == "http://localhost:8765/"
assert not c.get("/api/google/status").json()["connected"]
assert "not valid" in c.get("/", params={"code": "good-code", "state": "WRONG"}).text
assert "cancelled" in c.get("/", params={"error": "access_denied"}).text
assert "Connected to Google" in c.get("/", params={"code": "good-code", "state": "STATE1"}).text
assert c.get("/api/google/status").json() == {"connected": True, "created": False}
made = c.post("/api/google/create").json()
assert made["ok"] and "SHEET9" in made["sheet_url"] and "FOLDER9" in made["folder_url"]
r = save(dashboard_key="abc123def456", google={})
got = envfile.read_env(env_path)
assert r["ok"] and got["GOOGLE_REFRESH_TOKEN"] == "1//refresh-xyz" and got["GOOGLE_CLIENT_ID"] == "cid.apps.googleusercontent.com" and got["GOOGLE_CLIENT_SECRET"] == "GOCSPX-s"
assert got["GOOGLE_SHEET_ID"] == "SHEET9" and got["GOOGLE_DRIVE_FOLDER_ID"] == "FOLDER9" and got["GOOGLE_SHEET_NAME"] == "Tracker" and got["DASHBOARD_KEY"] == "abc123def456"
assert "1//refresh-xyz" not in json.dumps(c.get("/api/state").json()) and "1//refresh-xyz" not in json.dumps(made), "the login never goes through the browser"
assert not save(dashboard_key="has space")["ok"]
r = save(google={"GOOGLE_SHEET_ID": "mine"}); assert envfile.read_env(env_path)["GOOGLE_SHEET_ID"] == "mine", "values typed by hand win"
# the tracker has exactly the columns the bot writes
import bot.services.sheets as _sheets, inspect
assert all(f'"{h}"' in inspect.getsource(_sheets.append_application) for h in google_setup.HEADERS)
print("L ok: Google sign-in, folder and sheet creation, login kept on the server, dashboard key saved")
stopped = []
server.state["finish"] = lambda: stopped.append(1)
assert c.post("/api/finish").json()["ok"] and stopped and server.state["done"]
page_html = c.get("/").text
assert "Set up Internify" in page_html and "prefers-color-scheme" not in page_html and 'id="theme"' in page_html and 'localStorage.getItem("internify-setup-theme") || "light"' in page_html, "light unless you pick dark"
assert "Set up Internify" in page_html and c.get("/assets/brand/logo-mark.svg").status_code == 200
print("S ok: the form is checked, .env is written (private), a rerun keeps your keys, Finish stops the wizard")

# ---- P: the Gemini switch in the app itself
def probe(env):
    code = ("import service.config as c, service.clients as k; "
            "print(c.OPENAI_API_KEY, c.AI_BASE_URL, k.openai_client.base_url, k.instructor_client.mode.name)")
    out = subprocess.run([sys.executable, "-c", code], env={**os.environ, "OPENAI_API_KEY": "sk-o", "GEMINI_API_KEY": "gk", **env}, capture_output=True, text=True, cwd=os.getcwd())
    return out.stdout.split() if out.returncode == 0 else out.stderr
oa = probe({"AI_PROVIDER": "openai"})
assert oa[0] == "sk-o" and oa[1] == "None" and "api.openai.com" in oa[2], oa
gm = probe({"AI_PROVIDER": "gemini"})
assert gm[0] == "gk" and "generativelanguage.googleapis.com/v1beta/openai" in gm[1] and gm[3] == "JSON", gm
print("P ok: AI_PROVIDER=gemini points the app at Gemini's compatible endpoint with JSON answers; OpenAI is unchanged")
print("\nALL SETUP TESTS PASSED")
