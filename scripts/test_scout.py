"""Offline test of Scout's routes for blocked/empty pages. Websites are faked with canned responses (no network)."""
from __future__ import annotations
import asyncio, json, os, sys
import httpx

sys.path.insert(0, os.path.abspath("."))
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
from bot import config
from bot.services import ai as _ai; _ai.enabled = lambda: False   # plain-rules behaviour; the AI helpers are covered in test_ai.py
from bot.services import scraper
from service import events

JOB = "https://careers.acme.com/jobs/software-engineer-intern-payments-4411"
POSTING = ("Software Engineer Intern, Payments\nAcme Corp\nBuild backend services with Python and PostgreSQL. " * 4)
LONG = "x" * 10


def page(body: str, title="Software Engineer Intern - Payments | Acme") -> str:
    return f"<html><head><title>{title}</title></head><body><main>{body}</main></body></html>"


def ddg(*urls) -> str:
    return "<html><body>" + "".join(f'<a class="result__a" href="//duckduckgo.com/l/?uddg={httpx.URL(u)}&rut=1">r</a>' for u in urls) + "</body></html>"


def run(handler, *, key="", fallbacks=True):
    """Run scrape_job_url against fake websites; returns (page, list of scout attempt events)."""
    config.SCRAPINGANT_API_KEY, config.SCOUT_FALLBACKS = key, fallbacks
    scraper._client = lambda timeout: httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=timeout,
                                                        follow_redirects=True, headers=scraper.HEADERS)
    async def go():
        rid = events.start_run(url=JOB)
        got = await scraper.scrape_job_url(JOB)
        return got, [e for e in events.bus.get_run(rid) if e["agent"] == "scout" and e["data"].get("status")]
    return asyncio.run(go())


def trail(att):  # "direct:failed(the site blocked us (HTTP 403)) > reader:ok"
    return " > ".join(f"{a['data']['method']}:{a['data']['status']}" + (f"({a['data']['reason']})" if a["data"].get("reason") else "")
                      for a in att if a["data"]["status"] != "trying")


# 1. normal page: one attempt, not a fallback
p, att = run(lambda r: httpx.Response(200, text=page(POSTING)))
assert p.method == "direct" and not p.via_fallback and len(att) == 2, trail(att)       # trying + ok
print("1 ok: normal page ->", trail(att))

# 2. blocked, then the page-rendering service reads it
def h2(r):
    if r.url.host == "r.jina.ai": return httpx.Response(200, text=POSTING)
    return httpx.Response(403, text="denied")
p, att = run(h2)
assert p.method == "reader" and p.via_fallback and "blocked us (HTTP 403)" in trail(att), trail(att)
assert [a["data"]["attempt"] for a in att if a["data"]["status"] == "trying"] == [1, 2], "attempts numbered 1, 2"
print("2 ok: blocked -> rendering service ->", trail(att))

# 3. blocked + renderer empty -> saved copy from the web archive, with its date
def h3(r):
    if r.url.host == "r.jina.ai": return httpx.Response(200, text="short")
    if r.url.host == "archive.org":
        return httpx.Response(200, json={"archived_snapshots": {"closest": {"available": True, "timestamp": "20260312101500",
                                         "url": "http://web.archive.org/web/20260312101500/" + JOB}}})
    if r.url.host == "web.archive.org": return httpx.Response(200, text=page(POSTING))
    return httpx.Response(403)
p, att = run(h3)
assert p.method == "archive" and "12 Mar 2026" in p.note and p.source_url.startswith("https://web.archive.org"), p
assert "reader:failed(the page came back empty)" in trail(att)
print("3 ok: archive copy ->", p.note)

# 4. nothing else has it -> the same posting on another site, but a WRONG posting is rejected
def h4(r):
    if r.url.host == "html.duckduckgo.com":
        return httpx.Response(200, text=ddg("https://other.example/wrong-job", "https://jobs.example.org/intern-payments"))
    if r.url.host == "other.example": return httpx.Response(200, text=page("Senior Marketing Manager at Globex. " * 10, "Marketing"))
    if r.url.host == "jobs.example.org": return httpx.Response(200, text=page(POSTING))
    if r.url.host in ("r.jina.ai",): return httpx.Response(500)
    if r.url.host == "archive.org": return httpx.Response(200, json={"archived_snapshots": {}})
    return httpx.Response(403)
