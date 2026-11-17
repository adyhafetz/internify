"""Offline test of connecting Google in the hosted version: the one-time connect link (previews can't use it up), the sign-in
coming back, the folder and sheet created in the person's own Drive, the login stored encrypted and used only for that person,
disconnecting and deleting an account, and that nobody can reach another person's connection. Google, Drive and Telegram are
replaced by fakes: this does NOT prove Google accepts the app, which must be tried once for real (see docs/domain_setup.md)."""
from __future__ import annotations
import asyncio, os, sqlite3, sys, tempfile, types

sys.path.insert(0, os.path.abspath("."))
tmp = tempfile.mkdtemp()
os.environ.update(OPENAI_API_KEY="sk-test", DATABASE_PATH=os.path.join(tmp, "t.db"), OUTPUT_DIR=os.path.join(tmp, "out"),
                  FACT_STORE_PATH=os.path.join(tmp, "out", "data", "facts.json"), SKELETON_PATH=os.path.join(tmp, "out", "data", "resume_skeleton.json"),
                  EMBEDDING_CACHE_PATH=os.path.join(tmp, "out", "data", "embeddings_cache.json"), TELEGRAM_BOT_TOKEN="123:abc")
for k in ("MODE", "OUTCOMES_PATH", "TELEGRAM_ALLOWED_USER_IDS", "INTERNIFY_CLOUD_PREVIEW", "MARKETING_SITE", "PUBLIC_BOT_USERNAME"): os.environ.pop(k, None)

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from urllib.parse import parse_qs, urlparse
from bot import config, jobflow
from bot.dashboard import server
from bot.handlers import profile_hub as H
from bot.services import accounts, db, drive, google_auth, google_link, sheets, watchlist, websession
from bot.setup import google_setup
from service import users

db.init_db()
run = asyncio.run
BASE = "https://internify.example.dev"
KEY = Fernet.generate_key().decode()
config.PUBLIC_URL, config.BOT_USERNAME, config.TELEGRAM_BOT_TOKEN = BASE, "InternifyBot", "123:abc"
config.GOOGLE_CLIENT_ID, config.GOOGLE_CLIENT_SECRET, config.GOOGLE_TOKEN_KEY = "web-client.apps.googleusercontent.com", "GOCSPX-web", KEY
def anon(): return TestClient(server.app, follow_redirects=False)

# ---------------------------------------------------------------- fakes
posts = []      # every outgoing POST: Google's revoke, Telegram's sendMessage
class Resp:
    status_code = 200
google_link.httpx.post = lambda url, **kw: (posts.append((url, kw)), Resp())[1]
flows = []
class FakeCreds:
    def __init__(self, token): self.refresh_token = token
class FakeFlow:
    def __init__(self, state): self.state, self.credentials = state, None; flows.append(self)
    def authorization_url(self, **kw):
        assert kw == {"access_type": "offline", "prompt": "consent", "include_granted_scopes": "false"}, kw
        return f"https://accounts.google.com/o/oauth2/auth?client_id=x&state={self.state}", self.state
    def fetch_token(self, code):
        if code == "bad": raise RuntimeError("invalid_grant")
        self.credentials = FakeCreds(None if code == "norefresh" else f"1//rt-{code}")
google_link.make_flow = lambda state=None: FakeFlow(state)
created, written, fail_create = [], [], []
def fake_create(creds):
    if fail_create: raise RuntimeError("quota")
    n = len(created) + 1; created.append(creds.refresh_token)
    return {"GOOGLE_SHEET_ID": f"SHEET{n}", "GOOGLE_SHEET_NAME": "Tracker", "GOOGLE_DRIVE_FOLDER_ID": f"FOLDER{n}"}
google_setup.create_tracker = fake_create
google_setup.write_rows = lambda creds, sid, rows: (written.append((sid, rows)), len(rows))[1]
usable = [False]
google_link._usable = lambda creds, sheet_id, folder_id: usable[0]

users.MODE = "cloud"
A, B, C = (accounts.create(t, n) for t, n in ((1001, "Alice"), (1002, "Bob"), (1003, "Cara")))
for a in (A, B, C):
    with users.use(a.id): watchlist.set_setting("owner_chat_id", str(a.telegram_id))

