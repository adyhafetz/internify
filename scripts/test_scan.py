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
watchlist.set_setting("scan_web_search", "0")    # the web search and the filters have their own tests below
watchlist.set_setting("scan_interns_only", "0")

# ---- W: watchlist
assert watchlist.classify("https://job-boards.greenhouse.io/acme-corp/jobs/123") == ("greenhouse", "acme-corp", "Acme Corp")
assert watchlist.classify("https://jobs.lever.co/globex/abc")[0:2] == ("lever", "globex")
assert watchlist.classify("https://careers.initech.com/jobs")[0] == "page"
a = watchlist.add_source("job-boards.greenhouse.io/acme-corp/jobs/123")
assert a.url == "https://boards.greenhouse.io/acme-corp" and a.kind == "greenhouse"
assert watchlist.add_source("https://boards.greenhouse.io/acme-corp").id == a.id, "same board is not added twice"
try: watchlist.add_source("nonsense"); raise SystemExit("should reject")
except watchlist.SourceError: pass
b = watchlist.add_source("https://jobs.lever.co/globex")
assert [s.id for s in watchlist.list_sources()] == [a.id, b.id][::-1] or len(watchlist.list_sources()) == 2
assert watchlist.remove_source(b.id) and not watchlist.remove_source(b.id)
assert not watchlist.seen("u1"); watchlist.mark_seen("u1", "T", "C", "low-fit", 0.2, "r"); assert watchlist.seen("u1")
assert not watchlist.scan_paused(); watchlist.set_setting("scan_paused", "1"); assert watchlist.scan_paused(); watchlist.set_setting("scan_paused", "0")
assert watchlist.owner_chat_id() is None; watchlist.set_setting("owner_chat_id", "42"); assert watchlist.owner_chat_id() == 42
assert watchlist.classify("https://jobs.ashbyhq.com/acme-co/1234-abcd") == ("ashby", "acme-co", "Acme Co")
assert watchlist.classify("https://apply.workable.com/globex/j/ABC123/")[:2] == ("workable", "globex")
assert watchlist.classify("https://jobs.smartrecruiters.com/Visa/7438")[:2] == ("smartrecruiters", "Visa")
assert watchlist.classify("https://boards.greenhouse.io/embed/job_board?for=acme")[:2] == ("greenhouse", "acme")
assert watchlist.board_root("https://jobs.ashbyhq.com/acme-co/1234-abcd") is None, "Ashby is read through its API now"
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
def _job(role, **kw):
    base = dict(role=role, company="Co", location="", deadline=None, is_internship=None)
    return types.SimpleNamespace(**{**base, **kw})
matcher._analyse = lambda text: (_job(text.split("\n")[0]), *fits[text.split("\n")[0]])
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

# ---- B: more hiring systems, a career page that is really a board, the web search, your filters
config.SCAN_ENABLED = True
ASHBY = {"jobs": [{"title": "Software Engineer Intern", "jobUrl": "https://jobs.ashbyhq.com/ramp/1", "location": "Kuala Lumpur",
                   "descriptionPlain": "Python", "employmentType": "Intern"},
                  {"title": "Senior Engineer", "jobUrl": "https://jobs.ashbyhq.com/ramp/2", "location": "Kuala Lumpur"},
                  {"title": "Data Intern", "jobUrl": "https://jobs.ashbyhq.com/ramp/3", "location": "Singapore"},
                  {"title": "Hidden Intern", "jobUrl": "https://jobs.ashbyhq.com/ramp/4", "isListed": False}]}
