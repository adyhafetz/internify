"""Offline test of the Google changes: the one tracker sheet layout (the same as the README template), the limited
"only files Internify created" permission, moving an old sheet's rows into a new one, and the plain-language check. Google
itself is replaced by fakes, so this does not prove Google accepts the limited permission: confirm that in your Google Cloud
console (see docs/domain_setup.md)."""
from __future__ import annotations
import os, sys, tempfile

sys.path.insert(0, os.path.abspath("."))
tmp = tempfile.mkdtemp()
os.environ["SETUP_DIR"] = tmp
os.environ["OPENAI_API_KEY"] = "sk-test"
os.environ.pop("GOOGLE_ACCESS", None)

from bot import config
from bot.services import google_auth, google_check, sheet_layout as L
from bot.setup import envfile, google_setup, server
from fastapi.testclient import TestClient

# ---- 1. one layout, the same as the template
assert L.HEADERS == ["Company", "Job Title", "Location", "Job Description", "Job Requirements", "Job Link",
                     "Resume Link", "Resume Status", "Comment", "Status"], "the README template's columns, Status last"
assert L.SHEET_TITLE == "Internify — Application Tracker" and L.TAB_NAME == "Tracker"
assert L.STATUSES == ["New", "Applied", "In Review", "Interviewing", "Offer", "Rejected", "Withdrawn"]
reqs = L.format_requests(7)
kinds = [next(iter(r)) for r in reqs]
assert kinds[:2] == ["repeatCell", "setDataValidation"] and kinds.count("updateDimensionProperties") == len(L.HEADERS)
dv = reqs[1]["setDataValidation"]
assert dv["range"]["startColumnIndex"] == 9 and [v["userEnteredValue"] for v in dv["rule"]["condition"]["values"]] == L.STATUSES, "drop-down is on the Status column"
assert google_setup.HEADERS is L.HEADERS and google_setup.TAB_NAME == "Tracker", "the setup page uses the same layout"
print("1 ok: one layout (template columns, Status last, drop-down, widths) shared by the setup page")

# ---- 2. creating a sheet from the layout, with a fake Sheets service
class Req:
    def __init__(self, log, name, kw, result=None): self.log, self.name, self.kw, self.result = log, name, kw, result
    def execute(self): self.log.append((self.name, self.kw)); return self.result
class Values:
    def __init__(self, log, data=None): self.log, self.data = log, data or {}
    def update(self, **kw): return Req(self.log, "values.update", kw)
    def get(self, **kw): return Req(self.log, "values.get", kw, self.data)
class Spreadsheets:
    def __init__(self, log, data=None): self.log, self.data = log, data
    def create(self, **kw): return Req(self.log, "create", kw, {"spreadsheetId": "NEW1", "sheets": [{"properties": {"sheetId": 11}}]})
    def values(self): return Values(self.log, self.data)
    def batchUpdate(self, **kw): return Req(self.log, "batchUpdate", kw)
class Sheets:
    def __init__(self, log, data=None): self.log, self.data = log, data
    def spreadsheets(self): return Spreadsheets(self.log, self.data)
log = []
sid, tab = L.create_sheet(Sheets(log))
assert (sid, tab) == ("NEW1", 11)
assert log[0][1]["body"]["properties"]["title"] == "Internify — Application Tracker" and log[0][1]["body"]["sheets"][0]["properties"]["gridProperties"]["frozenRowCount"] == 1
assert log[1][0] == "values.update" and log[1][1]["body"]["values"] == [L.HEADERS] and log[1][1]["range"] == "Tracker!A1:J1"
assert log[2][0] == "batchUpdate" and log[2][1]["body"]["requests"] == L.format_requests(11)
print("2 ok: a new tracker sheet is created from the layout")

