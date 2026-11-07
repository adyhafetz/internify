"""Offline test of the AI helpers. The model is replaced by a fake, so no key or network is needed."""
from __future__ import annotations
import asyncio, os, sys, tempfile
import httpx

sys.path.insert(0, os.path.abspath("."))
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "app.db")
from bot import config
from bot.services import ai, db, discovery, scraper, watchlist
from service import config as sc, events

db.init_db()
config.AI_HELPERS = True; sc.OPENAI_API_KEY = "sk-test"
CALLS = []

def fake(answers):
    def _ask(model, response_model, system, user):
        CALLS.append((model, response_model.__name__))
        a = answers[response_model.__name__]
        if isinstance(a, Exception): raise a
        return a(user) if callable(a) else a
    return _ask

# ---- Scout: a login page is rejected, a real posting accepted; the AI being down changes nothing
ai._ask = fake({"_PostingCheck": lambda u: ai._PostingCheck(is_job_posting="Sign in" not in u, what_it_is="a login page")})
assert asyncio.run(ai.looks_like_posting("Sign in to continue " * 20)) == (False, "a login page")
assert asyncio.run(ai.looks_like_posting("Backend intern. Python, SQL. " * 20)) == (True, "")
ai._ask = fake({"_PostingCheck": RuntimeError("model down")})
assert asyncio.run(ai.looks_like_posting("anything")) == (True, ""), "fails open"
config.AI_HELPERS = False
assert asyncio.run(ai.looks_like_posting("Sign in")) == (True, "") and CALLS[-1][1] == "_PostingCheck", "off means no call"
config.AI_HELPERS = True
print("A1 ok: posting check rejects a login page, fails open")

# ---- Scout chain: the first route returns a login page, the AI sends it on to the next route
JOB = "https://careers.acme.com/jobs/software-engineer-intern-payments-4411"
GOOD = "Software Engineer Intern, Payments at Acme. Build backend services with Python and PostgreSQL. " * 5
LOGIN = "Sign in to Acme Careers to continue. Forgot password? Create account. " * 4

def site(req: httpx.Request) -> httpx.Response:
    u = str(req.url)
    if "r.jina.ai" in u: return httpx.Response(200, text=GOOD)
    return httpx.Response(200, text=f"<html><title>x</title><body><main>{LOGIN}</main></body></html>")

scraper._client = lambda t: httpx.AsyncClient(transport=httpx.MockTransport(site), timeout=t, follow_redirects=True, headers=scraper.HEADERS)
config.SCRAPINGANT_API_KEY, config.SCOUT_FALLBACKS = "", True
ai._ask = fake({"_PostingCheck": lambda u: ai._PostingCheck(is_job_posting="Sign in" not in u, what_it_is="a login page")})
async def go():
    rid = events.start_run(url=JOB)
    page = await scraper.scrape_job_url(JOB)
    return page, [e for e in events.bus.get_run(rid) if e["agent"] == "scout" and e["data"].get("status") == "failed"]
page, fails = asyncio.run(go())
assert page and page.method == "reader" and "Software Engineer Intern" in page.text, page and page.method
assert any("it wasn't a job posting, it was a login page" == e["data"]["reason"] for e in fails), [e["data"] for e in fails]
print("A2 ok: Scout skips a login page the plain rules accepted and uses the next route")

# ---- Discoverer: AI reads a career page, invented links are dropped, AI down falls back to the rules
PAGE = "# Careers\n[ML Engineer Intern](/jobs/ml-77)\n[Platform Developer](https://careers.initech.com/careers/platform-5)\n[Our story](/about)\n"
def listing(req): return httpx.Response(200, text=PAGE)
scraper._client = lambda t: httpx.AsyncClient(transport=httpx.MockTransport(listing), timeout=t, follow_redirects=True, headers=scraper.HEADERS)
config.SCRAPINGANT_API_KEY = ""
src = watchlist.add_source("https://careers.initech.com/jobs")
ai._ask = fake({"_FoundJobs": ai._FoundJobs(jobs=[ai._FoundJob(title="ML Engineer Intern", url="/jobs/ml-77"),
                                                   ai._FoundJob(title="Made Up Role", url="/jobs/does-not-exist"),
                                                   ai._FoundJob(title="Platform Developer", url="https://careers.initech.com/careers/platform-5")])})
cands, res = asyncio.run(discovery.discover([src]))
assert sorted(c.title for c in cands) == ["ML Engineer Intern", "Platform Developer"], cands
assert any(m == "_FoundJobs" for _, m in CALLS)
ai._ask = fake({"_FoundJobs": RuntimeError("down")})
cands, res = asyncio.run(discovery.discover([src]))
assert res[0].ok and {c.title for c in cands} == {"ML Engineer Intern", "Platform Developer"}, "plain rules still find them"
print("A3 ok: Discoverer reads a page with AI, drops invented links, falls back to rules")

# ---- search route: same-job judgement
ai._ask = fake({"_SameJob": ai._SameJob(same_job=True)})
assert asyncio.run(ai.is_same_job("acme payments intern", "Acme payments internship ...")) is True
ai._ask = fake({"_SameJob": RuntimeError("down")})
assert asyncio.run(ai.is_same_job("x", "y")) is False, "fails closed: never accept a wrong job"
print("A4 ok: same-job check")

# ---- Matcher: one plain sentence, template kept when the AI is down
ai._ask = fake({"_Why": ai._Why(sentence="You already use Python and SQL; Kubernetes is the only gap.")})
assert asyncio.run(ai.explain_match("Intern", "Acme", ["Python"], ["Kubernetes"], "tmpl")).startswith("You already")
ai._ask = fake({"_Why": RuntimeError("down")})
assert asyncio.run(ai.explain_match("Intern", "Acme", ["Python"], [], "tmpl")) == "tmpl"
print("A5 ok: match explanation")
print("ALL AI TESTS PASSED")
