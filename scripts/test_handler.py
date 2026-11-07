"""Offline end-to-end check of the Telegram job-link handler. Mocks Telegram, scraping, LLM, Drive and Sheets."""
from __future__ import annotations
import asyncio, os, sys, tempfile, types

sys.path.insert(0, os.path.abspath("."))
tmp = tempfile.mkdtemp()
os.environ.update(OPENAI_API_KEY="sk-test", DATABASE_PATH=os.path.join(tmp, "t.db"), OUTPUT_DIR=tmp)

from service import events
from service.models import ProcessResponse
import bot.handlers.job as job
import bot.jobflow as jobflow
from bot.services import db
from bot.extraction import NormalizedJob
from bot.services.scraper import Page

db.init_db()
pdf = os.path.join(tmp, "r.pdf"); open(pdf, "wb").write(b"%PDF")
calls = {"append": 0, "sheet_update": 0}
sheet_rows: dict[str, int] = {}


async def fake_scrape(url): return Page(text="Job text " * 50, method="direct", label="a normal visit", source_url=url, via_fallback=False)
def fake_norm(text): return NormalizedJob(company="A&B_Corp *Inc*", job_title="Dev <Intern>", location="Remote", job_description="d", job_requirements="r")
seen_ask = []
def fake_process(req, ask=None):
    seen_ask.append(ask)
    events.emit("scribe", events.START, "x", iteration=1)
    return ProcessResponse(resume_status="Excellent", score=88.0, pdf_path=pdf, comment="Add Kubernetes <b>")
def fake_find(url): return sheet_rows.get(url)
def fake_append(**kw): calls["append"] += 1; sheet_rows[kw["job_link"]] = 7; return 7
def fake_sheet_update(*a, **kw): calls["sheet_update"] += 1
jobflow.scrape_job_url, jobflow.normalize_job_posting, jobflow.run_pipeline = fake_scrape, fake_norm, fake_process
jobflow.find_row_by_url, jobflow.append_application, jobflow.update_application_status = fake_find, fake_append, fake_sheet_update
jobflow.upload_to_drive = lambda path, name: "https://drive.google.com/x?a=1&b=2"


class Msg:
    def __init__(self, edits): self.edits, self.deleted = edits, False
    async def edit_text(self, t, **kw): self.edits.append(t)
    async def delete(self): self.deleted = True


class Incoming:
    def __init__(self, text): self.text, self.sent, self.edits, self.docs, self.status = text, [], [], [], None
    async def reply_text(self, t, **kw):
        self.sent.append(t); m = Msg(self.edits)
        self.status = self.status or m
        return m
    async def reply_document(self, document, filename, caption, parse_mode, **kw): self.docs.append((filename, caption, parse_mode))


def run(text):
    inc = Incoming(text)
    update = types.SimpleNamespace(effective_message=inc, effective_chat=types.SimpleNamespace(id=7),
                                   effective_user=types.SimpleNamespace(id=1, username="t", first_name="T"))
    asyncio.run(job.job_url_handler(update, types.SimpleNamespace(bot=object())))
    return inc


# 1. first run of a new link
a = run("apply https://x.com/jobs/1 please")
fn, cap, mode = a.docs[0]
assert mode == "HTML" and "A&amp;B_Corp *Inc*" in cap and "Dev &lt;Intern&gt;" in cap, cap      # special characters can't break formatting
assert "Job match" in cap and "88%" in cap and "ATS" not in cap and "Critic" not in cap, cap     # plain wording
assert "&amp;b=2" in cap or "b=2" in cap and "<a href=" in cap, cap
assert a.status.deleted, "progress message should be removed after delivery"
assert calls["append"] == 1
print("run 1 ok:", cap.replace("\n", " | ")[:140])