# ---- 3. moving rows from an old sheet (another column order, an extra column, a short row, a blank row)
old = [["Company", "Job Title", "Location", "Job Description", "Job Requirements", "Job Link", "Status", "Resume Link", "Resume Status", "Comment", "My notes"],
       ["Shopee", "Data Intern", "KL", "d", "r", "https://s/1", "Applied", "https://drive/1", "Good", "Match 72%", "call Ali"],
       ["Experian", "AI Intern", "Cyberjaya", "d2", "r2", "https://e/2", "Interviewing"],
       ["", "", "", "", "", "", "", "", "", "", ""]]
rows = L.map_rows(old)
assert len(rows) == 2 and rows[0] == ["Shopee", "Data Intern", "KL", "d", "r", "https://s/1", "https://drive/1", "Good", "Match 72%", "Applied"], rows[0]
assert rows[1] == ["Experian", "AI Intern", "Cyberjaya", "d2", "r2", "https://e/2", "", "", "", "Interviewing"], "a short row is padded, the Status is kept"
assert L.map_rows([]) == [] and L.map_rows([["Company"]]) == []
old_log, new_log = [], []
class FakeBuild:   # google_setup._services(creds): old creds read, new creds write
    def __init__(self): self.by = {"old": Sheets(old_log, {"values": old}), "new": Sheets(new_log)}
fb = FakeBuild()
google_setup._services = lambda creds: (None, fb.by[creds])
n = google_setup.copy_rows("old", "OLDSHEET", "Tracker", "new", "NEW1")
assert n == 2 and old_log[0][1]["spreadsheetId"] == "OLDSHEET" and old_log[0][1]["range"] == "Tracker"
assert new_log[0][1]["spreadsheetId"] == "NEW1" and new_log[0][1]["range"] == "Tracker!A2" and new_log[0][1]["body"]["values"] == rows
assert not any(name in ("values.update", "batchUpdate", "create") for name, _ in old_log), "the old sheet is only read"
assert google_setup.copy_rows("old", "OLDSHEET", "", "new", "NEW1") == 2
print("3 ok: rows move into the layout by header name; the old sheet is only read")

# ---- 4. which permission is asked for
assert config.GOOGLE_ACCESS == "full" and google_auth.scopes() == google_auth.FULL_SCOPES, "an install with no GOOGLE_ACCESS keeps the full permission"
config.GOOGLE_ACCESS = "app"
assert google_auth.scopes() == ["https://www.googleapis.com/auth/drive.file"]
config.GOOGLE_ACCESS = "full"
captured = []
import google_auth_oauthlib.flow as _flow
class _F:
    @staticmethod
    def from_client_config(cfg, scopes, redirect_uri): captured.append(scopes); return object()
_flow.Flow = _F
google_setup.make_flow("cid", "sec", "http://localhost:8765/")
assert captured[-1] == ["https://www.googleapis.com/auth/drive.file"], "a new sign-in on the setup page asks only for files Internify creates"
print("4 ok: new sign-ins ask for the limited permission; an install without GOOGLE_ACCESS keeps the full one")

# ---- 5. the plain-language hint when the limited login can't open a sheet made by hand
config.GOOGLE_ACCESS = "app"
hint = google_check._hint(Exception("<HttpError 404 File not found: 1AbC>"))
assert "limited kind" in hint and "Move to a new tracker sheet" in hint and "GOOGLE_ACCESS=full" in hint, hint
config.GOOGLE_ACCESS = "full"
assert "limited kind" not in google_check._hint(Exception("<HttpError 404 File not found>"))
print("5 ok: the check explains why a hand-made sheet can't be opened, and how to fix it")