SR = {"content": [{"id": "77", "name": "IT Intern", "location": {"city": "Penang", "country": "my"}, "typeOfEmployment": {"label": "Internship"}}]}
WK = {"name": "Globex", "jobs": [{"title": "Marketing Intern", "shortcode": "AB1", "url": "https://apply.workable.com/j/AB1", "city": "Remote", "telecommuting": True}]}
EMBED = '<html><script src="https://boards.greenhouse.io/embed/job_board/js?for=initrode"></script></html>'
GH2 = {"jobs": [{"title": "QA Intern", "absolute_url": "https://boards.greenhouse.io/initrode/jobs/5", "location": {"name": "Remote"}, "content": "tests"}]}
RSS = """<rss><channel>
<item><title>Backend Intern - Hooli</title><link>https://jobs.lever.co/hooli/abc-1</link></item>
<item><title>Hooli careers</title><link>https://jobs.lever.co/hooli</link></item>
<item><title>Job Application for Platform Intern at Pied Piper</title><link>https://boards.greenhouse.io/piedpiper/jobs/42</link></item>
<item><title>Some blog</title><link>https://example.com/blog/interns</link></item>
</channel></rss>"""
searched = []
def handler2(req: httpx.Request) -> httpx.Response:
    u = str(req.url)
    if "api.ashbyhq.com/posting-api/job-board/ramp" in u: return httpx.Response(200, json=ASHBY)
    if "api.smartrecruiters.com/v1/companies/Visa/postings" in u: return httpx.Response(200, json=SR)
    if "apply.workable.com/api/v1/widget/accounts/globex" in u: return httpx.Response(200, json=WK)
    if "careers.initrode.com" in u: return httpx.Response(200, text=EMBED)
    if "boards-api.greenhouse.io/v1/boards/initrode" in u: return httpx.Response(200, json=GH2)
    if "bing.com/search" in u: searched.append(u); return httpx.Response(200, text=RSS)
    return httpx.Response(404)
scraper._client = lambda t: httpx.AsyncClient(transport=httpx.MockTransport(handler2), timeout=t, follow_redirects=True, headers=scraper.HEADERS)
for s_ in watchlist.list_sources(): watchlist.remove_source(s_.id)
srcs = [watchlist.add_source(u) for u in ("https://jobs.ashbyhq.com/ramp", "https://careers.smartrecruiters.com/Visa",
                                          "https://apply.workable.com/globex", "https://careers.initrode.com/jobs")]
assert [x.kind for x in srcs] == ["ashby", "smartrecruiters", "workable", "page"]
cands, results = asyncio.run(discovery.discover(srcs))
assert {c.title for c in cands} == {"Software Engineer Intern", "Senior Engineer", "Data Intern", "IT Intern", "Marketing Intern", "QA Intern"}, cands
assert next(c for c in cands if c.title == "IT Intern").url == "https://jobs.smartrecruiters.com/Visa/77"
init = next(x for x in watchlist.list_sources() if "initrode" in x.url)
assert (init.kind, init.ident) == ("greenhouse", "initrode"), "a career page powered by Greenhouse is read through its API from now on"
print("B1 ok: Ashby, SmartRecruiters, Workable; a career page embedding Greenhouse is switched to the API")

prefs = watchlist.Prefs(roles=["backend intern"], places=["Malaysia", "Remote"], interns_only=True, web_search=True)
cands, results = asyncio.run(discovery.discover(srcs[:3], prefs))
titles = {c.title for c in cands}
assert "Senior Engineer" not in titles and "Data Intern" not in titles, "filtered: not an internship / Singapore"
assert {"Software Engineer Intern", "IT Intern", "Marketing Intern"} <= titles
assert {"Backend Intern", "Platform Intern at Pied Piper"} & titles and "Hooli careers" not in titles and "Some blog" not in titles, titles
assert searched and "site%3Ajobs.lever.co" in searched[0] and "backend+intern" in searched[0].lower()
ws = next(r for r in results if r.label == "Web search"); assert ws.ok and ws.found == 2
boards = {x.url: x for x in watchlist.list_sources()}
assert boards["https://jobs.lever.co/hooli"].auto == watchlist.AUTO_SEARCH and "https://boards.greenhouse.io/piedpiper" in boards, boards
assert sum(r.skipped for r in results) >= 2
watchlist.remove_source(boards["https://jobs.lever.co/hooli"].id)
asyncio.run(discovery.discover([], prefs))
assert "https://jobs.lever.co/hooli" not in {x.url for x in watchlist.list_sources()}, "a removed company is not added back by the search"
print("B2 ok: web search finds postings, adds their companies, respects removals; filters drop senior roles and other places")

from bot.services import websearch
assert websearch.is_posting("https://jobs.lever.co/hooli/abc") and not websearch.is_posting("https://jobs.lever.co/hooli")
assert websearch.is_posting("https://boards.greenhouse.io/x/jobs/1") and not websearch.is_posting("https://boards.greenhouse.io/x")
q = websearch.build_queries(["data intern"], ["Malaysia", "Remote"]); assert len(q) == 1 and '"data intern" Malaysia' in q[0]
ddg = '<a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fjobs.lever.co%2Fx%2F1&amp;rut=1">X Intern</a>'
assert websearch._ddg(ddg) == [("X Intern", "https://jobs.lever.co/x/1")]
print("B3 ok: search helpers (postings vs board pages, queries, DuckDuckGo redirect links)")

