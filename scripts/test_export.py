"""Offline test of the Excel export and of Export / Import my data (the .zip file): what goes in, what never does, that a
bad or hostile file is refused, and that importing replaces everything safely. Telegram and the AI are faked."""
from __future__ import annotations
import asyncio, io, json, os, sys, tempfile, types, zipfile

sys.path.insert(0, os.path.abspath("."))
tmp = tempfile.mkdtemp()
os.environ.update(OPENAI_API_KEY="sk-test", DATABASE_PATH=os.path.join(tmp, "t.db"), FACT_STORE_PATH=os.path.join(tmp, "a", "data", "facts.json"),
                  SKELETON_PATH=os.path.join(tmp, "a", "data", "resume_skeleton.json"), EMBEDDING_CACHE_PATH=os.path.join(tmp, "a", "data", "emb.json"),
                  OUTCOMES_PATH=os.path.join(tmp, "a", "data", "outcomes.json"), OUTPUT_DIR=os.path.join(tmp, "a"))
from bot import data_export, tracker
from bot.handlers import datafile
from bot.services import db, export_xlsx, sheet_layout, watchlist
from service import config, outcomes
from service.profile_data import _load_facts_raw, _load_skeleton_raw

db.init_db()
facts = _load_facts_raw(); sk = _load_skeleton_raw()      # the sample profile that ships with the code
assert facts and sk["header"]["name"]

# ---- 1. Excel export: the sheet's columns, text never read as a formula, the Status drop-down
a = db.upsert_application("Shopee", "Data Intern", "KL", "d" * 700, "r", "https://s/1", "Processing")
db.update_application(a, status="Ready", score=72.4, resume_status="Good", drive_link="https://drive/1", comment="=HYPERLINK(\"http://evil\",\"x\")")
b = db.upsert_application("Grab", "SWE Intern", "KL", "d", "r", "https://g/1", "Processing"); tracker._apply(b, db.INTERVIEW)
apps, _ = db.list_applications(None, 1000, 0)
from openpyxl import load_workbook
wb = load_workbook(io.BytesIO(export_xlsx.build(apps)))
ws = wb["Tracker"]
head = [c.value for c in ws[1]]
assert head[:10] == sheet_layout.HEADERS and head[10:] == ["Applied Date", "Deadline", "Match %"], head
rows = {r[0].value: [c.value for c in r] for r in ws.iter_rows(min_row=2)}
assert rows["Shopee"][head.index("Status")] == "New", "the internal 'Ready' is 'New' in the Sheet's words"
assert rows["Grab"][head.index("Status")] == "Interviewing" and rows["Shopee"][head.index("Match %")] == 72
assert len(rows["Shopee"][head.index("Job Description")]) == 500
cell = ws.cell(row=2 + list(rows).index("Shopee"), column=head.index("Comment") + 1)
assert cell.data_type == "s" and cell.value.startswith("="), "text from a job posting is never a formula"
assert ws.freeze_panes == "A2" and ws.data_validations.dataValidation and "Interviewing" in ws.data_validations.dataValidation[0].formula1
assert export_xlsx.filename().endswith(".xlsx") and load_workbook(io.BytesIO(export_xlsx.build([])))["Tracker"].max_row == 1
print("1 ok: Excel export: the sheet's columns, dates, drop-down, text never read as a formula")

# ---- 2. the button in /applied sends it
sent = []
class Bot:
    async def send_document(self, chat, document, filename, caption): sent.append((chat, len(document), filename, caption))
class Q:
    def __init__(self, data): self.data, self.answers = data, []
    message = types.SimpleNamespace(chat_id=7, text="", document=None, replies=[])
    async def answer(self, t="", **kw): self.answers.append(t)
    async def edit_message_text(self, *a, **k): pass