# ---- 6. the setup page: the old sheet's rows move only when asked, and nothing is orphaned
c = TestClient(server.app)
env_path = os.path.join(tmp, ".env")
open(os.path.join(tmp, ".env.example"), "w").write(open(".env.example").read())
envfile.write_env(env_path, os.path.join(tmp, ".env.example"), {"GOOGLE_CLIENT_ID": "oldcid", "GOOGLE_CLIENT_SECRET": "oldsec",
                  "GOOGLE_REFRESH_TOKEN": "1//old", "GOOGLE_SHEET_ID": "OLDSHEET", "GOOGLE_SHEET_NAME": "Tracker", "GOOGLE_DRIVE_FOLDER_ID": "OLDFOLDER",
                  "OPENAI_API_KEY": "sk-a", "TELEGRAM_BOT_TOKEN": "123:abc", "TELEGRAM_ALLOWED_USER_IDS": "42"})
class Creds: refresh_token = "1//new"
server.state["google"] = {"creds": Creds(), "client_id": "newcid", "client_secret": "newsec"}
assert c.get("/api/google/status").json()["existing_sheet"] is True, "an earlier full-access setup with a sheet is offered the move"
made = []
google_setup.create_tracker = lambda creds: (made.append(1), {"GOOGLE_SHEET_ID": "NEW1", "GOOGLE_SHEET_NAME": "Tracker", "GOOGLE_DRIVE_FOLDER_ID": "NEWFOLDER"})[1]
google_setup.old_credentials = lambda env: "old"
copied = []
google_setup.copy_rows = lambda oc, osid, otab, nc, nsid: (copied.append((oc, osid, otab, nc is server.state["google"]["creds"], nsid)), 2)[1]
base = {"provider": "openai", "ai_key": "", "telegram_token": "", "user_id": "", "models": {}, "google": {}}
r = c.post("/api/save", json=base).json()
assert not r["ok"] and "current tracker sheet would stop working" in r["message"], "signing in without creating the new sheet must not orphan the old one"
assert envfile.read_env(env_path)["GOOGLE_REFRESH_TOKEN"] == "1//old"
r = c.post("/api/google/create", json={"copy_rows": False}).json()
assert r["ok"] and not copied and "Copied" not in r["message"]
server.state["google"]["ids"] = None
r = c.post("/api/google/create", json={"copy_rows": True}).json()
assert r["ok"] and copied == [("old", "OLDSHEET", "Tracker", True, "NEW1")] and "Copied 2 rows" in r["message"] and "untouched" in r["message"], r
r = c.post("/api/save", json=base).json()
got = envfile.read_env(env_path)
assert r["ok"] and got["GOOGLE_ACCESS"] == "app" and got["GOOGLE_SHEET_ID"] == "NEW1" and got["GOOGLE_REFRESH_TOKEN"] == "1//new" and got["GOOGLE_DRIVE_FOLDER_ID"] == "NEWFOLDER"
assert c.get("/api/google/status").json()["existing_sheet"] is False, "once moved, the move isn't offered again"
# a failed copy is reported plainly and nothing is lost
google_setup.copy_rows = lambda *a: (_ for _ in ()).throw(RuntimeError("quota"))
envfile.write_env(env_path, os.path.join(tmp, ".env.example"), {"GOOGLE_ACCESS": "full", "GOOGLE_SHEET_ID": "OLDSHEET", "GOOGLE_REFRESH_TOKEN": "1//old"})
server.state["google"] = {"creds": Creds(), "client_id": "newcid", "client_secret": "newsec"}
r = c.post("/api/google/create", json={"copy_rows": True}).json()
assert r["ok"] and "could not copy" in r["message"] and "untouched" in r["message"] and "NEW1" in r["sheet_url"], r
# an install already on the limited login has nothing to move
envfile.write_env(env_path, os.path.join(tmp, ".env.example"), {"GOOGLE_ACCESS": "app"})
server.state["google"]["ids"] = None
r = c.post("/api/google/create", json={"copy_rows": True}).json()
assert r["ok"] and "Copied" not in r["message"] and "could not copy" not in r["message"], r
print("6 ok: setup page: the old rows move only when asked, the old sheet is never orphaned, the new login is the limited one")
print("\n✅ google test passed")