# ---- 1. is it set up? and the login is stored encrypted
assert google_link.enabled()
for name, val in (("GOOGLE_CLIENT_ID", ""), ("GOOGLE_CLIENT_SECRET", ""), ("GOOGLE_TOKEN_KEY", ""), ("PUBLIC_URL", ""), ("GOOGLE_TOKEN_KEY", "not-a-key")):
    old = getattr(config, name); setattr(config, name, val); assert not google_link.enabled(), name; setattr(config, name, old)
users.MODE = "single"; assert not google_link.enabled(), "single mode keeps its own login in .env"; users.MODE = "cloud"
assert google_link.decrypt(google_link.encrypt("1//secret")) == "1//secret" and google_link.encrypt("x") != google_link.encrypt("x")
google_link.save(A.id, "1//plain-refresh-token", "S", "F")
with sqlite3.connect(os.environ["DATABASE_PATH"]) as cx: raw = " ".join(str(v) for row in cx.execute("SELECT * FROM google_links") for v in row)
assert "1//plain-refresh-token" not in raw, "the login is never stored as plain text"
assert google_link.refresh_token(A.id) == "1//plain-refresh-token"
config.GOOGLE_TOKEN_KEY = Fernet.generate_key().decode()
assert google_link.refresh_token(A.id) is None, "a different key can't open it (the person simply connects again)"
config.GOOGLE_TOKEN_KEY = KEY; google_link.disconnect(A.id, revoke=False); posts.clear()
print("1 ok: needs the Web client, a site address and a working key; the login is stored encrypted and a wrong key can't open it")

# ---- 2. the Telegram side: a one-time link as a button
with users.use(A.id):
    text, kb = H.google_screen()
    btn = kb.inline_keyboard[0][0]
    assert "Connect Google" in text and "your own Google Drive" in text and "not mine" in text and btn.url.startswith(BASE + "/connect/google/")
    tok = btn.url.rsplit("/", 1)[1]
    assert websession.peek(tok, role=("google",)) and not websession.peek(tok), "a connect link isn't a sign-in link"
    assert websession.sign_in(tok) is None and websession.peek(tok, role=("google",)), "...and can't be used to sign in (nor does trying burn it)"
    lst = H.list_screen()[1].inline_keyboard
    assert "🔗 Connect Google" in [b.text for r in lst for b in r]
config.GOOGLE_TOKEN_KEY = ""
with users.use(A.id): assert "isn't available" in H.google_screen()[0] and "🔗 Connect Google" not in [b.text for r in H.list_screen()[1].inline_keyboard for b in r]
config.GOOGLE_TOKEN_KEY = KEY
users.MODE = "single"; assert "🔗 Connect Google" not in [b.text for r in H.list_screen()[1].inline_keyboard for b in r]; users.MODE = "cloud"
print("2 ok: /profile offers Connect Google (hosted only, when set up) as a one-time button that is not a sign-in link")

# ---- 3. the web flow
with users.use(A.id):
    import bot.services.db as _db
    for co, jt, st, link in (("Shopee", "Data Intern", "Ready", "https://s/1"), ("Grab", "SWE Intern", "Applied", "https://g/1")):
        aid = _db.upsert_application(co, jt, job_link=link, status="Processing"); _db.update_application(aid, status=st, comment="ok", drive_link="")
