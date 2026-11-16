"""Offline test of keyword discovery: the LinkedIn, JobStreet, Hiredly and open-web sources (read from sample pages shaped
like the real ones), their health records, the filters, and how they join the daily check. The websites and the AI are faked.
Nothing here proves the real sites still answer in this shape: that is what ⚙️ Status shows after a real check."""
from __future__ import annotations
import asyncio, json, os, sys, tempfile, types
import httpx

sys.path.insert(0, os.path.abspath("."))
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "app.db")
from bot import config
from bot.services import ai, db, discovery, scraper, sources, watchlist, websearch
from bot.services.sources import base, hiredly, jobstreet, linkedin, openweb
ai.enabled = lambda: False   # plain-rules behaviour unless a test says otherwise

db.init_db()
config.SCRAPINGANT_API_KEY, config.SCOUT_FALLBACKS = "", False
sleeps = []
async def _nap(*a, **k): sleeps.append(1)
base.pause = _nap

# ---------------------------------------------------------------- sample pages
def card(job_id, title, company, place, slug="x"):
    return (f'<li><div class="base-card relative w-full" data-entity-urn="urn:li:jobPosting:{job_id}">'
            f'<a class="base-card__full-link" href="https://my.linkedin.com/jobs/view/{slug}-{job_id}?position=1&amp;pageNum=0&amp;refId=abc&amp;trackingId=xyz"></a>'
            f'<div class="base-search-card__info"><h3 class="base-search-card__title">{title}</h3>'
            f'<h4 class="base-search-card__subtitle"><a class="hidden-nested-link" href="https://my.linkedin.com/company/x">{company}</a></h4>'
            f'<div class="base-search-card__metadata"><span class="job-search-card__location">{place}</span>'
            f'<time class="job-search-card__listdate" datetime="2026-11-14">2 days ago</time></div></div></div></li>')

LI_PAGE1 = "".join(card(3900000000 + i, f"Software Engineer Intern {i}", "Grab", "Kuala Lumpur, Federal Territory of Kuala Lumpur, Malaysia") for i in range(25))
LI_PAGE2 = card(3900000100, "Data Analyst Intern", "Shopee", "Petaling Jaya, Selangor, Malaysia") + card(3900000000, "Software Engineer Intern 0", "Grab", "Kuala Lumpur, Malaysia")
LI_POSTING = """<html><body><div class="top-card-layout"><h2 class="top-card-layout__title">Software Engineer Intern</h2>
<a class="topcard__org-name-link" href="x">Grab</a><span class="topcard__flavor topcard__flavor--bullet">Kuala Lumpur, Malaysia</span></div>
<div class="show-more-less-html__markup"><p>Join our platform team to build and test backend services in Python and Go.</p>
<ul><li>Currently pursuing a degree in Computer Science</li><li>Knowledge of SQL, REST APIs and Docker</li></ul></div>
<ul><li class="description__job-criteria-item"><h3 class="description__job-criteria-subheader">Employment type</h3>
<span class="description__job-criteria-text">Internship</span></li></ul></body></html>"""

JS = {"data": [{"id": "88123456", "title": "Software Developer Intern", "companyName": "Maybank", "workTypes": ["Internship"],
                "locations": [{"label": "Kuala Lumpur"}], "teaser": "Build internal tools with Java and SQL."},
               {"id": "88123457", "title": "Marketing Executive", "advertiser": {"id": "1", "description": "Acme Sdn Bhd"},
                "locations": [{"label": "Penang"}], "teaser": "Run campaigns"}], "totalCount": 2}

HR_NEXT = {"props": {"pageProps": {"jobs": [{"id": 7, "title": "Backend Engineer Intern", "slug": "backend-engineer-intern-petronas-7",
                                              "company": {"name": "Petronas Digital"}, "location": "Kuala Lumpur", "description": "<p>APIs in Python</p>"}]}}}
