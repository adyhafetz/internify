"""The Discoverer: finds new jobs for the daily check.

  * Company boards on Greenhouse, Lever, Ashby, SmartRecruiters and Workable are read through their public job-listing
    APIs: titles, links, locations (and usually the description), with no scraping.
  * A career page is read (ScrapingAnt if you have a key, a normal visit, or a page-rendering service). If it turns out
    to be powered by one of those hiring systems, the page is switched to the API for good; otherwise the job links
    are picked out of it (by AI if available, plain rules if not).
  * The web search (on by default) looks for postings that match what you look for, so the companies you watch grow
    by themselves instead of being a fixed list (see websearch.py).
Your filters (internships only, places) are applied here, before anything is read in full, so big boards don't crowd
out the few jobs that fit. Sources are visited one at a time, politely. A source that fails never stops the others."""
from __future__ import annotations

import html as htmllib
import logging
import re
from dataclasses import dataclass, replace
from typing import Optional
from urllib.parse import urldefrag, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from bot import config
from bot.services import ai, scraper
from bot.services.watchlist import BOARD_KINDS, Prefs, Source, classify, mark_checked, place_ok, retarget, title_ok
from service import events

logger = logging.getLogger(__name__)

MAX_PER_SOURCE = 60   # kept per source, after your filters
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
    skipped: int = 0          # left out by your filters


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


async def _ashby(src: Source) -> list[Candidate]:
    async with scraper._client(25.0) as client:
        data = (await _get(client, f"https://api.ashbyhq.com/posting-api/job-board/{src.ident}")).json()
    out = []
    for j in data.get("jobs", []):
        if not j.get("title") or not j.get("jobUrl") or j.get("isListed") is False:
            continue
        loc = j.get("location") or ""
        if j.get("isRemote"):
            loc = f"{loc}, Remote".strip(", ")
        extra = " ".join(x.get("location", "") for x in (j.get("secondaryLocations") or []) if isinstance(x, dict))
        out.append(Candidate(title=j["title"].strip(), company=src.label, url=j["jobUrl"], location=f"{loc} {extra}".strip(),
                             snippet=(j.get("employmentType", "") + " " + (j.get("descriptionPlain") or "")).strip()[:3000],
                             source=src.label))
    return out


async def _smartrecruiters(src: Source) -> list[Candidate]:
    async with scraper._client(25.0) as client:
        data = (await _get(client, f"https://api.smartrecruiters.com/v1/companies/{src.ident}/postings", params={"limit": 100})).json()
    out = []
    for p in data.get("content", []):
        if not p.get("name") or not p.get("id"):
            continue
        loc = p.get("location") or {}
        where = loc.get("fullLocation") or ", ".join(x for x in (loc.get("city"), loc.get("country")) if x)
        if loc.get("remote"):
            where = f"{where}, Remote".strip(", ")
        kind = " ".join(((p.get(k) or {}).get("label") or "") for k in ("typeOfEmployment", "experienceLevel"))
        out.append(Candidate(title=p["name"].strip(), company=src.label, url=f"https://jobs.smartrecruiters.com/{src.ident}/{p['id']}",
                             location=where, snippet=kind.strip(), source=src.label))
    return out


async def _workable(src: Source) -> list[Candidate]:
    async with scraper._client(25.0) as client:
        data = (await _get(client, f"https://apply.workable.com/api/v1/widget/accounts/{src.ident}", params={"details": "true"})).json()
    out = []
    for j in data.get("jobs", []):
        url = j.get("url") or j.get("application_url") or (f"https://apply.workable.com/{src.ident}/j/{j['shortcode']}" if j.get("shortcode") else "")
        if not j.get("title") or not url:
            continue
        where = ", ".join(x for x in (j.get("city"), j.get("state"), j.get("country")) if x)
        if j.get("telecommuting"):
            where = f"{where}, Remote".strip(", ")
        out.append(Candidate(title=j["title"].strip(), company=data.get("name") or src.label, url=url, location=where,
                             snippet=(str(j.get("employment_type") or "") + " " + _text_of(j.get("description", ""))).strip()[:3000],
                             source=src.label))
    return out


_BOARD_LINK = re.compile(r"https?://(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/[^\s\"'<>)]+|"
                         r"https?://jobs(?:\.eu)?\.lever\.co/[^\s\"'<>)]+|https?://jobs\.ashbyhq\.com/[^\s\"'<>)]+|"
                         r"https?://apply\.workable\.com/[^\s\"'<>)]+|https?://(?:careers|jobs)\.smartrecruiters\.com/[^\s\"'<>)]+")