cl = anon()
r = cl.get(f"/connect/google/{tok}")
assert r.status_code == 200 and "Connect your Google Drive" in r.text and "Continue with Google" in r.text and f'action="/connect/google/{tok}"' in r.text and 'noindex' in r.text
assert "can open only those" in r.text and r.headers["referrer-policy"] == "no-referrer" and r.headers["cache-control"] == "no-store"
cl.get(f"/connect/google/{tok}"); assert websession.peek(tok, role=("google",)), "a link preview (just fetching the page) doesn't use it up"
r = cl.post(f"/connect/google/{tok}")
assert r.status_code == 303 and r.headers["location"].startswith("https://accounts.google.com/o/oauth2/auth") and r.headers["referrer-policy"] == "no-referrer"
state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
assert cl.post(f"/connect/google/{tok}").status_code == 400 and "expired" in cl.get(f"/connect/google/{tok}").text, "the link works once"
with sqlite3.connect(os.environ["DATABASE_PATH"]) as cx: assert state not in " ".join(str(v) for row in cx.execute("SELECT * FROM google_states") for v in row), "the state is stored hashed"
assert cl.get("/connect/google/not-a-token").status_code == 400
# coming back
assert "Nothing was connected" in cl.get("/connect/google/callback?error=access_denied").text and google_link.get(A.id) is None
assert cl.get("/connect/google/callback").status_code == 400 and cl.get("/connect/google/callback?code=x&state=forged").status_code == 400 and google_link.get(A.id) is None
posts.clear()
r = cl.get(f"/connect/google/callback?code=goodcode&state={state}")
assert r.status_code == 200 and "Google is connected" in r.text and "Created a folder" in r.text and "copied 2 applications" in r.text and 'noindex' in r.text and "t.me/InternifyBot" in r.text, r.text[:400]
link = google_link.get(A.id)
assert (link.sheet_id, link.folder_id, link.sheet_tab) == ("SHEET1", "FOLDER1", "Tracker") and google_link.refresh_token(A.id) == "1//rt-goodcode" and created == ["1//rt-goodcode"]
sid, rows = written[0]; assert sid == "SHEET1" and len(rows) == 2 and rows[0][:2] == ["Shopee", "Data Intern"] and rows[0][-1] == "New" and rows[1][-1] == "Applied", "the tracker came across, 'Ready' as New"
tg = [p for p in posts if "api.telegram.org" in p[0]]
assert len(tg) == 1 and tg[0][1]["data"]["chat_id"] == 1001 and "SHEET1" in tg[0][1]["data"]["text"] and "Google is connected" in tg[0][1]["data"]["text"], "the person is told in Telegram"
assert cl.get(f"/connect/google/callback?code=again&state={state}").status_code == 400 and created == ["1//rt-goodcode"], "a sign-in can't be replayed"
print("3 ok: the web flow: a button first, then Google; a forged, replayed or denied sign-in changes nothing; success saves the link and copies the tracker")

# failures leave nothing half done
for code, why in (("bad", "did not accept"), ("norefresh", "lasting login")):
    tok2 = websession.new_login(B.id, "google"); st = parse_qs(urlparse(cl.post(f"/connect/google/{tok2}").headers["location"]).query)["state"][0]
    r = cl.get(f"/connect/google/callback?code={code}&state={st}"); assert r.status_code == 400 and why in r.text and google_link.get(B.id) is None, code
tok2 = websession.new_login(B.id, "google"); st = parse_qs(urlparse(cl.post(f"/connect/google/{tok2}").headers["location"]).query)["state"][0]
fail_create.append(1); r = cl.get(f"/connect/google/callback?code=c2&state={st}")
assert r.status_code == 400 and "create your folder and sheet" in r.text and google_link.get(B.id) is None, "no folder or sheet made = nothing saved"
fail_create.clear()
config.GOOGLE_TOKEN_KEY = ""; assert cl.get(f"/connect/google/{tok}").status_code == 404 and cl.get("/connect/google/callback?code=a&state=b").status_code == 404; config.GOOGLE_TOKEN_KEY = KEY
users.MODE = "single"; assert cl.get(f"/connect/google/{tok}").status_code == 404; users.MODE = "cloud"
assert "Disallow: /connect/" in cl.get("/robots.txt").text
print("4 ok: a refused code, no lasting login or a failed folder/sheet creation saves nothing; not offered when not set up or in single mode")

# ---- 5. each person's connection is theirs alone
def connect(user, code):
    t = websession.new_login(user.id, "google"); st = parse_qs(urlparse(anon().post(f"/connect/google/{t}").headers["location"]).query)["state"][0]
    assert anon().get(f"/connect/google/callback?code={code}&state={st}").status_code == 200