HR_PAGE = f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(HR_NEXT)}</script></html>'
HR_LINKS = ('<html><body><a href="/jobs/qa-intern-setel-123">QA Intern at Setel</a> <a href="/about">About us today</a>'
            '<a href="https://my.hiredly.com/jobs/data-intern-axiata-55">Data Intern at Axiata</a></body></html>')

RSS = ("<rss><channel>"
       "<item><title>Software Intern - Careers at Shopee</title><link>https://careers.shopee.com.my/job-detail/J02/1</link></item>"
       "<item><title>Latihan Industri Developer | Petronas</title><link>https://www.petronas.com/careers/jobs/internship-123</link></item>"
       "<item><title>10 tips for internships</title><link>https://medium.com/@x/10-tips-internship</link></item>"
       "<item><title>Software Intern in Malaysia</title><link>https://www.facebook.com/jobs/post/1</link></item>"
       "<item><title>Careers</title><link>https://example.com/careers</link></item></channel></rss>")

seen_requests: list[httpx.Request] = []
state = {"li": "ok", "js": "ok", "hr": "ok"}
def handler(req: httpx.Request) -> httpx.Response:
    seen_requests.append(req); u = str(req.url)
    if "jobs-guest/jobs/api/seeMoreJobPostings/search" in u:
        if state["li"] == "blocked": return httpx.Response(429, text="slow down")
        if state["li"] == "empty": return httpx.Response(200, text="")
        start = int(req.url.params.get("start", "0"))
        return httpx.Response(200, text=LI_PAGE1 if start == 0 else LI_PAGE2)
    if "jobs-guest/jobs/api/jobPosting/3900000000" in u: return httpx.Response(200, text=LI_POSTING)
    if "my.jobstreet.com/api/jobsearch" in u:
        if state["js"] == "bad": return httpx.Response(200, text="<html>not json</html>")
        return httpx.Response(200, json=JS)
    if "my.hiredly.com/jobs" in u: return httpx.Response(200, text=HR_PAGE if state["hr"] == "ok" else HR_LINKS)
    if "bing.com/search" in u: return httpx.Response(200, text=RSS)
    return httpx.Response(404)
scraper._client = lambda t: httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=t, follow_redirects=True, headers=scraper.HEADERS)
run = asyncio.run

# ---- 1. LinkedIn
cards = linkedin.parse_search(LI_PAGE1)
assert len(cards) == 25 and cards[0].url == "https://www.linkedin.com/jobs/view/3900000000", cards[0]
assert cards[0].company == "Grab" and "Kuala Lumpur" in cards[0].location and cards[0].title == "Software Engineer Intern 0"
found = run(linkedin.LinkedIn().search("software engineer intern", ["Malaysia"], 40, True))
assert len(found) == 26 and len({c.url for c in found}) == 26, "two pages, repeats merged"
first = next(r for r in seen_requests if "seeMoreJobPostings" in str(r.url))
assert first.url.params["f_JT"] == "I" and first.url.params["location"] == "Malaysia" and first.url.params["keywords"] == "software engineer intern"
seen_requests.clear(); run(linkedin.LinkedIn().search("x", ["Malaysia"], 40, False)); assert "f_JT" not in seen_requests[0].url.params
text = run(linkedin.job_page("https://my.linkedin.com/jobs/view/software-engineer-intern-at-grab-3900000000?trackingId=1"))
assert "Job title: Software Engineer Intern" in text and "Company: Grab" in text and "REST APIs and Docker" in text and "Employment type: Internship" in text, text
assert run(linkedin.job_page("https://example.com/jobs/1")) is None
assert base.normalize_url("https://my.linkedin.com/jobs/view/foo-bar-3900000000/?refId=1#x") == "https://www.linkedin.com/jobs/view/3900000000"
assert base.normalize_url("https://jobs.lever.co/acme/1/#apply") == "https://jobs.lever.co/acme/1"
print("1 ok: LinkedIn search pages, filters in the request, repeats merged, public posting page, one spelling per job")

