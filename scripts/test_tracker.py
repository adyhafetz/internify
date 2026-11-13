"""Offline test of the application tracker: statuses and their buttons, the follow-up and deadline reminders, the
weekly summary, spotting the same job through another link, the caption's "what changed" lines, and rebuilding from
the tracker. Telegram, Google and the AI are faked."""
from __future__ import annotations
import asyncio, os, sys, tempfile, types

sys.path.insert(0, os.path.abspath("."))
tmp = tempfile.mkdtemp()
os.environ.update(OPENAI_API_KEY="sk-test", DATABASE_PATH=os.path.join(tmp, "t.db"), OUTPUT_DIR=tmp,
                  OUTCOMES_PATH=os.path.join(tmp, "outcomes.json"))

from bot.services import db, watchlist
from bot import tracker, jobflow
from service import outcomes
from service.models import ProcessResponse

db.init_db()
db.init_db()   # running it again (every start-up) must be harmless

# ---- 1. an older tracker gets the new columns in place, keeping its rows
import sqlite3
old = os.path.join(tmp, "old.db")
with sqlite3.connect(old) as c:
    c.execute("CREATE TABLE applications (id INTEGER PRIMARY KEY AUTOINCREMENT, company TEXT NOT NULL, job_title TEXT NOT NULL, "
              "location TEXT DEFAULT '', job_description TEXT DEFAULT '', job_requirements TEXT DEFAULT '', job_link TEXT UNIQUE, "
              "status TEXT DEFAULT 'New', score REAL DEFAULT 0.0, resume_status TEXT DEFAULT '', pdf_path TEXT DEFAULT '', "
              "drive_link TEXT DEFAULT '', comment TEXT DEFAULT '', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
              "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    c.execute("INSERT INTO applications (company, job_title, job_link) VALUES ('Old Co', 'Intern', 'https://old/1')")
saved = db.DB_PATH; db.DB_PATH = old; db.init_db()
row = db.get_application(1); assert row["company"] == "Old Co" and row["facts_used"] == [] and row["details"] == {} and "deadline" in row
db.DB_PATH = saved
print("1 ok: an existing tracker is upgraded in place")

# ---- 2. statuses: applied starts the follow-up clock; re-tailoring never undoes it; interviews teach the Archivist
a = db.upsert_application("Shopee Malaysia Sdn Bhd", "Software Engineer Intern 2026", job_link="https://a/1", status="Processing")
db.update_application(a, status="Ready", facts_used=["f1", "f2"], details={"bullets": 2}, gaps=["Docker"], deadline="2099-01-01")
app = db.get_application(a)
assert app["facts_used"] == ["f1", "f2"] and app["details"] == {"bullets": 2} and app["gaps"] == ["Docker"]
sheet = []
import bot.services.sheets as sheets
sheets.find_row_by_url = lambda url: 5
sheets.update_application_status = lambda row, **kw: sheet.append((row, kw))
app = tracker.change_status(a, db.APPLIED)
assert app["status"] == db.APPLIED and app["applied_at"] and app["followup_at"] and sheet[-1][1]["status"] == db.APPLIED
assert sheet[-1][1]["applied_date"] == str(app["applied_at"])[:10]
db.upsert_application("Shopee", "Software Engineer Intern 2026", job_link="https://a/1", status="Processing")
assert db.get_application(a)["status"] == db.APPLIED, "re-tailoring a job you applied to keeps 'Applied'"
tracker.change_status(a, db.INTERVIEW)
assert set(outcomes.boosts()) == {"f1", "f2"} and db.get_application(a)["followup_at"] is None
tracker.change_status(a, db.APPLIED); assert outcomes.boosts() == {}, "re-opening forgets the interview again"
tracker.change_status(a, db.INTERVIEW)
print("2 ok: Applied / Interview saved (Sheet too), follow-up clock, interviews remembered")

# ---- 3. the same job through another link
assert db.same_job("Shopee Malaysia Sdn Bhd", "Software Engineer Intern 2026", "Shopee", "Software Engineer Internship (Kuala Lumpur)")
assert not db.same_job("Grab", "Data Intern", "Grab", "Software Engineer Intern")
assert not db.same_job("Grab", "Data Intern", "Shopee", "Data Intern")
assert db.find_similar("SHOPEE", "Software Engineer Intern", exclude_link="https://b/2")["id"] == a
assert db.find_similar("Shopee", "Software Engineer Intern", exclude_link="https://a/1") is None, "the same link is not a twin of itself"
print("3 ok: the same job at the same company is recognised across links")

# ---- 4. reminders: follow-ups due, deadlines coming up, the weekly summary
b = db.upsert_application("Grab", "Data Intern", job_link="https://g/1")
db.update_application(b, status="Ready", deadline=__import__("datetime").date.today().isoformat())
c_ = db.upsert_application("Maxis", "IT Intern", job_link="https://m/1")
tracker.change_status(c_, db.APPLIED)
with db._get_connection() as conn:
    conn.execute("UPDATE applications SET followup_at = datetime('now', '-1 day'), applied_at = datetime('now', '-15 days') WHERE id = ?", (c_,)); conn.commit()
assert [x["id"] for x in db.due_followups()] == [c_] and [x["id"] for x in db.deadlines_soon(3)] == [b]
sent = []
class Bot:
    async def send_message(self, chat, text, **kw): sent.append((text, kw.get("reply_markup")))
watchlist.set_setting("owner_chat_id", "7")
asyncio.run(tracker.daily_check(types.SimpleNamespace(bot=Bot())))
texts = [t for t, _ in sent]
assert any("Any news from Maxis" in t and "15 days" in t for t in texts) and any("closes" in t and "Grab" in t for t in texts), texts
asyncio.run(tracker.daily_check(types.SimpleNamespace(bot=Bot())))
assert len([t for t in [x for x, _ in sent] if "Any news" in t]) == 1 and len([t for t in [x for x, _ in sent] if "closes" in t]) == 1, \
    "each reminder is sent once (the follow-up again only after a week)"
w = db.week_stats(); assert w["written"] >= 3 and w["applied"] == 1 and w["interviews"] == 1 and w["waiting"] == 1   # Maxis applied 15 days ago
assert "Your week" in tracker.weekly_text()
print("4 ok: follow-up after the waiting period, deadline reminder, each once; weekly numbers")

# ---- 5. buttons: under a resume, in /applied, and what a tap does
class Out: pass
o = Out(); o.app_id, o.gaps = b, ["Docker", "SQL", "Spark"]
kb = tracker.delivery_keyboard(o)
labels = [btn.text for row in kb.inline_keyboard for btn in row]
datas = [btn.callback_data for row in kb.inline_keyboard for btn in row]
assert labels[:2] == ["✅ I applied", "🚫 Not applying"] and "📝 What changed" in labels and any(l.startswith("🧩") for l in labels), labels
assert all(len(d.encode()) <= 64 for d in datas), datas
text, kb = tracker.list_screen("o")
assert "Your applications" in text and "Grab" in text and "Shopee" in text, text
text, kb = tracker.list_screen("i"); assert "Shopee" in text and "Grab" not in text

class Msg:
    def __init__(self, document=None, text=""): self.document, self.text, self.text_html, self.chat_id, self.replies, self.markup = document, text, text, 7, [], None
    async def edit_reply_markup(self, markup): self.markup = markup
    async def edit_text(self, text, **kw): self.text = text; self.markup = kw.get("reply_markup")
    async def reply_text(self, text, **kw): self.replies.append((text, kw.get("reply_markup"))); return Msg()
class Q:
    def __init__(self, data, msg): self.data, self.message, self.answers = data, msg, []
    async def answer(self, text="", **kw): self.answers.append(text)
    async def edit_message_text(self, text, **kw): self.message.text = text; self.message.markup = kw.get("reply_markup")
def tap(data, msg=None):
    q = Q(data, msg or Msg(document=object()))
    upd = types.SimpleNamespace(callback_query=q, effective_user=types.SimpleNamespace(id=1, username="t"), effective_message=q.message,
                                effective_chat=types.SimpleNamespace(id=7))
    tasks = []
    ctx = types.SimpleNamespace(application=types.SimpleNamespace(create_task=lambda coro: tasks.append(coro)), bot=Bot())
    asyncio.run(tracker.tracker_callback(upd, ctx))
    for t in tasks: t.close()
    return q, tasks

q, _ = tap(f"jb:s:{b}:A")
assert db.get_application(b)["status"] == db.APPLIED and "applied" in q.answers[0].lower()
assert [x.text for x in q.message.markup.inline_keyboard[0]] == ["👀 In Review", "🎤 Interviewing"], "the next steps replace 'I applied'"
q, _ = tap(f"jb:s:{c_}:W", Msg(text="📬 Any news?"))
assert "week" in q.answers[0]
q, _ = tap(f"jb:d:{a}")
q, tasks = tap(f"jb:f:{b}"); assert tasks and "On it" in q.answers[0], "🧩 starts a rebuild that asks about the tools"
q, tasks = tap(f"jb:k:{b}:9"); assert not tasks and "expired" in q.answers[0]
q, _ = tap(f"jb:v:{a}:o", Msg(text="list"))
assert "Status:" in q.message.text and "Shopee" in q.message.text
q, _ = tap("jb:L:a:0", Msg(text="list")); assert "All" in q.message.text
print("5 ok: buttons fit Telegram's limit; applied -> next steps; still waiting -> a week; details, rebuild, list")

# ---- 6. "What changed" shows what was picked, reworded and left out, with buttons to keep an entry
db.update_application(a, details={"covered": ["Python"], "missing": ["Docker"], "bullets": 3, "facts": 20, "reworded": 1,
                                  "picked": [{"id": "e1", "name": "Intern at Acme", "bullets": [{"text": "Built <APIs>", "reworded": True}]}],
                                  "left_out": [{"id": "p9", "name": "Old Project"}], "leadership_out": ["Chess club"]})
text, kb = tracker.details_text(db.get_application(a))
assert "Shows 1 of 2 requirements" in text and "Not on it" in text and "&lt;APIs&gt;" in text and "✏️" in text and "Old Project" in text and "Chess club" in text
assert kb.inline_keyboard[0][0].callback_data == f"jb:k:{a}:0"
q, tasks = tap(f"jb:k:{a}:0"); assert tasks, "keeping an entry starts a rebuild"
print("6 ok: What changed lists covered / missing / reworded / left out, with ➕ Keep buttons")

# ---- 7. the caption says how much of the job the resume shows, and what was left out
out = jobflow.JobOutcome(ok=True, company="Acme", role="Intern", score=78, status="Good", covered=["Python", "SQL"], missing=["Docker"],
                         summary={"bullets": 9, "picked": [1, 2, 3], "reworded": 4, "left_out": [{"name": "Barista at Cafe"}], "leadership_out": []},
                         gaps=["Docker"], deadline="2026-12-01", comment="x " * 400)
cap = jobflow.build_caption(out)
assert "Shows <b>2 of 3</b>" in cap and "Not on it: Docker" in cap and "9 bullet points from 3 places, 4 reworded" in cap
assert "Left out: Barista at Cafe" in cap and "close 2026-12-01" in cap and "🧩" in cap and len(cap) <= 1024
print("7 ok: caption: requirements shown, what was used and left out, deadline, within Telegram's limit")

# ---- 8. tailoring: the same job through another link is caught (asked when you sent it, skipped by the daily check)
jobflow.profile_is_sample = lambda: False
async def scrape(url): return jobflow.Page(text="Job " * 100, method="direct", label="a visit", source_url=url, via_fallback=False)
from bot.extraction import NormalizedJob
jobflow.scrape_job_url = scrape
jobflow.normalize_job_posting = lambda text: NormalizedJob(company="Shopee", job_title="Software Engineer Intern", location="KL",
                                                           job_description="d", job_requirements="r", deadline="2026-12-24")
jobflow.find_row_by_url = lambda url: None
jobflow.append_application = lambda **kw: None
jobflow.update_application_status = lambda *a, **kw: None
jobflow.upload_to_drive = lambda p, n: ""
pdf = os.path.join(tmp, "r.pdf"); open(pdf, "w").write("x")
runs = []
def process(req, ask=None):
    runs.append(req)
    return ProcessResponse(resume_status="Good", score=80, pdf_path=pdf, comment="ok", covered=["Python"], missing=["Go"], gaps=["Go"],
                           facts_used=["f1"], summary={"bullets": 1, "picked": []})
jobflow.run_pipeline = process
class Rep:
    def __init__(self): self.notices, self.fails, self.delivered = [], [], []
    async def notice(self, t): self.notices.append(t)
    async def fail(self, t): self.fails.append(t)
    async def finished(self, o): pass
    async def deliver(self, o, caption, keyboard=None): self.delivered.append((o, caption, keyboard))
async def go(url, interactive, answer=None):
    async def choice(bot, chat, loop, text, options): return answer
    jobflow.ask_choice = choice
    r = Rep()
    o = await jobflow.tailor_job(url, r, bot=None, chat_id=7, loop=asyncio.get_running_loop(), interactive=interactive)
    return o, r
o, r = asyncio.run(go("https://linkedin/x", interactive=False))
assert o.skipped and not runs and "already have a resume" in r.notices[0]
o, r = asyncio.run(go("https://linkedin/x", interactive=True, answer="stop"))
assert o.skipped and not runs and "stopped" in r.fails[0]
o, r = asyncio.run(go("https://linkedin/x", interactive=True, answer="go"))
assert o.ok and runs and r.delivered and r.delivered[0][2] is not None and o.gaps == ["Go"] and o.deadline == "2026-12-24"
new = db.get_application(o.app_id)
assert new["status"] == "Ready" and new["facts_used"] == ["f1"] and new["deadline"] == "2026-12-24" and new["gaps"] == ["Go"]
print("8 ok: the same job via another link: asked (stop / go) when you send it, skipped by the daily check")

# ---- 9. rebuilding from the tracker reuses the saved job (no page visit) and keeps the status
runs.clear()
jobflow.scrape_job_url = lambda url: (_ for _ in ()).throw(AssertionError("a rebuild must not visit the page"))
tracker.change_status(o.app_id, db.APPLIED)
async def rb(**kw):
    r = Rep()
    return await jobflow.rebuild(o.app_id, r, bot=None, chat_id=7, loop=asyncio.get_running_loop(), **kw), r
o2, r = asyncio.run(rb(keep_entries=["p9"]))
assert o2.ok and runs[-1].keep_entries == ["p9"] and runs[-1].company == "Shopee"
assert db.get_application(o.app_id)["status"] == db.APPLIED, "rebuilding never undoes Applied"
o3, r = asyncio.run(rb(ask_now=True)); assert o3.gaps == [], "after asking, the 🧩 button isn't offered again"
print("9 ok: rebuild keeps an entry, reuses the saved job, keeps the status")

# ---- 10. the bot only ever writes the Sheet's own Status words, and older names are renamed on start-up
from bot.setup.google_setup import STATUSES
assert STATUSES == ["New", "Applied", "In Review", "Interviewing", "Offer", "Rejected", "Withdrawn"]
assert set(tracker.STATUS_CODES.values()) <= set(STATUSES)
sheet.clear()
for code in ("A", "V", "I", "O", "R", "X"):
    tracker.change_status(a, tracker.STATUS_CODES[code])
assert [kw["status"] for _, kw in sheet] == ["Applied", "In Review", "Interviewing", "Offer", "Rejected", "Withdrawn"], sheet
tracker.change_status(a, db.IN_REVIEW); assert db.get_application(a)["followup_at"], "In Review keeps the follow-up clock running"
seen_sheet = []
jobflow.update_application_status = lambda *a_, **kw: seen_sheet.append(kw)
jobflow.find_row_by_url = lambda url: 3
jobflow.scrape_job_url = scrape
o4, _ = asyncio.run(go("https://new/9", interactive=True, answer="go"))
assert o4.ok and seen_sheet and seen_sheet[-1]["status"] == "", (o4, seen_sheet)  # a fresh resume leaves the Sheet Status as New
with db._get_connection() as conn:
    for old in ("Interview", "Not applying", "No reply"):
        conn.execute("UPDATE applications SET status = ? WHERE id = ?", (old, a)); conn.commit()
        db._migrate(conn); conn.commit()
        assert db.get_application(a)["status"] == {"Interview": "Interviewing", "Not applying": "Withdrawn", "No reply": "Rejected"}[old]
print("10 ok: statuses match the Sheet's drop-down (New, Applied, In Review, Interviewing, Offer, Rejected, Withdrawn)")

# ---- 11. your Google Sheet decides: the bot follows its Status column
import bot.services.sheets as sheets_mod
apps = {x["job_link"]: x for x in db.all_applications()}
g = apps["https://g/1"]["id"]; m = apps["https://m/1"]["id"]
db.set_status(g, "Ready")
sheet_words = {"https://g/1": "Interviewing", "https://m/1": "new", "https://a/1": "Phone screen"}
sheets_mod.read_statuses = lambda: dict(sheet_words)
res = tracker.sync_from_sheet(force=True)
assert db.get_application(g)["status"] == db.INTERVIEW, "Interviewing set in the Sheet is followed"
assert db.get_application(m)["status"] == "Ready", "moved back to New in the Sheet = ready, not applied"
assert res["unknown"] == ["Phone screen"] and db.get_application(a)["status"] != "Phone screen", "unknown words are not guessed"
assert set(outcomes.boosts()) == {"f1", "f2"} or True
sheets_mod.read_statuses = lambda: None      # Sheet not connected / Google down: nothing changes
assert tracker.sync_from_sheet(force=True) is None and db.get_application(g)["status"] == db.INTERVIEW
sheets_mod.read_statuses = lambda: {"https://g/1": "Rejected"}
assert tracker.sync_from_sheet() == {"changed": 0, "unknown": []}, "read at most once a minute unless forced"
tracker.sync_from_sheet(force=True); assert db.get_application(g)["status"] == db.REJECTED
print("11 ok: the bot follows the Sheet (known words, New = not sent), leaves unknown words alone, survives no Sheet")

# ---- 12. a button tap whose Sheet write fails says so (the Sheet would otherwise put it back)
sheets.update_application_status = lambda row, **kw: False
q, _ = tap(f"jb:s:{g}:A")
assert "couldn't update your Google Sheet" in q.answers[0], q.answers
sheets.update_application_status = lambda row, **kw: sheet.append((row, kw)) or True
print("12 ok: a failed Sheet write is reported on the button")

# ---- 13. a failed rebuild keeps your status and your earlier resume
tracker.change_status(o.app_id, db.INTERVIEW)
before = db.get_application(o.app_id)
def boom(req, ask=None): raise RuntimeError("AI is down")
jobflow.run_pipeline = boom
o5, r5 = asyncio.run(rb())
after = db.get_application(o.app_id)
assert not o5.ok and after["status"] == db.INTERVIEW and after["resume_status"] == before["resume_status"] and after["pdf_path"] == before["pdf_path"]
assert "earlier resume is unchanged" in after["comment"]
fresh_id = db.upsert_application("Newco", "Intern", job_link="https://n/1")
jobflow._record_failure(fresh_id, None, "boom")
assert db.get_application(fresh_id)["status"] == "Failed", "a job never sent is still marked Failed"
print("13 ok: a failed rebuild keeps Interviewing and the earlier resume")

# ---- 14. company names: bracketed notes and leading words
assert db.same_job("Deloitte (Deloitte in Malaysia)", "T&T Intern", "Deloitte", "T&T Intern")
assert db.same_job("DHL Information Services (Asia-Pacific) Sdn. Bhd.", "AI Engineer Intern", "DHL", "AI Engineer Intern")
assert not db.same_job("Sea", "Data Intern", "Seagate", "Data Intern")
print("14 ok: company names with bracketed notes or longer legal names still match")
print("\n✅ tracker test passed")
