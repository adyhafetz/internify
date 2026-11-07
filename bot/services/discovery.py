"""The Discoverer: finds job links on the sources you watch.

  * Greenhouse and Lever boards are read through their public job-listing APIs: titles, links, locations and the full
    description, with no scraping.
  * Any other career page is read (ScrapingAnt if you have a key, a normal visit, or a page-rendering service) and the
    job links are picked out of it. Those candidates carry only a title, so the Matcher reads the best of them later.

Sources are visited one at a time, politely. A source that fails never stops the others."""
from __future__ import annotations

import html as htmllib
import logging
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urldefrag, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from bot import config
from bot.services import ai, scraper
from bot.services.watchlist import Source, mark_checked
from service import events

logger = logging.getLogger(__name__)

MAX_PER_SOURCE = 60
ROLE_WORDS = ("engineer", "developer", "intern", "analyst", "scientist", "designer", "manager", "associate", "graduate",
              "researcher", "specialist", "consultant", "administrator", "architect", "programmer", "technician", "trainee")
JOB_PATH = re.compile(r"/(jobs?|careers?|positions?|openings?|vacanc\w*|postings?|roles?|apply|opportunit\w*)(/|$|\?|-)|gh_jid=|/j/|/o/", re.I)
NAV_WORDS = ("home", "about", "contact", "log in", "login", "sign in", "sign up", "privacy", "terms", "cookie", "blog", "press",
             "view all", "see all", "all jobs", "search jobs", "benefits", "faq", "back to", "skip to", "learn more", "read more")


@dataclass
class Candidate:
    title: str
    company: str
    url: str
    location: str = ""
    snippet: str = ""   # the description when the source provides it (Greenhouse, Lever); empty for plain pages
    source: str = ""


@dataclass
class SourceResult:
    label: str
    ok: bool
    found: int = 0
    reason: str = ""


def _text_of(markup: str) -> str:
    return re.sub(r"\s+", " ", BeautifulSoup(htmllib.unescape(markup or ""), "html.parser").get_text(" ")).strip()


def _reason(err: Exception) -> str:
    if isinstance(err, httpx.TimeoutException):
        return "it took too long to answer"
    if isinstance(err, httpx.HTTPStatusError):
        return scraper._status_reason(err.response.status_code)
    if isinstance(err, httpx.HTTPError):
        return "couldn't connect to it"
    if isinstance(err, (ValueError, KeyError, TypeError)):
        return "it answered in a way I couldn't read"
    return "something went wrong with it"


async def _get(client: httpx.AsyncClient, url: str, **kw) -> httpx.Response:
    resp = await client.get(url, **kw)
    resp.raise_for_status()
    return resp


# ---------------------------------------------------------------- job-board APIs

async def _greenhouse(src: Source) -> list[Candidate]:
    async with scraper._client(25.0) as client:
        data = (await _get(client, f"https://boards-api.greenhouse.io/v1/boards/{src.ident}/jobs", params={"content": "true"})).json()
    return [Candidate(title=j.get("title", "").strip(), company=src.label, url=j.get("absolute_url", ""),
                      location=(j.get("location") or {}).get("name", ""), snippet=_text_of(j.get("content", ""))[:3000], source=src.label)
            for j in data.get("jobs", []) if j.get("title") and j.get("absolute_url")]


async def _lever(src: Source) -> list[Candidate]:
    async with scraper._client(25.0) as client:
        data = (await _get(client, f"https://api.lever.co/v0/postings/{src.ident}", params={"mode": "json"})).json()
    if not isinstance(data, list):
        raise ValueError("unexpected answer")
    return [Candidate(title=p.get("text", "").strip(), company=src.label, url=p.get("hostedUrl", ""),
                      location=(p.get("categories") or {}).get("location", "") or "",
                      snippet=((p.get("descriptionPlain") or "") + " " + (p.get("additionalPlain") or "")).strip()[:3000], source=src.label)
            for p in data if p.get("text") and p.get("hostedUrl")]


# ---------------------------------------------------------------- any other career page