# the Scout reads LinkedIn links through LinkedIn's public page first (a normal visit only meets a login wall)
li_link = "https://my.linkedin.com/jobs/view/software-engineer-intern-at-grab-3900000000"
assert [r[0] for r in scraper._routes(li_link)][0] == "linkedin" and "linkedin" not in [r[0] for r in scraper._routes("https://example.com/job/1")]
page = run(scraper.scrape_job_url(li_link))
assert page and page.method == "linkedin" and "Python and Go" in page.text and not page.via_fallback
print("2 ok: the Scout tries LinkedIn's public page first for LinkedIn links, and only for those")

# ---- 3. JobStreet, Hiredly
js = run(jobstreet.JobStreet().search("software developer intern", ["Kuala Lumpur"], 30, True))
assert [c.title for c in js] == ["Software Developer Intern", "Marketing Executive"], js
assert js[0].url == "https://my.jobstreet.com/job/88123456" and js[0].company == "Maybank" and js[0].location == "Kuala Lumpur" and "Internship" in js[0].snippet
assert js[1].company == "Acme Sdn Bhd", "a company written as an object is read too"
state["js"] = "bad"
try: run(jobstreet.JobStreet().search("x", [], 5, True)); raise SystemExit("should fail")
except base.SourceError as e: assert "couldn't read" in str(e)
state["js"] = "ok"
hr = run(hiredly.Hiredly().search("backend intern", ["Malaysia"], 30, True))
assert len(hr) == 1 and hr[0].title == "Backend Engineer Intern" and hr[0].company == "Petronas Digital" and hr[0].url == "https://my.hiredly.com/jobs/backend-engineer-intern-petronas-7", hr
state["hr"] = "links"
hr = run(hiredly.Hiredly().search("qa intern", ["Malaysia"], 30, True))
assert [c.title for c in hr] == ["QA Intern at Setel", "Data Intern at Axiata"], hr
state["hr"] = "ok"
print("3 ok: JobStreet and Hiredly read from the data inside their pages (or the job links); odd answers give a plain reason")

# ---- 4. open web: the AI picks single postings; plain rules when it is off; social and advice pages never
res = websearch._bing(RSS)
assert openweb.rules(res) == [0, 1], openweb.rules(res)
ow = run(openweb.OpenWeb().search("software intern", ["Malaysia"], 30, True))
assert [c.url for c in ow] == ["https://careers.shopee.com.my/job-detail/J02/1", "https://www.petronas.com/careers/jobs/internship-123"], ow
asked = []
async def fake_pick(results, keyword, place): asked.append((len(results), keyword, place)); return [1]
ai.pick_postings = fake_pick
ow = run(openweb.OpenWeb().search("software intern", ["Malaysia"], 30, True))
assert asked and asked[0][1:] == ("software intern", "Malaysia") and [c.url for c in ow] == ["https://www.petronas.com/careers/jobs/internship-123"]
assert asked[0][0] == 3, "social and blog pages are removed before the AI sees the list (5 results, 2 dropped)"
print("4 ok: open web search: AI picks postings (rules without it); social and advice pages are dropped")

# ---- 5. keywords and places
P = lambda roles, places=("Malaysia", "Remote"), interns=True: watchlist.Prefs(roles=list(roles), places=list(places), interns_only=interns, web_search=False)
kw = sources.keywords(P(["software engineer intern", "data analyst intern"]))
assert kw == ["software engineer intern", "data analyst intern", "software engineer latihan industri"], kw
assert sources.keywords(P(["backend developer"], interns=False)) == ["backend developer"]
assert sources.search_places(P([], ["Remote"])) == ["Malaysia"] and sources.search_places(P([], ["Selangor", "Penang", "Johor"])) == ["Selangor", "Penang"]
print("5 ok: keywords (with the Malay term for internships) and Malaysian places")

