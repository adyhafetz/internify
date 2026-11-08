"""Offline test of the daily job check: watchlist, Discoverer, Matcher, scanner orchestration. Websites and AI are faked."""
from __future__ import annotations
import asyncio, os, sys, tempfile, types
import httpx

sys.path.insert(0, os.path.abspath("."))
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "app.db")
from bot import config, scanner
from bot.services import ai as _ai; _ai.enabled = lambda: False   # plain-rules behaviour; the AI helpers are covered in test_ai.py
from bot.jobflow import JobOutcome
from bot.services import db, discovery, matcher, scraper, watchlist
from service import events

db.init_db()

# ---- W: watchlist
assert watchlist.classify("https://job-boards.greenhouse.io/acme-corp/jobs/123") == ("greenhouse", "acme-corp", "Acme Corp")
assert watchlist.classify("https://jobs.lever.co/globex/abc")[0:2] == ("lever", "globex")
assert watchlist.classify("https://careers.initech.com/jobs")[0] == "page"
a = watchlist.add_source("job-boards.greenhouse.io/acme-corp/jobs/123")
assert a.url == "https://boards.greenhouse.io/acme-corp" and a.kind == "greenhouse"
assert watchlist.add_source("https://boards.greenhouse.io/acme-corp").id == a.id, "same board is not added twice"
try: watchlist.add_source("nonsense"); raise SystemExit("should reject")
except watchlist.SourceError: pass
b = watchlist.add_source("https://jobs.lever.co/globex", auto=True)
assert [s.id for s in watchlist.list_sources()] == [a.id, b.id][::-1] or len(watchlist.list_sources()) == 2
assert watchlist.remove_source(b.id) and not watchlist.remove_source(b.id)
assert not watchlist.seen("u1"); watchlist.mark_seen("u1", "T", "C", "low-fit", 0.2, "r"); assert watchlist.seen("u1")
assert not watchlist.scan_paused(); watchlist.set_setting("scan_paused", "1"); assert watchlist.scan_paused(); watchlist.set_setting("scan_paused", "0")
assert watchlist.owner_chat_id() is None; watchlist.set_setting("owner_chat_id", "42"); assert watchlist.owner_chat_id() == 42
assert watchlist.board_root("https://jobs.ashbyhq.com/acme-co/1234-abcd") == ("https://jobs.ashbyhq.com/acme-co", "Acme Co")
assert watchlist.board_root("https://apply.workable.com/globex/j/ABC123/") == ("https://apply.workable.com/globex", "Globex")
assert watchlist.board_root("https://intel.wd1.myworkdayjobs.com/en-US/External/job/Penang/Intern_JR1") == ("https://intel.wd1.myworkdayjobs.com/External", "Intel")
assert watchlist.board_root("https://careers.initech.com/jobs/1") is None
for link in ("https://jobs.ashbyhq.com/acme-co/1234-abcd", "https://careers.initech.com/jobs/1", "https://jobs.lever.co/globex/abc"):
    db.upsert_application("Co", "Role", job_link=link)
added = watchlist.sources_from_history()
urls = {s_.url: s_ for s_ in watchlist.list_sources()}
assert "https://jobs.ashbyhq.com/acme-co" in urls and urls["https://jobs.ashbyhq.com/acme-co"].label == "Acme Co" and urls["https://jobs.ashbyhq.com/acme-co"].auto
assert "https://jobs.lever.co/globex" in urls and not any("initech" in u for u in urls), "other hosts are not guessed"
for s_ in list(urls.values()):
    watchlist.remove_source(s_.id)
with db._get_connection() as _c: _c.execute("DELETE FROM applications"); _c.commit()
print("W ok: classify, add/dedupe/remove, seen, settings, boards found from your history")

# ---- D: discovery with fake websites
GH = {"jobs": [{"title": "Backend Intern", "absolute_url": "https://boards.greenhouse.io/acme-corp/jobs/1", "location": {"name": "KL"}, "content": "<p>Python &amp; Postgres</p>"},
               {"title": "", "absolute_url": "x"}]}
LV = [{"text": "Data Intern", "hostedUrl": "https://jobs.lever.co/globex/9", "categories": {"location": "Remote"}, "descriptionPlain": "SQL"}]
PAGE = '<a href="/jobs/ml-engineer-intern-77">ML Engineer Intern</a> <a href="/about">About us today</a> [Platform Developer](https://careers.initech.com/careers/platform-5)'

def handler(req: httpx.Request) -> httpx.Response:
    u = str(req.url)
    if "boards-api.greenhouse.io/v1/boards/acme-corp" in u: return httpx.Response(200, json=GH)
    if "api.lever.co/v0/postings/globex" in u: return httpx.Response(200, json=LV)
    if "careers.initech.com" in u: return httpx.Response(200, text=PAGE)
    return httpx.Response(404)

scraper._client = lambda t: httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=t, follow_redirects=True, headers=scraper.HEADERS)
config.SCRAPINGANT_API_KEY, config.SCOUT_FALLBACKS = "", False
links = discovery.extract_job_links(PAGE, "https://careers.initech.com/jobs")
assert [t for t, _ in links] == ["ML Engineer Intern", "Platform Developer"], links
srcs = [watchlist.add_source("https://boards.greenhouse.io/acme-corp"), watchlist.add_source("https://jobs.lever.co/globex"),
        watchlist.add_source("https://careers.initech.com/jobs"), watchlist.add_source("https://jobs.lever.co/ghost")]
cands, results = asyncio.run(discovery.discover(srcs))
assert {c.title for c in cands} == {"Backend Intern", "Data Intern", "ML Engineer Intern", "Platform Developer"}
assert [r.ok for r in results] == [True, True, True, False], "a failing source does not stop the others"
assert "Python & Postgres" in next(c for c in cands if c.title == "Backend Intern").snippet
print("D ok: Greenhouse, Lever, page links, failing source isolated")