def detect_board(text: str) -> Optional[tuple[str, str]]:
    """(kind, board name) if a career page is really a board on a hiring system we can read through its API
    (it links to or embeds it), else None. The board linked most often wins."""
    counts: dict[tuple[str, str], int] = {}
    for m in _BOARD_LINK.finditer(htmllib.unescape(text or "")):
        kind, ident, _ = classify(m.group(0).rstrip(".,;"))
        if kind in BOARD_KINDS and ident:
            counts[(kind, ident)] = counts.get((kind, ident), 0) + 1
    return max(counts, key=counts.get) if counts else None


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
    board = detect_board(text)
    if board is not None:   # the page is powered by a hiring system with an API: use that (and remember it)
        kind, ident = board
        try:
            found = await _READERS[kind](replace(src, kind=kind, ident=ident))
            retarget(src.id, kind, ident)
            events.emit("discoverer", events.PROGRESS, f"{src.label} uses {kind.title()}: reading its job list directly",
                        label=src.label, board=kind)
            return found
        except Exception as err:
            logger.info("Board %s/%s behind %s didn't answer (%s); reading the page instead", kind, ident, src.url, err)
    links = None
    if ai.enabled():
        events.emit("discoverer", events.PROGRESS, f"Reading {src.label} with AI", ai_read=src.label)
        links = await ai.find_jobs_on_page(text, src.url)
    if not links:   # AI off, failed or found nothing: fall back to the plain rules
        links = extract_job_links(text, src.url)
    return [Candidate(title=t, company=src.label, url=u, source=src.label) for t, u in links]


_READERS = {"greenhouse": _greenhouse, "lever": _lever, "ashby": _ashby, "smartrecruiters": _smartrecruiters,
            "workable": _workable, "page": _page}


def _wanted(c: Candidate, prefs: Optional[Prefs]) -> bool:
    """Your filters, on what is known before the posting is read: the title, and the location if the board gave one."""
    if prefs is None:
        return True
    return title_ok(c.title, prefs, c.snippet)[0] and place_ok(c.location, prefs)


async def _search_web(prefs: Prefs, seen_urls: set[str]) -> tuple[list[Candidate], Optional[SourceResult]]:
    from bot.services import websearch
    events.emit("discoverer", events.PROGRESS, "Searching the web for matching postings", status="trying", label="Web search")
    try:
        postings, queries = await websearch.find_postings(prefs)
    except Exception as err:
        logger.info("Web search failed: %s", err)
        _remember_search("failed: the search engines didn't answer")
        return [], SourceResult("Web search", False, 0, "the search engines didn't answer")
    if not queries:
        return [], None
    found = [Candidate(title=t, company=co, url=u, source="Web search") for t, co, u in postings]
    kept = [c for c in found if c.url not in seen_urls and _wanted(c, prefs)]
    seen_urls.update(c.url for c in kept)
    events.emit("discoverer", events.PROGRESS, f"Web search: {len(kept)} jobs", status="ok", label="Web search",
                found=len(kept), queries=queries)
    if not postings:
        _remember_search("no results: the search engines may be blocking this server")
        return [], SourceResult("Web search", False, 0, "no results today")
    _remember_search(f"ok: {len(postings)} postings, {len(kept)} new that fit your filters")
    return kept, SourceResult("Web search", True, len(kept), skipped=len(found) - len(kept))


def _remember_search(text: str) -> None:
    """For ⚙️ Status: did the last web search get answers?"""
    import time
    try:
        from bot.services.watchlist import set_setting
        set_setting("search_last", time.strftime("%d %b %H:%M") + " · " + text)
    except Exception:
        pass


async def discover(sources: list[Source], prefs: Optional[Prefs] = None) -> tuple[list[Candidate], list[SourceResult]]:
    """Visit every source (and search the web if that is on). Returns (all candidates that pass your filters, one
    result per source). Progress goes out as Discoverer events."""
    candidates: list[Candidate] = []
    results: list[SourceResult] = []
    seen_urls: set[str] = set()
    for src in sources:
        events.emit("discoverer", events.PROGRESS, f"Checking {src.label}", status="trying", label=src.label)
        try:
            found = await _READERS[src.kind](src)
            wanted = [c for c in found if c.url and c.url not in seen_urls and _wanted(c, prefs)]
            fresh = wanted[:MAX_PER_SOURCE]
            seen_urls.update(c.url for c in fresh)
            candidates += fresh
            results.append(SourceResult(src.label, True, len(fresh), skipped=len(found) - len(wanted)))
            mark_checked(src.id, f"ok: {len(fresh)} jobs" + (f" ({len(found) - len(wanted)} filtered out)" if len(found) > len(wanted) else ""))
            events.emit("discoverer", events.PROGRESS, f"{src.label}: {len(fresh)} jobs", status="ok", label=src.label, found=len(fresh))
        except Exception as err:   # one bad source must never stop the scan
            reason = _reason(err)
            logger.info("Source %s failed: %s", src.url, err)
            results.append(SourceResult(src.label, False, 0, reason))
            mark_checked(src.id, f"failed: {reason}")
            events.emit("discoverer", events.PROGRESS, f"{src.label} didn't answer", status="failed", label=src.label, reason=reason)
    if prefs is not None and prefs.web_search:
        found, result = await _search_web(prefs, seen_urls)
        candidates += found
        if result is not None:
            results.append(result)
    return candidates, results