# ---- 6. health: one source failing never stops the others; zero results counts as a problem; a source rests after 3 in a row
for k in ("linkedin", "jobstreet", "hiredly", "openweb"): watchlist.set_setting(f"src_health_{k}", "")
prefs = P(["software engineer intern"])
state["li"] = "blocked"
cands, reports = run(sources.search_all(prefs))
by = {r.key: r for r in reports}
assert not by["linkedin"].ok and "limiting" in by["linkedin"].reason and by["jobstreet"].ok and by["hiredly"].ok and by["openweb"].ok, reports
assert {c.source for c in cands} == {"JobStreet", "Hiredly", "Web search"}
assert "didn't answer" in sources.health("linkedin")["status"] and sources.health("jobstreet")["status"].startswith("ok")
state["li"] = "empty"
_, reports = run(sources.search_all(prefs))
assert "no results" in sources.health("linkedin")["status"] and sources.health("linkedin")["fails"] == 2
run(sources.search_all(prefs)); assert sources.resting("linkedin")
n = len(seen_requests)
_, reports = run(sources.search_all(prefs))
assert next(r for r in reports if r.key == "linkedin").skipped and not any("seeMoreJobPostings" in str(r.url) for r in seen_requests[n:]), "a resting source is not asked"
h = sources.health("linkedin"); h["last_try"] -= 25 * 3600; watchlist.set_setting("src_health_linkedin", json.dumps(h))
state["li"] = "ok"
_, reports = run(sources.search_all(prefs))
assert next(r for r in reports if r.key == "linkedin").ok and sources.health("linkedin")["fails"] == 0 and not sources.resting("linkedin")
assert any("LinkedIn: ok" in line for line in sources.status_lines())
print("6 ok: failures are isolated and recorded; a quiet source is flagged; after 3 in a row it rests a day, then is tried again")

# ---- 7. switching a source off
assert sources.switch("hiredly") is False and not sources.is_on("hiredly") and "Hiredly: off" in sources.status_lines()
_, reports = run(sources.search_all(prefs)); assert "hiredly" not in {r.key for r in reports}
assert sources.switch("hiredly") is True
print("7 ok: sources can be switched off and on")

# ---- 8. the Discoverer: filters, repeats across sources, results labelled as searches
class Fake(base.KeywordSource):
    def __init__(self, key, name, items): self.key, self.name, self.items = key, name, items
    async def search(self, keyword, places, limit, interns_only): return self.items
C = lambda t, co, u, loc="", src="": base.Candidate(t, co, u, loc, "", src)
a = Fake("linkedin", "LinkedIn", [C("Software Engineer Intern", "Shopee Malaysia Sdn Bhd", "https://www.linkedin.com/jobs/view/111", "Kuala Lumpur, Malaysia", "LinkedIn"),
                                   C("Senior Software Engineer", "Grab", "https://www.linkedin.com/jobs/view/112", "Kuala Lumpur, Malaysia", "LinkedIn"),
                                   C("Data Intern", "Grab", "https://www.linkedin.com/jobs/view/113", "Singapore", "LinkedIn")])
b = Fake("jobstreet", "JobStreet", [C("Software Engineer Internship", "Shopee", "https://my.jobstreet.com/job/9", "Kuala Lumpur", "JobStreet"),
                                     C("Backend Intern", "Maybank", "https://my.jobstreet.com/job/10", "Kuala Lumpur", "JobStreet")])
c = Fake("hiredly", "Hiredly", [C("QA Intern at Setel", "", "https://my.hiredly.com/jobs/qa-1/", "", "Hiredly")])
sources.ALL[:] = [a, b, c]
prefs = P(["software engineer intern"])
cands, results = run(discovery.discover([], prefs))
titles = {(x.title, x.company) for x in cands}
assert ("Software Engineer Intern", "Shopee Malaysia Sdn Bhd") in titles and ("Backend Intern", "Maybank") in titles and ("QA Intern at Setel", "") in titles, titles
assert not any(t[0] in ("Senior Software Engineer", "Data Intern", "Software Engineer Internship") for t in titles), \
    "senior roles, other places and the same job from another source are dropped"