async def reply(*a, **k): Q.message.replies.append(a)
Q.message.reply_text = reply
import bot.services.sheets as sheets
sheets.read_statuses = lambda: None
q = Q("jb:X"); upd = types.SimpleNamespace(callback_query=q, effective_user=types.SimpleNamespace(id=1, username="t"), effective_message=q.message, effective_chat=types.SimpleNamespace(id=7))
asyncio.run(tracker.tracker_callback(upd, types.SimpleNamespace(bot=Bot(), application=None)))
assert sent and sent[0][2].endswith(".xlsx") and "2 applications" in sent[0][3], sent
text, kb = tracker.list_screen("o")
assert "📊 Send as Excel" in [b_.text for r in kb.inline_keyboard for b_ in r]
print("2 ok: the 📊 button in /applied sends the file")

# ---- 3. data file: what goes in, what never does
watchlist.add_source("https://boards.greenhouse.io/acme"); watchlist.mark_seen("https://x/1", "T", "C", "tailored")
for k, v in (("scan_roles", "backend intern"), ("scan_places", "Penang"), ("src_linkedin", "0"), ("owner_chat_id", "99"), ("tidy_pending", "[1]"), ("scan_last", "x")):
    watchlist.set_setting(k, v)
outcomes.record("app1", ["f1"], good=True)
os.environ["GOOGLE_REFRESH_TOKEN"] = "1//secret-token"; os.environ["OPENAI_API_KEY"] = "sk-live-secret"
blob = data_export.export_bytes()
z = zipfile.ZipFile(io.BytesIO(blob))
assert set(z.namelist()) <= data_export.MEMBERS and {"manifest.json", "tracker.json", "data/facts.json", "data/resume_skeleton.json", "data/outcomes.json"} <= set(z.namelist())
everything = b"".join(z.read(n) for n in z.namelist())
assert b"1//secret-token" not in everything and b"sk-live-secret" not in everything, "no keys or logins in the file"
tr = json.loads(z.read("tracker.json"))
assert set(tr["settings"]) == {"scan_roles", "scan_places", "src_linkedin"}, "only job-search settings move (no chat id, no tidy state)"
assert len(tr["applications"]) == 2 and len(tr["watch_sources"]) == 1 and len(tr["seen_jobs"]) == 1
man = json.loads(z.read("manifest.json")); assert man["app"] == "internify" and man["schema_version"] == 1 and man["counts"]["applications"] == 2
pv, content = data_export.inspect(blob)
assert pv.applications == 2 and pv.pages == 1 and pv.facts == len(facts) and pv.name == sk["header"]["name"] and "scan_roles" in pv.settings
print("3 ok: the file holds profile, tracker, pages and job-search settings; never keys, logins or chat")

# ---- 4. bad files are refused with a plain reason
def zipped(**members):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zz:
        for n, c in members.items(): zz.writestr(n.replace("__", "/"), c if isinstance(c, str) else json.dumps(c))
    return buf.getvalue()
def refused(blob_, word):
    try: data_export.inspect(blob_); raise SystemExit("should have been refused: " + word)
    except data_export.DataFileError as e: assert word in str(e), (word, str(e))