# ---- M: matcher with fake AI
C = lambda t, u, snip="": discovery.Candidate(t, "Co", u, "", snip, "s")
cs = [C("Backend Intern", "u/a", "x" * 500), C("Chef", "u/b", "x" * 500), C("Data Intern", "u/c", "x" * 500)]
matcher.taste_texts = lambda: ["backend python"]
matcher._embed = lambda texts: (_ for _ in ()).throw(RuntimeError("no embeddings"))   # exercises the word-overlap fallback
fits = {"Backend Intern": (["Python", "SQL"], []), "Data Intern": (["SQL"], ["Spark", "Kafka", "Hive"]), "Chef": ([], ["Knife"])}
matcher._analyse = lambda text: (text.split("\n")[0], "Co", *fits[text.split("\n")[0]])
res = asyncio.run(matcher.match(cs, max_keep=2, min_fit=0.5))
assert [k.role for k in res.kept] == ["Backend Intern"], [(s.role, s.fit) for s in res.scored]
assert res.top == res.kept[0].fit and "Python" in res.kept[0].why and res.total == 3
res2 = asyncio.run(matcher.match(cs, max_keep=2, min_fit=0.99)); assert res2.kept == [] and res2.top is not None
print("M ok: keeps the fit, drops the rest, falls back without embeddings")

# ---- S: scanner orchestration
class Bot:
    def __init__(self): self.sent = []
    async def send_message(self, chat, text, **kw): self.sent.append(text)
    async def send_document(self, chat, **kw): self.sent.append("DOC " + kw.get("caption", ""))

tailored = []
async def fake_tailor(url, reporter, *, bot, chat_id, loop, interactive, intro=""):
    assert interactive is False
    tailored.append(url)
    out = JobOutcome(ok="bad" not in url, company="Co", role="R", score=88.0, status="Ready", pdf_path="/x/y.pdf", error="boom")
    await (reporter.finished(out) if out.ok else reporter.fail("❌ boom"))
    return out
scanner.tailor_job = fake_tailor
scanner.profile_is_sample = lambda: False

LOG = []
_emit, _emit_run = events.emit, events.emit_run
events.emit = lambda agent, kind, msg="", to=None, **d: (LOG.append((agent, kind, to)), _emit(agent, kind, msg, to, **d))[1]
events.emit_run = lambda kind, **d: (LOG.append(("run", kind, None)), _emit_run(kind, **d))[1]

def scan():
    LOG.clear(); b = Bot(); s = asyncio.run(scanner.run_scan(b, 1, "manual"))
    return b, s, list(LOG)

# no sources
for s_ in watchlist.list_sources(): watchlist.remove_source(s_.id)
b, s, _ = scan(); assert "nowhere to look" in b.sent[-1] and s.note
# all sources fail
watchlist.add_source("https://jobs.lever.co/ghost")
b, s, _ = scan(); assert "None of your job pages answered" in b.sent[-1]
# normal: one board, two jobs, one fits
GH["jobs"] = [{"title": "Backend Intern", "absolute_url": "https://boards.greenhouse.io/acme-corp/jobs/1", "content": "x" * 500},
              {"title": "bad Chef", "absolute_url": "https://boards.greenhouse.io/acme-corp/jobs/bad", "content": "x" * 500}]
fits["bad Chef"] = ([], ["Knife"])
watchlist.add_source("https://boards.greenhouse.io/acme-corp")
b, s, log = scan()
order = [(a, k) for a, k, _ in log]
assert [x for x in order if x[0] != "discoverer" or x[1] != "progress"] == [
    ("run", "started"), ("discoverer", "start"), ("discoverer", "done"), ("discoverer", "handoff"), ("matcher", "start"), ("matcher", "progress"),
    ("matcher", "done"), ("matcher", "handoff"), ("run", "job_started"), ("run", "job_finished"), ("run", "finished")], order
assert tailored == ["https://boards.greenhouse.io/acme-corp/jobs/1"], tailored
assert s.found == 2 and s.kept == 1 and s.tailored == 1 and "1 resume" in b.sent[-1]
assert watchlist.seen("https://boards.greenhouse.io/acme-corp/jobs/bad"), "low-fit jobs are remembered"
b, s, _ = scan(); assert "nothing new" in b.sent[-1], "second scan finds nothing new"
print("S ok: no sources / all failed / shortlisted+tailored / nothing new")

# one at a time
async def twice():
    scanner._running = True
    r = await scanner.run_scan(Bot(), 1); scanner._running = False; return r
assert asyncio.run(twice()) is None
print("S ok: one scan at a time")

# a fresh install still holds the made-up sample profile: the daily check refuses to write resumes from it
scanner.profile_is_sample = lambda: True
b, s, _ = scan(); assert "sample profile" in b.sent[-1] and not tailored[len(tailored):], b.sent[-1]
scanner.profile_is_sample = lambda: False

# schedule
class JQ:
    def __init__(self): self.calls = []
    def run_daily(self, cb, time, name): self.calls.append((cb, time, name))
app = types.SimpleNamespace(job_queue=JQ()); config.SCAN_ENABLED, config.SCAN_TIME, config.SCAN_TIMEZONE = True, "07:30", "Asia/Kuala_Lumpur"
assert "07:30" in scanner.schedule(app) and app.job_queue.calls[0][1].hour == 7 and app.job_queue.calls[0][2] == "daily-scan"
config.SCAN_TIME = "nope"; assert scanner.schedule(types.SimpleNamespace(job_queue=JQ())) is None
config.SCAN_ENABLED = False; assert scanner.schedule(app) is None
print("T ok: schedule")
print("ALL SCAN TESTS PASSED")
