"""Offline end-to-end check of the Telegram job-link handler. Mocks Telegram, scraping, LLM, Drive and Sheets."""
from __future__ import annotations
import asyncio, os, sys, tempfile, types

sys.path.insert(0, os.path.abspath("."))
tmp = tempfile.mkdtemp()
os.environ.update(OPENAI_API_KEY="sk-test", DATABASE_PATH=os.path.join(tmp, "t.db"), OUTPUT_DIR=tmp)

from service import events
from service.models import ProcessResponse
import bot.handlers.job as job
from bot.services import db
from bot.extraction import NormalizedJob

db.init_db()
pdf = os.path.join(tmp, "r.pdf"); open(pdf, "wb").write(b"%PDF")
calls = {"append": 0, "sheet_update": 0}
sheet_rows: dict[str, int] = {}


async def fake_scrape(url): return "Job text " * 50
def fake_norm(text): return NormalizedJob(company="A&B_Corp *Inc*", job_title="Dev <Intern>", location="Remote", job_description="d", job_requirements="r")
def fake_process(req):
    events.emit("scribe", events.START, "x", iteration=1)
    return ProcessResponse(resume_status="Excellent", score=88.0, pdf_path=pdf, comment="Add Kubernetes <b>")
def fake_find(url): return sheet_rows.get(url)
def fake_append(**kw): calls["append"] += 1; sheet_rows[kw["job_link"]] = 7; return 7
def fake_sheet_update(*a, **kw): calls["sheet_update"] += 1
job.scrape_job_url, job.normalize_job_posting, job.process = fake_scrape, fake_norm, fake_process
job.find_row_by_url, job.append_application, job.update_application_status = fake_find, fake_append, fake_sheet_update
job.upload_to_drive = lambda path, name: "https://drive.google.com/x?a=1&b=2"


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
    update = types.SimpleNamespace(effective_message=inc, effective_user=types.SimpleNamespace(id=1, username="t", first_name="T"))
    asyncio.run(job.job_url_handler(update, None))
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

# 2. same link again: no crash, rows reused, user told why
b = run("https://x.com/jobs/1")
assert any("already in your tracker" in str(t) for t in b.sent), b.sent
assert b.docs, "should still deliver a fresh resume"
assert calls["append"] == 1, "must reuse the existing sheet row, not append another"
assert len(db.get_recent_applications(limit=10)) == 1, "must reuse the existing database row"
print("run 2 ok: repeat link reused rows, resume delivered")

# 3. page that can't be read
async def blocked(url): return "tiny"
job.scrape_job_url = blocked
c = run("https://x.com/jobs/2")
last = c.edits[-1]
assert "Couldn't read the job page" in last and not c.docs, c.edits
rid = events.bus.latest_run_id()
assert events.bus.get_run(rid)[-1]["data"]["state"] == "failed", "run must be closed as failed"
print("run 3 ok:", last.replace("\n", " "))

# 4. pipeline error shows readable reason, trackers marked failed
job.scrape_job_url = fake_scrape
from fastapi import HTTPException
def boom(req): raise HTTPException(status_code=500, detail="Your saved experience is empty. Add some with /addexperience or /addproject first.")
job.process = boom
d = run("https://x.com/jobs/3")
last = d.edits[-1]
assert "Your saved experience is empty" in last and "500:" not in last, last
assert db.get_recent_applications(limit=1)[0]["status"] == "Failed"
print("run 4 ok:", last.replace("\n", " "))
print("\n✅ handler test passed")