good = {"manifest.json": {"app": "internify", "schema_version": 1}, "data__facts.json": facts, "data__resume_skeleton.json": sk}
refused(b"not a zip", "isn't a zip")
refused(zipped(x="1"), "no manifest")
refused(zipped(**{**good, "manifest.json": {"app": "other", "schema_version": 1}}), "isn't an Internify")
refused(zipped(**{**good, "manifest.json": {"app": "internify", "schema_version": 2}}), "newer Internify")
refused(zipped(**{**good, "manifest.json": {"app": "internify"}}), "no valid version")
refused(zipped(manifest_only=1, **{"manifest.json": good["manifest.json"]}), "no saved experience")
refused(zipped(**{**good, "data__facts.json": [{"id": "x"}]}), "doesn't look right")
refused(zipped(**{**good, "data__facts.json": "not json {"}), "damaged")
refused(zipped(**{**good, "tracker.json": ["a"]}), "tracker")
refused(zipped(**{**good, "tracker.json": {"applications": "no"}}), "applications")
refused(b"x" * (data_export.MAX_ZIP_BYTES + 1), "20 MB")
big = io.BytesIO()
with zipfile.ZipFile(big, "w", zipfile.ZIP_DEFLATED) as zz: zz.writestr("manifest.json", "0" * (data_export.MAX_UNPACKED_BYTES + 1))
refused(big.getvalue(), "far too big")
pv, content = data_export.inspect(zipped(**good, **{"../../evil.txt": "x", "tracker.json": {"settings": {"owner_chat_id": "1", "scan_roles": "x"}}}))
assert content["settings"] == {"scan_roles": "x"}, "unknown settings in a file are ignored"
assert not os.path.exists(os.path.join(tmp, "evil.txt")), "files inside the zip are read by name, never written out"
print("4 ok: bad, future-version, damaged, oversized and hostile files are refused or ignored with a plain reason")

# ---- 5. importing replaces everything, after a safety copy; settings outside the list stay
db.upsert_application("Old Co", "Old Intern", job_link="https://old/1")
watchlist.set_setting("owner_chat_id", "99")
new_facts = [{"id": "n1", "entry_id": sk["experience_entries"][0]["id"], "section": "experience", "text": "Imported bullet", "tech_tags": ["Go"]}]
new_sk = json.loads(json.dumps(sk)); new_sk["header"]["name"] = "Moved Person"
tracker_new = {"applications": [{"id": 5, "company": "Maybank", "job_title": "Dev Intern", "job_link": "https://m/1", "status": "Applied", "bogus": "x", "facts_used": '["n1"]'}, "junk"],
               "watch_sources": [{"id": 1, "url": "https://jobs.lever.co/zed", "kind": "lever", "ident": "zed", "label": "Zed", "auto": 0}],
               "seen_jobs": [{"url": "https://seen/1", "status": "tailored"}], "settings": {"scan_roles": "ml intern", "scan_places": "Johor"}}
imp = zipped(**{"manifest.json": good["manifest.json"], "data__facts.json": new_facts, "data__resume_skeleton.json": new_sk, "tracker.json": tracker_new,
                "data__outcomes.json": {"wins": {"z": ["n1"]}}})
pv, content = data_export.inspect(imp)
report = data_export.restore(content)
assert os.path.exists(report["safety_copy"]) and report["safety_copy"].endswith(".zip") and "exports" in report["safety_copy"]
old_pv, _ = data_export.inspect(open(report["safety_copy"], "rb").read()); assert old_pv.applications == 3, "the safety copy has what you had (3 applications)"
assert os.path.isdir(report["backup"]), "the profile is also backed up the usual way"
assert _load_skeleton_raw()["header"]["name"] == "Moved Person" and [f["id"] for f in _load_facts_raw()] == ["n1"]
now = {x["company"]: x for x in db.all_applications()}
assert list(now) == ["Maybank"] and now["Maybank"]["status"] == "Applied" and now["Maybank"]["facts_used"] == ["n1"], "tracker replaced, junk rows skipped, unknown columns ignored"
assert [s.label for s in watchlist.list_sources()] == ["Zed"] and watchlist.seen("https://seen/1") and not watchlist.seen("https://x/1")
assert watchlist.get_setting("scan_roles") == "ml intern" and watchlist.get_setting("scan_places") == "Johor"
assert watchlist.get_setting("owner_chat_id") == "99", "your chat stays yours"
assert outcomes.boosts() == {"n1": outcomes.STEP}, "what worked before moves with you"
assert not os.path.exists(config.EMBEDDING_CACHE_PATH), "the search index is rebuilt for the new bullet points"
print("5 ok: import replaces profile, tracker, pages and job-search settings; safety copy and backup made; chat and keys untouched")