from bot import config as bot_config
from bot.ask import SkillQuestioner
assert isinstance(seen_ask[-1], SkillQuestioner), "the pipeline must be given the questioner so the Archivist can ask"
bot_config.ASK_ABOUT_MISSING_SKILLS = False
run("https://x.com/jobs/ask-off")
assert seen_ask[-1] is None, "feature switched off: no questions"
bot_config.ASK_ABOUT_MISSING_SKILLS = True
print("ask wiring ok: questioner passed when on, nothing passed when off")

# 2. same link again: no crash, rows reused, user told why
appends_before, apps_before = calls["append"], len(db.get_recent_applications(limit=50))
b = run("https://x.com/jobs/1")
assert any("already in your tracker" in str(t) for t in b.sent), b.sent
assert b.docs, "should still deliver a fresh resume"
assert calls["append"] == appends_before, "must reuse the existing sheet row, not append another"
assert len(db.get_recent_applications(limit=50)) == apps_before, "must reuse the existing database row"
print("run 2 ok: repeat link reused rows, resume delivered")

# 3. page that can't be read by any route, and the user doesn't paste anything
async def nothing(url): return None
async def no_paste(bot, chat_id, loop, min_chars): return None
jobflow.scrape_job_url, jobflow.ask_for_job_text = nothing, no_paste
c = run("https://x.com/jobs/2")
last = c.edits[-1]
assert "Couldn't read the job page, even after trying other ways in" in last and not c.docs, c.edits
rid = events.bus.latest_run_id()
assert events.bus.get_run(rid)[-1]["data"]["state"] == "failed", "run must be closed as failed"
print("run 3 ok:", last.replace("\n", " ")[:110])

# 3b. ...but the user pastes the job text when Scout asks: the run carries on and is delivered
pasted_calls = []
async def paste(bot, chat_id, loop, min_chars): pasted_calls.append(min_chars); return "Pasted job description. " * 20
jobflow.ask_for_job_text = paste
c = run("https://x.com/jobs/2b")
assert c.docs and pasted_calls == [100], (c.sent, c.edits)
assert not any("couldn't open that page directly" in t for t in c.sent), "pasted text needs no 'I used a copy' warning"
print("run 3b ok: pasted text -> resume delivered")

# 3c. Scout used a saved copy from the web archive: the user is told where the text came from
async def archived(url): return Page(text="Job text " * 50, method="archive", label="a saved copy from the web archive",
                                     source_url="https://web.archive.org/x", via_fallback=True, note="a saved copy from 12 Mar 2026, which may be out of date")
jobflow.scrape_job_url = archived
c = run("https://x.com/jobs/2c")
note = next(t for t in c.sent if "couldn't open that page directly" in t)
assert "a saved copy from 12 Mar 2026, which may be out of date" in note and "check that the company and role look right" in note, note
assert c.docs
print("run 3c ok:", note.replace("\n", " "))

# 3d. fallbacks switched off: no paste request, plain failure
bot_config.SCOUT_FALLBACKS = False
jobflow.scrape_job_url = nothing
jobflow.ask_for_job_text = lambda *a: (_ for _ in ()).throw(AssertionError("must not ask to paste when fallbacks are off"))
c = run("https://x.com/jobs/2d")
assert "Couldn't read the job page" in c.edits[-1] and not c.docs
bot_config.SCOUT_FALLBACKS = True
print("run 3d ok: fallbacks off -> no paste request")

# 4. pipeline error shows readable reason, trackers marked failed
jobflow.scrape_job_url = fake_scrape
from service.pipeline import PipelineError
def boom(req, ask=None): raise PipelineError("Your saved experience is empty. Send me your resume file (/importresume) or add some in /profile first.")
jobflow.run_pipeline = boom
d = run("https://x.com/jobs/3")
last = d.edits[-1]
assert "Your saved experience is empty" in last and "500:" not in last, last
assert db.get_recent_applications(limit=1)[0]["status"] == "Failed"
print("run 4 ok:", last.replace("\n", " "))
print("\n✅ handler test passed")
