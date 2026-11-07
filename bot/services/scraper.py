"""Scout: reads a job posting, and finds another way in when the page is blocked or empty.

Routes, tried in order until one returns a real posting (each attempt is reported to Telegram and the dashboard):
  1. ScrapingAnt (only if a key is set)        2. a normal visit (+ job data built into the page)
  3. a page-rendering service (Jina Reader)    4. a saved copy from the web archive (Wayback Machine)
  5. the same posting on another site (web search, accepted only if its text matches the job)
If all fail, ``scrape_job_url`` returns None and the bot asks the user to paste the job text.
Routes 3 to 5 send the job link to those services; switch them off with SCOUT_FALLBACKS=false."""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Awaitable, Callable, Optional
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from bs4 import BeautifulSoup

from bot import config
from bot.services import ai
from service import events

logger = logging.getLogger(__name__)

MIN_PAGE_CHARS = 100  # a page with less text than this is a block page or an empty shell

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
HEADERS = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}

# Words that show up on "prove you're human" pages instead of the job
BLOCK_MARKERS = ("access denied", "captcha", "verify you are human", "are you a robot", "enable javascript",
                 "just a moment", "attention required", "checking your browser", "unusual traffic")
STOPWORDS = {"job", "jobs", "career", "careers", "apply", "the", "and", "for", "with", "position", "opening",
             "remote", "full", "time", "part", "www", "com", "html", "view", "details"}


@dataclass
class Page:
    text: str
    method: str            # scrapingant | direct | reader | archive | search | pasted
    label: str             # plain-language name of the route, e.g. "a saved copy from the web archive"
    source_url: str        # where the text really came from
    via_fallback: bool     # True when it did not come from the page the user sent
    note: str = ""         # e.g. "saved on 12 Mar 2026" or "found on example.com"


class _Miss(Exception):
    """A route did not work. The message is a short plain-language reason shown to the user."""


def _client(timeout: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=HEADERS)


# ---------------------------------------------------------------- reading pages

def _status_reason(code: int) -> str:
    if code in (401, 403, 429, 503):
        return f"the site blocked us (HTTP {code})"
    if code in (404, 410):
        return f"the page is gone (HTTP {code})"
    return f"the site answered with an error (HTTP {code})"


def _looks_blocked(text: str) -> bool:
    low = text.lower()
    return len(text) < 2000 and any(m in low for m in BLOCK_MARKERS)


def _jobposting_text(soup: BeautifulSoup) -> str:
    """Many job sites embed the posting as schema.org JSON-LD even when the visible page is an empty shell."""
    def walk(node):
        if isinstance(node, list):
            for item in node:
                yield from walk(item)
        elif isinstance(node, dict):
            kind = node.get("@type")
            if kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind):
                yield node
            for v in node.values():
                if isinstance(v, (list, dict)):
                    yield from walk(v)

    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except Exception:
            continue
        for job in walk(data):
            org = job.get("hiringOrganization") or {}
            loc = job.get("jobLocation") or {}
            loc = loc[0] if isinstance(loc, list) and loc else loc
            addr = (loc.get("address") or {}) if isinstance(loc, dict) else {}
            desc = BeautifulSoup(str(job.get("description") or ""), "html.parser").get_text("\n")
            parts = [f"Job title: {job.get('title', '')}",
                     f"Company: {org.get('name', '') if isinstance(org, dict) else org}",
                     f"Location: {addr.get('addressLocality', '') if isinstance(addr, dict) else ''}",
                     f"Description:\n{desc}"]
            for key in ("qualifications", "skills", "responsibilities", "experienceRequirements"):
                if job.get(key):
                    parts.append(f"{key.title()}: {BeautifulSoup(str(job[key]), 'html.parser').get_text(' ')}")
            return "\n".join(p for p in parts if p.strip())
    return ""


def _parse_html(html: str) -> tuple[str, str, str]:
    """Returns (visible text, job data embedded in the page, page title)."""
    soup = BeautifulSoup(html, "html.parser")
    title = ""
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        title = og["content"].strip()
    elif soup.title and soup.title.string:
        title = soup.title.string.strip()
    embedded = _jobposting_text(soup)
    for tag in soup(["script", "style", "nav", "header", "footer", "noscript", "svg"]):
        tag.decompose()
    lines = [ln.strip() for ln in soup.get_text(separator="\n").splitlines() if ln.strip()]
    return "\n".join(lines), embedded, title