# Matcher filters after reading: closed, another place, not an internship, the same job you already have
cs = [C("Intern A", "u/1", "x" * 500), C("Intern B", "u/2", "x" * 500), C("Intern C", "u/3", "x" * 500), C("Intern D", "u/4", "x" * 500)]
jobs = {"Intern A": _job("Intern A", deadline="2020-01-01"), "Intern B": _job("Intern B", location="London"),
        "Intern C": _job("Intern C", company="Dupe Co"), "Intern D": _job("Intern D", is_internship=True)}
db.upsert_application("Dupe Co", "Intern C", job_link="https://elsewhere/1")
matcher._analyse = lambda text: (jobs[text.split("\n")[0]], ["Python"], [])
r = asyncio.run(matcher.match(cs, max_keep=4, min_fit=0.1, prefs=prefs))
why = {x.cand.title: x.rejected for x in r.scored}
assert why["Intern A"].startswith("closed") and why["Intern B"] == "in London" and "already" in why["Intern C"] and why["Intern D"] == ""
assert [k.cand.title for k in r.kept] == ["Intern D"]
print("B4 ok: Matcher skips closed jobs, other places and jobs you already have a resume for")

# the scan works with only the web search (no pages added yet)
for s_ in watchlist.list_sources(): watchlist.remove_source(s_.id)
watchlist.set_setting("scan_web_search", "1"); watchlist.set_setting("scan_roles", "backend intern")
tailored.clear(); matcher._analyse = lambda text: (_job(text.split("\n")[0], is_internship=True), ["Python"], [])
matcher._read = lambda cand: asyncio.sleep(0, result=cand.title + "\nposting")
b, s, _ = scan()
assert s.searched and s.found >= 1 and tailored, (b.sent, s)
print("B5 ok: a scan with no pages still finds jobs through the web search")
assert "ok:" in watchlist.get_setting("search_last", ""), "Status shows whether the last web search got answers"

# jobs not read in full wait in line for the next check instead of being dismissed by their title
watchlist.mark_seen("https://w/1", "Waiting Intern", "Co", "waiting")
assert not watchlist.seen("https://w/1") and watchlist.known("https://w/1")
with db._get_connection() as conn:
    conn.execute("INSERT INTO seen_jobs (url, status, first_seen) VALUES ('https://w/old', 'waiting', datetime('now', '-30 days'))"); conn.commit()
assert watchlist.expire_waiting() == 1 and watchlist.seen("https://w/old") and not watchlist.seen("https://w/1")
many = [{"title": f"Backend Intern {i}", "absolute_url": f"https://boards.greenhouse.io/acme-corp/jobs/q{i}", "content": "x" * 500} for i in range(12)]
GH["jobs"] = many
def handler3(req):
    if "boards-api.greenhouse.io/v1/boards/acme-corp" in str(req.url): return httpx.Response(200, json=GH)
    return httpx.Response(404)
scraper._client = lambda t: httpx.AsyncClient(transport=httpx.MockTransport(handler3), timeout=t, follow_redirects=True, headers=scraper.HEADERS)
watchlist.set_setting("scan_web_search", "0")
for s_ in watchlist.list_sources(): watchlist.remove_source(s_.id)
watchlist.add_source("https://boards.greenhouse.io/acme-corp")
matcher._analyse = lambda text: (_job(text.split("\n")[0], is_internship=True), [], ["Rust"])   # read, but low fit
b, s, _ = scan()
waiting = [j["absolute_url"] for j in many if not watchlist.seen(j["absolute_url"])]
assert s.found == 12 and s.left_waiting == len(waiting) > 0 and "waiting in line" in b.sent[-1], (s, b.sent[-1])
b, s2, _ = scan()
assert s2.found == 0 and s2.waiting == len(waiting), "the next check reads the ones still waiting (not counted as new)"
print("B6 ok: jobs not read in full wait for the next check; old ones expire")
print("ALL SCAN TESTS PASSED")