connect(B, "bobcode")
seen_creds = []
# stand-ins for the Google libraries, so this runs without them and records which login each client was made with
sys.modules["gspread"] = types.SimpleNamespace(authorize=lambda creds: (seen_creds.append(("sheets", creds.refresh_token)), types.SimpleNamespace(tag=creds.refresh_token))[1])
gapi_discovery = types.SimpleNamespace(build=lambda *a, **kw: (seen_creds.append(("drive", kw["credentials"].refresh_token)), types.SimpleNamespace(tag=kw["credentials"].refresh_token))[1])
sys.modules["googleapiclient"] = types.SimpleNamespace(discovery=gapi_discovery); sys.modules["googleapiclient.discovery"] = gapi_discovery
with users.use(A.id): ca = google_auth.get_google_credentials(); ids_a = google_auth.ids(); sa, da = sheets._get_client(), drive._get_drive_service()
with users.use(B.id): cb = google_auth.get_google_credentials(); ids_b = google_auth.ids(); sb, db_ = sheets._get_client(), drive._get_drive_service()
with users.use(C.id): assert google_auth.get_google_credentials() is None and google_auth.ids() == ("", "Tracker", "") and sheets._get_client() is None and drive._get_drive_service() is None and sheets._get_worksheet() is None
assert ca.refresh_token == "1//rt-goodcode" and cb.refresh_token == "1//rt-bobcode" and ca.scopes == ["https://www.googleapis.com/auth/drive.file"], "limited to the files Internify made"
assert ca.client_id == config.GOOGLE_CLIENT_ID and ids_a == ("SHEET1", "Tracker", "FOLDER1") and ids_b == ("SHEET2", "Tracker", "FOLDER2")
assert (sa.tag, da.tag, sb.tag, db_.tag) == ("1//rt-goodcode", "1//rt-goodcode", "1//rt-bobcode", "1//rt-bobcode"), "each person's own clients"
seen_creds.clear()
with users.use(A.id): sheets._get_client(); drive._get_drive_service()
assert seen_creds == [], "clients are kept per person, not rebuilt"
users.MODE = "single"; config.GOOGLE_SHEET_ID, config.GOOGLE_SHEET_NAME, config.GOOGLE_DRIVE_FOLDER_ID = "ENVSHEET", "Tab", "ENVFOLDER"
assert google_auth.ids() == ("ENVSHEET", "Tab", "ENVFOLDER"), "single mode still uses .env"
config.GOOGLE_SHEET_ID = config.GOOGLE_DRIVE_FOLDER_ID = ""; config.GOOGLE_SHEET_NAME = "Tracker"; users.MODE = "cloud"
print("5 ok: each person's credentials, ids and Sheets/Drive clients are their own; strangers and single mode are unchanged")

# ---- 6. reconnecting keeps the files that still open, else makes new ones
n_created, n_written = len(created), len(written)
usable[0] = True; connect(A, "again1")
assert len(created) == n_created and len(written) == n_written and google_link.refresh_token(A.id) == "1//rt-again1" and google_link.get(A.id).sheet_id == "SHEET1", "same folder and sheet, new login"
usable[0] = False; connect(A, "again2")
assert len(created) == n_created + 1 and google_link.get(A.id).sheet_id.startswith("SHEET") and google_link.get(A.id).sheet_id != "SHEET1" and len(written) == n_written + 1
print("6 ok: reconnecting keeps the old folder and sheet if they still open, and makes new ones if not")

# ---- 7. disconnecting (and what the Telegram button does)
posts.clear()
with users.use(A.id):
    text, kb = H.google_screen(); assert "Google is connected" in text and [b.text for r in kb.inline_keyboard for b in r][:3] == ["📑 Open the tracker sheet", "📁 Open the resume folder", "Disconnect Google"]
    assert kb.inline_keyboard[0][0].url == google_link.get().sheet_url and "🔗 Google: connected" in [b.text for r in H.list_screen()[1].inline_keyboard for b in r]
    q = types.SimpleNamespace(message=types.SimpleNamespace(chat_id=1001))
    ask = run(H._handle(q, types.SimpleNamespace(user_data={}), "gd", [])); assert "Disconnect Google?" in ask[0] and "stay in your Drive" in ask[0] and google_link.get() is not None, "asks first"
    token_before = google_link.refresh_token()
    done = run(H._handle(q, types.SimpleNamespace(user_data={}), "gdy", []))
    assert "disconnected" in done[0] and google_link.get() is None and google_auth.get_google_credentials() is None and sheets._get_client() is None and drive._get_drive_service() is None