async def _fetch(client: httpx.AsyncClient, url: str) -> tuple[str, str, str]:
    """GET a page and return (text, embedded job data, title). Raises _Miss with a plain reason."""
    if urlparse(url).scheme not in ("http", "https"):
        raise _Miss("that isn't a web address")
    try:
        resp = await client.get(url)
    except httpx.TimeoutException:
        raise _Miss("it took too long to answer")
    except httpx.HTTPError:
        raise _Miss("couldn't connect to it")
    if resp.status_code >= 400:
        raise _Miss(_status_reason(resp.status_code))
    return _parse_html(resp.text)


def _pick(text: str, embedded: str) -> Optional[str]:
    """The best usable text from a fetched page, or None if it's a block page or an empty shell."""
    if len(text) >= MIN_PAGE_CHARS and not _looks_blocked(text):
        return text
    if len(embedded) >= MIN_PAGE_CHARS:
        return embedded
    return None


def _empty_or_blocked(text: str) -> _Miss:
    return _Miss("the site asked us to prove we're human" if _looks_blocked(text) else "the page came back empty")


# ---------------------------------------------------------------- the routes

class _Ctx:
    """Shared between routes: what we learned about the job from pages that did not give us the posting."""
    def __init__(self, url: str) -> None:
        self.url = url
        self.title = ""

    def hint(self) -> str:
        """Words that describe the job: the page title if we saw one, otherwise the link's own words."""
        title = self.title if self.title and not any(m in self.title.lower() for m in BLOCK_MARKERS) else ""
        if title:
            return title
        segments = [s for s in urlparse(self.url).path.split("/") if re.search(r"[a-zA-Z]{3}", s)]
        if not segments:
            return ""
        words = [w for w in re.split(r"[-_+\s]+", unquote(max(segments, key=len))) if re.fullmatch(r"[A-Za-z]{2,}", w)]
        return " ".join(words)


async def _via_scrapingant(ctx: _Ctx) -> Page:
    try:
        async with _client(45.0) as client:
            resp = await client.get("https://api.scrapingant.com/v2/markdown", params={"url": ctx.url},
                                    headers={"x-api-key": config.SCRAPINGANT_API_KEY})
    except httpx.TimeoutException:
        raise _Miss("it took too long to answer")
    except httpx.HTTPError:
        raise _Miss("couldn't connect to it")
    text = resp.text.strip()
    if resp.status_code != 200:
        raise _Miss(_status_reason(resp.status_code))
    if len(text) < MIN_PAGE_CHARS or _looks_blocked(text):
        raise _empty_or_blocked(text)
    return Page(text, "scrapingant", "a scraping service", ctx.url, False)


async def _via_direct(ctx: _Ctx) -> Page:
    async with _client(30.0) as client:
        text, embedded, title = await _fetch(client, ctx.url)
    ctx.title = title or ctx.title
    chosen = _pick(text, embedded)
    if chosen is None:
        raise _empty_or_blocked(text)
    note = "used the job details built into the page" if chosen == embedded and chosen != text else ""
    return Page(chosen, "direct", "a normal visit", ctx.url, False, note)


async def _via_reader(ctx: _Ctx) -> Page:
    """Jina Reader renders the page (including JavaScript) and returns it as plain text."""
    try:
        async with _client(45.0) as client:
            resp = await client.get(f"https://r.jina.ai/{ctx.url}", headers={"Accept": "text/plain"})
    except httpx.TimeoutException:
        raise _Miss("it took too long to answer")
    except httpx.HTTPError:
        raise _Miss("couldn't connect to it")
    if resp.status_code != 200:
        raise _Miss(_status_reason(resp.status_code))
    text = resp.text.strip()
    if len(text) < MIN_PAGE_CHARS or _looks_blocked(text):
        raise _empty_or_blocked(text)
    return Page(text, "reader", "a page-rendering service", ctx.url, True, "read through a page-rendering service")


async def _via_archive(ctx: _Ctx) -> Page:
    """The Wayback Machine keeps copies of many pages, so a blocked job page may still be readable there."""
    try:
        async with _client(20.0) as client:
            resp = await client.get("https://archive.org/wayback/available", params={"url": ctx.url})
            snap = (resp.json().get("archived_snapshots") or {}).get("closest") or {}
    except (httpx.HTTPError, ValueError):
        raise _Miss("the web archive didn't answer")
    if not snap.get("available") or not snap.get("url"):
        raise _Miss("the web archive has no saved copy")
    snap_url = re.sub(r"^http://", "https://", snap["url"])
    try:
        d = datetime.strptime(str(snap.get("timestamp", ""))[:8], "%Y%m%d")
        saved = f"{d.day} {d:%b %Y}"
    except ValueError:
        saved = "an earlier date"
    async with _client(30.0) as client:
        text, embedded, _ = await _fetch(client, snap_url)
    chosen = _pick(text, embedded)
    if chosen is None:
        raise _Miss("the saved copy was empty too")
    return Page(chosen, "archive", "a saved copy from the web archive", snap_url, True, f"a saved copy from {saved}, which may be out of date")