# ---- 6. a failure part-way leaves the tracker as it was (all or nothing)
before = db.export_tracker()
class Boom(dict):
    def get(self, k, d=None): raise RuntimeError("disk full")
try: db.replace_tracker(Boom()); raise SystemExit("should fail")
except RuntimeError: pass
assert db.export_tracker() == before, "the tracker is replaced all or nothing"
print("6 ok: a failed import leaves the tracker untouched")

# ---- 7. Telegram: send the zip, see what is in it, confirm (double taps and stale previews do nothing)
class Doc:
    file_size = 1000; file_name = "internify-data-2026-11-16.zip"
    def __init__(self, blob_): self.blob = blob_
    async def get_file(self):
        blob_ = self.blob
        return types.SimpleNamespace(download_as_bytearray=lambda: asyncio.sleep(0, result=bytearray(blob_)))
class Msg:
    def __init__(self, doc=None): self.document, self.edits, self.replies = doc, [], []
    async def reply_text(self, t, **kw): m = Msg(); self.replies.append(m); m.first = t; m.markup = kw.get("reply_markup"); return m
    async def edit_text(self, t, **kw): self.edits.append((t, kw.get("reply_markup")))
async def go(doc_blob):
    m = Msg(Doc(doc_blob)); ud = {}
    upd = types.SimpleNamespace(effective_message=m, effective_user=types.SimpleNamespace(id=1, username="t"), effective_chat=types.SimpleNamespace(id=7))
    await datafile.data_zip_handler(upd, types.SimpleNamespace(user_data=ud))
    return m, ud
m, ud = asyncio.run(go(blob))
status = m.replies[0]
assert "An Internify data file" in status.edits[-1][0] and "replaces" in status.edits[-1][0] and datafile.PENDING_KEY in ud
m2, ud2 = asyncio.run(go(b"junk")); assert "isn't a zip" in m2.replies[0].edits[-1][0] and not ud2
class CQ:
    def __init__(self, data): self.data, self.texts = data, []
    async def answer(self, *a, **k): pass
    async def edit_message_text(self, t, **k): self.texts.append(t)
def tap(data, ud_):
    q = CQ(data); asyncio.run(datafile.data_import_callback(types.SimpleNamespace(callback_query=q, effective_user=types.SimpleNamespace(id=1, username="t")), types.SimpleNamespace(user_data=ud_))); return q
q = tap("dim:cancel", dict(ud)); assert "nothing was changed" in q.texts[-1]
q = tap("dim:go", {}); assert "expired" in q.texts[-1], "a stale button does nothing"
stale = {datafile.PENDING_KEY: {**ud[datafile.PENDING_KEY], "at": 0}}; q = tap("dim:go", stale); assert "expired" in q.texts[-1]
q = tap("dim:go", dict(ud)); assert "Imported!" in q.texts[-1] and "Your earlier data is saved as" in q.texts[-1], q.texts[-1]
assert [x["company"] for x in db.all_applications()] == ["Shopee", "Grab"], "importing the file from step 3 brought those two back"
print("7 ok: Telegram: send the zip, preview, confirm; cancel, expired and repeated taps change nothing")

# ---- 8. the /profile screen and its export button
import bot.handlers.profile_hub as H
text, kb = H.data_screen(); labels = [b_.text for r in kb.inline_keyboard for b_ in r]
assert "My data" in text and "Not in it" in text and labels[0] == "⬇️ Export my data"
docs = []
class QM:
    async def reply_document(self, document, filename, caption): docs.append((len(document), filename))
class HQ: message = QM()
screen = asyncio.run(H._handle(HQ(), types.SimpleNamespace(user_data={}), "dx", []))
assert docs and docs[0][1].endswith(".zip") and "Sent" in screen[0]
assert asyncio.run(H._handle(HQ(), types.SimpleNamespace(user_data={}), "dm", []))[0].startswith("📦")
print("8 ok: /profile → 📦 My data exports the zip and explains importing")
print("\n✅ export test passed")