assert next(x for x in cands if x.company == "").url == "https://my.hiredly.com/jobs/qa-1", "links are normalised"
assert [r.kind for r in results] == ["search"] * 3 and [(r.label, r.found) for r in results] == [("LinkedIn", 1), ("JobStreet", 1), ("Hiredly", 1)]
assert next(r for r in results if r.label == "LinkedIn").skipped == 2
print("8 ok: Discoverer applies your filters, drops the same job found on two sources, reports each source")

# ---- 9. the daily check works with only keyword sources, and says what each found
from bot import scanner
from bot.jobflow import JobOutcome
from bot.services import matcher
scanner.profile_is_sample = lambda: False
for s_ in watchlist.list_sources(): watchlist.remove_source(s_.id)
watchlist.set_setting("scan_web_search", "0"); watchlist.set_setting("scan_roles", "software engineer intern")
sources.ALL[:] = [a, b, c]
matcher.taste_texts = lambda: ["backend python"]
matcher._embed = lambda texts: (_ for _ in ()).throw(RuntimeError("no embeddings"))
def _job(role, **kw): return types.SimpleNamespace(**{**dict(role=role, company="Co", location="", deadline=None, is_internship=True), **kw})
matcher._analyse = lambda text: (_job(text.split("\n")[0]), ["Python"], [])
matcher._read = lambda cand: asyncio.sleep(0, result=cand.title + "\nposting")
tailored = []
async def fake_tailor(url, reporter, *, bot, chat_id, loop, interactive, intro=""):
    tailored.append(url); out = JobOutcome(ok=True, company="Co", role="R", score=88.0, status="Good", pdf_path="/x.pdf")
    await reporter.finished(out); return out
scanner.tailor_job = fake_tailor
class Bot:
    def __init__(self): self.sent = []
    async def send_message(self, chat, text, **kw): self.sent.append(text)
    async def send_document(self, chat, **kw): self.sent.append("DOC")
bot = Bot(); summary = run(scanner.run_scan(bot, 1, "manual"))
assert summary.searched and summary.tailored >= 1 and tailored, (summary, bot.sent)
assert "Searched your keywords: LinkedIn 1, JobStreet 1, Hiredly 1" in bot.sent[-1], bot.sent[-1]
for k in ("linkedin", "jobstreet", "hiredly", "openweb"): watchlist.set_setting(f"src_{k}", "0")
bot = Bot(); run(scanner.run_scan(bot, 1, "manual")); assert "nowhere to look" in bot.sent[-1], bot.sent[-1]
print("9 ok: a daily check with no pages added works from keywords alone, and tells you what each source found")

# ---- 10. the screen in /profile: sources with their health, and switches
import bot.handlers.profile_hub as H
for k in ("linkedin", "jobstreet", "hiredly", "openweb"): watchlist.set_setting(f"src_{k}", "1")
sources.ALL[:] = [linkedin.LinkedIn(), jobstreet.JobStreet(), hiredly.Hiredly(), openweb.OpenWeb()]
text, kb = H.prefs_screen()
labels = [b_.text for row in kb.inline_keyboard for b_ in row]
assert "Keywords" in text and "LinkedIn: ok" in text and "official way to search" in text, text
assert {"✅ LinkedIn", "✅ JobStreet", "✅ Hiredly", "✅ Web search"} <= set(labels), labels
ctx = types.SimpleNamespace(user_data={})
screen = run(H._handle(None, ctx, "wfx", ["jobstreet"]))
assert "JobStreet is off" in screen[0] and not sources.is_on("jobstreet")
screen = run(H._handle(None, ctx, "wfx", ["nonsense"])); assert "Keywords" in screen[0]
print("10 ok: /profile shows each source with its health and lets you switch it")
print("\n✅ sources test passed")