def _tokens(hint: str) -> list[str]:
    return [w for w in dict.fromkeys(re.findall(r"[a-z0-9+#.]{3,}", hint.lower())) if w not in STOPWORDS]


def _matches(tokens: list[str], text: str) -> bool:
    """A candidate page is the same job only if most of the job's words appear in it."""
    low = text.lower()
    return len(tokens) >= 2 and sum(t in low for t in tokens) / len(tokens) >= 0.7


async def _via_search(ctx: _Ctx) -> Page:
    """Search for the same posting on another site; only accept a page whose text matches the job's words."""
    hint = ctx.hint()
    tokens = _tokens(hint)
    if len(tokens) < 2:
        raise _Miss("the link doesn't say what the job is")
    try:
        async with _client(20.0) as client:
            resp = await client.get("https://html.duckduckgo.com/html/", params={"q": f"{hint} job"})
    except httpx.HTTPError:
        raise _Miss("the search engine didn't answer")
    if resp.status_code != 200:
        raise _Miss("the search engine wouldn't answer")
    original_host = urlparse(ctx.url).netloc
    candidates: list[str] = []
    for a in BeautifulSoup(resp.text, "html.parser").select("a.result__a"):
        href = a.get("href", "")
        target = parse_qs(urlparse(href).query).get("uddg", [href])[0]
        if urlparse(target).scheme in ("http", "https") and urlparse(target).netloc != original_host:
            candidates.append(target)
    if not candidates:
        raise _Miss("no other site had this posting")
    for target in candidates[:4]:
        try:
            async with _client(25.0) as client:
                text, embedded, _ = await _fetch(client, target)
        except _Miss:
            continue
        chosen = _pick(text, embedded)
        if chosen and (_matches(tokens, chosen) or await ai.is_same_job(hint, chosen)):
            host = urlparse(target).netloc.removeprefix("www.")
            return Page(chosen, "search", "the same posting on another site", target, True, f"found on {host}")
    raise _Miss("no other site had a matching posting")


# ---------------------------------------------------------------- the chain

Route = tuple[str, str, Callable[[_Ctx], Awaitable[Page]]]


def _routes() -> list[Route]:
    routes: list[Route] = []
    if config.SCRAPINGANT_API_KEY:
        routes.append(("scrapingant", "a scraping service", _via_scrapingant))
    routes.append(("direct", "a normal visit", _via_direct))
    if config.SCOUT_FALLBACKS:
        routes += [("reader", "a page-rendering service", _via_reader),
                   ("archive", "a saved copy from the web archive", _via_archive),
                   ("search", "the same posting on another site", _via_search)]
    return routes


async def scrape_job_url(url: str) -> Optional[Page]:
    """Try each route until one returns the posting. Returns None if every route fails (the caller then asks the
    user to paste the text). Every attempt is reported with ``events`` so Telegram and the dashboard can show it."""
    routes = _routes()
    ctx = _Ctx(url)
    started = time.monotonic()
    for i, (method, label, route) in enumerate(routes, start=1):
        if i > 1 and time.monotonic() - started > config.SCOUT_BUDGET_SECONDS:
            events.emit("scout", events.PROGRESS, "Out of time, stopping the search",
                        attempt=i, total=len(routes), method=method, label=label, status="skipped",
                        reason="that was taking too long")
            break
        events.emit("scout", events.PROGRESS, f"Trying {label}", attempt=i, total=len(routes),
                    method=method, label=label, status="trying")
        try:
            page = await route(ctx)
        except _Miss as miss:
            reason = str(miss)
        except Exception as err:  # a bug in one route must not end the search
            logger.warning("Scout route %s crashed: %s", method, err)
            reason = "something went wrong with it"
        else:
            if page.method != "pasted" and ai.enabled():
                events.emit("scout", events.PROGRESS, "Checking it's really a job posting", check=True)
                real, what = await ai.looks_like_posting(page.text)
                if not real:
                    logger.info("Scout route %s returned %s", method, what)
                    events.emit("scout", events.PROGRESS, f"{label} didn't work", attempt=i, total=len(routes),
                                method=method, label=label, status="failed", reason=f"it wasn't a job posting, it was {what}")
                    continue
            events.emit("scout", events.PROGRESS, f"Got the posting from {label}", attempt=i, total=len(routes),
                        method=method, label=label, status="ok", chars=len(page.text), note=page.note,
                        source_url=page.source_url)
            return page
        logger.info("Scout route %s failed for %s: %s", method, url, reason)
        events.emit("scout", events.PROGRESS, f"{label} didn't work", attempt=i, total=len(routes),
                    method=method, label=label, status="failed", reason=reason)
    return None