p, att = run(h4)
assert p.method == "search" and p.source_url == "https://jobs.example.org/intern-payments" and "jobs.example.org" in p.note, p
assert "archive:failed(the web archive has no saved copy)" in trail(att)
print("4 ok: found elsewhere, the unrelated posting was rejected ->", p.note)

# 5. everything fails -> None (the bot then asks the user to paste), every reason is plain language
p, att = run(lambda r: httpx.Response(403, text="denied") if r.url.host != "html.duckduckgo.com" else httpx.Response(202))
assert p is None
reasons = [a["data"]["reason"] for a in att if a["data"]["status"] == "failed"]
assert len(reasons) == 4 and all(not any(w in x.lower() for w in ("exception", "traceback", "httpx")) for x in reasons), reasons
print("5 ok: all routes failed ->", "; ".join(reasons))

# 6. an empty JavaScript shell that still carries the job as JSON-LD
ld = json.dumps({"@context": "https://schema.org", "@type": "JobPosting", "title": "Backend Intern",
                 "hiringOrganization": {"name": "Acme"}, "description": "<p>" + "Build Python services. " * 8 + "</p>"})
shell = f'<html><head><script type="application/ld+json">{ld}</script></head><body><div id="root"></div></body></html>'
p, att = run(lambda r: httpx.Response(200, text=shell))
assert p.method == "direct" and not p.via_fallback and "Backend Intern" in p.text and p.note.startswith("used the job details"), p
print("6 ok: JavaScript shell -> read the job data built into the page")

# 7. a "prove you're human" page counts as blocked
p, att = run(lambda r: httpx.Response(200, text=page("Just a moment... verify you are human", "Just a moment...")) if r.url.host == "careers.acme.com" else httpx.Response(200, text=POSTING) if r.url.host == "r.jina.ai" else httpx.Response(403))
assert p.method == "reader" and "asked us to prove we're human" in trail(att), trail(att)
print("7 ok: human-check page treated as blocked")

# 8. ScrapingAnt key set but it is blocked too -> still falls through
p, att = run(lambda r: httpx.Response(403) if r.url.host == "api.scrapingant.com" else httpx.Response(200, text=page(POSTING)), key="k")
assert p.method == "direct" and trail(att).startswith("scrapingant:failed"), trail(att)
print("8 ok: scraping service blocked -> normal visit")

# 9. fallbacks switched off -> only the direct route is tried
p, att = run(lambda r: httpx.Response(403), fallbacks=False)
assert p is None and len([a for a in att if a["data"]["status"] == "failed"]) == 1
print("9 ok: SCOUT_FALLBACKS=false -> only the normal visit")

async def _decline(ctx):
    try: await scraper._via_search(ctx)
    except scraper._Miss as m: return str(m)


# 10. a link that doesn't say what the job is can't be searched for
JOB_ID_ONLY = scraper._Ctx("https://x.com/jobs/12345")
assert scraper._tokens(JOB_ID_ONLY.hint()) == [], "a bare id gives nothing to search for"
assert asyncio.run(_decline(JOB_ID_ONLY)) == "the link doesn't say what the job is"
assert scraper._Ctx(JOB).hint() == "software engineer intern payments"
print("10 ok: job hint comes from the link's words, empty when the link is just an id")

# 11. wording for the user
for status, extra in (("trying", {}), ("failed", {"reason": "the site blocked us (HTTP 403)"}), ("ok", {})):
    ev = {"agent": "scout", "kind": "progress", "message": "", "data": {"status": status, "attempt": 2, "total": 4,
          "label": "a page-rendering service", **extra}}
    print("   say:", events.describe(ev))
print("\n✅ scout test passed")