async def _listing(url: str) -> str:
    """The text (markdown or HTML) of a listing page, by the first route that works."""
    last: Optional[Exception] = None
    if config.SCRAPINGANT_API_KEY:
        try:
            async with scraper._client(45.0) as client:
                return (await _get(client, "https://api.scrapingant.com/v2/markdown", params={"url": url},
                                   headers={"x-api-key": config.SCRAPINGANT_API_KEY})).text
        except Exception as err:
            last = err
    try:
        async with scraper._client(30.0) as client:
            return (await _get(client, url)).text
    except Exception as err:
        last = err
    if config.SCOUT_FALLBACKS:
        try:
            async with scraper._client(45.0) as client:
                return (await _get(client, f"https://r.jina.ai/{url}", headers={"Accept": "text/plain"})).text
        except Exception as err:
            last = err
    raise last or RuntimeError("no route worked")


def extract_job_links(text: str, base_url: str) -> list[tuple[str, str]]:
    """Pick (title, url) pairs that look like individual job postings out of a listing page (markdown or HTML)."""
    pairs: list[tuple[str, str]] = []
    if "<a " in text.lower() or "<html" in text.lower():
        for a in BeautifulSoup(text, "html.parser").find_all("a", href=True):
            pairs.append((a.get_text(" ", strip=True), a["href"]))
    pairs += [(m.group(1), m.group(2)) for m in re.finditer(r"\[([^\]\n]{3,160})\]\(([^)\s]+)\)", text)]
    base = urlparse(base_url)
    out, seen = [], set()
    for title, href in pairs:
        title = re.sub(r"\s+", " ", title).strip()
        url = urldefrag(urljoin(base_url, href.strip()))[0]
        u = urlparse(url)
        low = title.lower()
        if u.scheme not in ("http", "https") or not 8 <= len(title) <= 140 or url in seen or url.rstrip("/") == base_url.rstrip("/"):
            continue
        if any(w in low for w in NAV_WORDS) or low.startswith(("http", "www.")):
            continue
        same_site = u.netloc.removeprefix("www.") == base.netloc.removeprefix("www.") or u.netloc.endswith(
            ("greenhouse.io", "lever.co", "ashbyhq.com", "workable.com", "smartrecruiters.com", "myworkdayjobs.com"))
        if not same_site:
            continue
        if not (JOB_PATH.search(u.path + ("?" + u.query if u.query else "")) or any(w in low for w in ROLE_WORDS)):
            continue
        seen.add(url)
        out.append((title, url))
    return out


async def _page(src: Source) -> list[Candidate]:
    text = await _listing(src.url)
    links = None
    if ai.enabled():
        events.emit("discoverer", events.PROGRESS, f"Reading {src.label} with AI", ai_read=src.label)
        links = await ai.find_jobs_on_page(text, src.url)
    if not links:   # AI off, failed or found nothing: fall back to the plain rules
        links = extract_job_links(text, src.url)
    return [Candidate(title=t, company=src.label, url=u, source=src.label) for t, u in links]


_READERS = {"greenhouse": _greenhouse, "lever": _lever, "page": _page}


async def discover(sources: list[Source]) -> tuple[list[Candidate], list[SourceResult]]:
    """Visit every source. Returns (all candidates, one result per source). Progress goes out as Discoverer events."""
    candidates: list[Candidate] = []
    results: list[SourceResult] = []
    seen_urls: set[str] = set()
    for src in sources:
        events.emit("discoverer", events.PROGRESS, f"Checking {src.label}", status="trying", label=src.label)
        try:
            found = (await _READERS[src.kind](src))[:MAX_PER_SOURCE]
            fresh = [c for c in found if c.url and c.url not in seen_urls]
            seen_urls.update(c.url for c in fresh)
            candidates += fresh
            results.append(SourceResult(src.label, True, len(fresh)))
            mark_checked(src.id, f"ok: {len(fresh)} jobs")
            events.emit("discoverer", events.PROGRESS, f"{src.label}: {len(fresh)} jobs", status="ok", label=src.label, found=len(fresh))
        except Exception as err:   # one bad source must never stop the scan
            reason = _reason(err)
            logger.info("Source %s failed: %s", src.url, err)
            results.append(SourceResult(src.label, False, 0, reason))
            mark_checked(src.id, f"failed: {reason}")
            events.emit("discoverer", events.PROGRESS, f"{src.label} didn't answer", status="failed", label=src.label, reason=reason)
    return candidates, results
