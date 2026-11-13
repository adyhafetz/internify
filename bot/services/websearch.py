"""Finds new job postings by searching the web, instead of only watching pages you listed.

The search is built from what you look for (roles and places in /profile, or the roles on your resume) and limited to
the big hiring systems that companies post on (Greenhouse, Lever, Ashby, Workable, SmartRecruiters, Workday). Those
pages are single postings with clean, readable text, and each one tells us the company's whole job board, which is
then added to the pages checked every day (marked "found by search"). So the list of companies grows by itself.

No key is needed: Bing's RSS results are read first, DuckDuckGo's plain HTML page if Bing answers with nothing.
A search that fails just means no extra jobs today; it never stops the daily check."""
from __future__ import annotations

import html as htmllib
import logging
import re
import xml.etree.ElementTree as ET
from typing import Optional
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

from bot.services import scraper, watchlist

logger = logging.getLogger(__name__)

SEARCH_SITES = ("boards.greenhouse.io", "job-boards.greenhouse.io", "jobs.lever.co", "jobs.ashbyhq.com",
                "apply.workable.com", "jobs.smartrecruiters.com", "myworkdayjobs.com")
MAX_QUERIES = 4
MAX_RESULTS = 40
MAX_SEARCH_BOARDS = 30   # companies added by the search at most (you can remove any of them)
DEFAULT_ROLE = "software engineer intern"


def default_roles() -> list[str]:
    """What to search for when you haven't said: the kind of roles on your resume, as internships."""
    try:
        from service.fact_store.store import load_skeleton
        titles = [e.title for e in load_skeleton().experience_entries]
    except Exception:
        titles = []
    roles: list[str] = []
    for t in titles:
        base = re.sub(r"(?i)\b(intern(ship)?|trainee|industrial training|part[- ]time|full[- ]time|junior|jr\.?)\b", " ", t)
        base = re.sub(r"[^A-Za-z&/ +#.-]", " ", base)
        base = re.sub(r"\s+", " ", base).strip(" -/&")
        if base and len(base) <= 40:
            role = f"{base.lower()} intern"
            if role not in roles:
                roles.append(role)
    return roles[:3] or [DEFAULT_ROLE]


def build_queries(roles: list[str], places: list[str]) -> list[str]:
    sites = " OR ".join(f"site:{s}" for s in SEARCH_SITES)
    places = [p for p in places if p.lower() != "remote"][:2] or [""]
    out = []
    for role in roles[:3]:
        for place in places:
            out.append(f"\"{role}\" {place} ({sites})".replace("  ", " "))
    return out[:MAX_QUERIES]


def _bing(text: str) -> list[tuple[str, str]]:
    out = []
    root = ET.fromstring(text)
    for item in root.iter("item"):
        title, link = item.findtext("title") or "", item.findtext("link") or ""
        if link:
            out.append((htmllib.unescape(title).strip(), link.strip()))
    return out


def _ddg(text: str) -> list[tuple[str, str]]:
    out = []
    for m in re.finditer(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', text, re.S):
        href, title = htmllib.unescape(m.group(1)), re.sub(r"<[^>]+>", "", m.group(2))
        if "uddg=" in href:   # DuckDuckGo wraps results in a redirect
            href = unquote(parse_qs(urlparse(href).query).get("uddg", [href])[0])
        if href.startswith("//"):
            href = "https:" + href
        out.append((htmllib.unescape(title).strip(), href))
    return out


async def search(query: str) -> list[tuple[str, str]]:
    """(title, url) results for one query. Empty if every engine failed."""
    routes = (("https://www.bing.com/search?format=rss&count=30&q=" + quote_plus(query), _bing),
              ("https://html.duckduckgo.com/html/?q=" + quote_plus(query), _ddg))
    for url, parse in routes:
        try:
            async with scraper._client(20.0) as client:
                resp = await client.get(url)
                resp.raise_for_status()
            results = parse(resp.text)
            if results:
                return results
        except Exception as err:
            logger.info("Web search via %s failed: %s", urlparse(url).netloc, err)
    return []


def is_posting(url: str) -> bool:
    """A single job posting (not a board's front page) on one of the hiring systems we search."""
    u = urlparse(url)
    host = (u.netloc or "").lower()
    segs = [s for s in u.path.split("/") if s]
    if not any(host == s or host.endswith("." + s) for s in SEARCH_SITES):
        return False
    if "greenhouse.io" in host:
        return "jobs" in segs or "gh_jid" in u.query
    if host.endswith("myworkdayjobs.com"):
        return "job" in segs
    return len(segs) >= 2


def _clean_title(title: str) -> str:
    title = re.sub(r"\s*[|\-–—]\s*(Greenhouse|Lever|Ashby|Workable|SmartRecruiters|Workday|Careers?|Jobs?)\b.*$", "", title, flags=re.I)
    return re.sub(r"^(Job Application for|Apply for)\s+", "", title, flags=re.I).strip()


async def find_postings(prefs: watchlist.Prefs) -> tuple[list[tuple[str, str, str]], list[str]]:
    """(postings as (title, company, url), the queries that were run). Company boards behind the postings are added
    to the pages checked every day."""
    roles = prefs.roles or default_roles()
    queries = build_queries(roles, prefs.places)
    seen: set[str] = set()
    postings: list[tuple[str, str, str]] = []
    for q in queries:
        for title, url in await search(q):
            url = url.split("#")[0]
            if url in seen or not is_posting(url):
                continue
            seen.add(url)
            kind, ident, label = watchlist.classify(url)
            company = label if kind != "page" else urlparse(url).netloc.split(".")[0].title()
            postings.append((_clean_title(title) or title, company, url))
            _remember_board(url)
            if len(postings) >= MAX_RESULTS:
                return postings, queries
    return postings, queries


def _remember_board(url: str) -> Optional[watchlist.Source]:
    """Add the company's board to the daily check (once), so its other openings are seen from now on."""
    kind, _, _ = watchlist.classify(url)
    root = None
    if kind in watchlist.BOARD_KINDS:
        root = url
    elif (br := watchlist.board_root(url)) is not None:
        root = br[0]
    if root is None or watchlist.count_auto(watchlist.AUTO_SEARCH) >= MAX_SEARCH_BOARDS:
        return None
    try:
        return watchlist.add_source(root, auto=watchlist.AUTO_SEARCH)
    except watchlist.SourceError:
        return None