revokes = [p for p in posts if "oauth2.googleapis.com/revoke" in p[0]]; assert len(revokes) == 1 and revokes[0][1]["data"] == {"token": token_before}, "Google is asked to forget it"
with users.use(A.id): assert [r["company"] for r in db.all_applications()] == ["Shopee", "Grab"], "the tracker here is kept"
with users.use(B.id): assert google_link.get() is not None, "someone else's connection isn't touched"
google_link.httpx.post = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline"))
with users.use(B.id): assert google_link.disconnect() is True, "Google being unreachable never blocks a disconnect"
google_link.httpx.post = lambda url, **kw: (posts.append((url, kw)), Resp())[1]
print("8 ok: disconnecting asks first, revokes with Google, keeps the tracker, doesn't touch others, and works offline")

# ---- 8. deleting an account removes the connection too (the person's own Drive files are never touched)
connect(C, "carol"); posts.clear()
with sqlite3.connect(os.environ["DATABASE_PATH"]) as cx: cx.execute("INSERT INTO google_states (state_hash, user_id, expires_at) VALUES ('h', ?, 9e12)", (C.id,)); cx.commit()
t_c = websession.new_login(C.id, "google")
accounts.delete_account(C.id)
with sqlite3.connect(os.environ["DATABASE_PATH"]) as cx:
    assert cx.execute("SELECT COUNT(*) FROM google_links WHERE user_id = ?", (C.id,)).fetchone()[0] == 0 and cx.execute("SELECT COUNT(*) FROM google_states WHERE user_id = ?", (C.id,)).fetchone()[0] == 0
assert not websession.peek(t_c, role=("google",)) and [p for p in posts if "revoke" in p[0]], "login revoked, link and states gone"
assert not [p for p in posts if "drive" in p[0] and "delete" in p[0].lower()], "their files in their own Drive stay theirs"
print("9 ok: deleting an account revokes and forgets its Google connection")

# ---- 9. tracker rows, captions and status
rows = google_link.tracker_rows([{"company": "Co", "job_title": "T", "location": None, "job_description": "d" * 700, "job_requirements": "r", "job_link": "u", "drive_link": "dl",
                                  "resume_status": "Good", "comment": "c", "status": "Ready"}, {"company": "Co2", "job_title": "T2", "status": "Interviewing"}])
from bot.services import sheet_layout as L
assert len(rows[0]) == len(L.HEADERS) and rows[0][L.HEADERS.index("Status")] == "New" and rows[1][L.HEADERS.index("Status")] == "Interviewing" and rows[0][L.HEADERS.index("Location")] == ""
assert len(rows[0][L.HEADERS.index("Job Description")]) == 500 and rows[0][L.HEADERS.index("Resume Link")] == "dl"
out = jobflow.JobOutcome(ok=True, company="Co", role="R", score=80, status="Good")
cap = jobflow.build_caption(out); assert "Connect Google in /profile" in cap and "Saved on this computer only" not in cap
users.MODE = "single"; cap = jobflow.build_caption(out); assert "Saved on this computer only" in cap and "Not added to your Google Sheet" in cap; users.MODE = "cloud"
from bot import status
connect(B, "bob2")
with users.use(B.id): assert "Google Drive and Sheets: connected" in str(status.screen())
with users.use(A.id): assert "not connected: /profile, Connect Google" in str(status.screen())
config.GOOGLE_TOKEN_KEY = ""
with users.use(A.id): assert "not available on this server" in str(status.screen())
config.GOOGLE_TOKEN_KEY = KEY
from bot.services import google_check
with users.use(A.id): assert "not connected" in google_check.check_google_setup()[0]
print("10 ok: tracker rows follow the template; hosted captions point to Connect Google; Status and the check say where Google stands")

# ---- 10. the hosted welcome message mentions it
from bot import cloud
assert "Connect Google" in cloud.welcome_text("Sam", True)
users.MODE = "single"
print("\n✅ google connect test passed")
