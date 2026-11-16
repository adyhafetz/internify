"""What every job source has in common: the Candidate a source returns, polite fetching, and reading a page as text."""
from __future__ import annotations

import asyncio
import html as htmllib
import random
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from bot import config
from bot.services import scraper
from service import metering


@dataclass
class Candidate:
    title: str
    company: str
    url: str
    location: str = ""
    snippet: str = ""   # the description when the source provides it; empty for sources that give only a title
    source: str = ""


class SourceError(Exception):
    """A source could not answer. The message is a short, plain reason shown in ⚙️ Status."""


class KeywordSource:
    """A place that can be searched with a keyword. Subclasses set ``key`` (the setting that switches it on or off)
    and ``name`` and implement ``search``."""
    key = ""
    name = ""

    async def search(self, keyword: str, places: list[str], limit: int, interns_only: bool) -> list[Candidate]:
        raise NotImplementedError


async def pause(low: float = 1.0, high: float = 2.0) -> None:
    """A short random wait between requests to the same site (tests replace this)."""
    await asyncio.sleep(random.uniform(low, high))


async def get(url: str, *, params: Optional[dict] = None, headers: Optional[dict] = None, timeout: float = 25.0) -> httpx.Response:
    """One GET, with a plain reason when it fails."""
    try:
        async with scraper._client(timeout) as client:
            resp = await client.get(url, params=params, headers=headers)
    except httpx.TimeoutException:
        raise SourceError("it took too long to answer")
    except httpx.HTTPError:
        raise SourceError("couldn't connect to it")
    if resp.status_code == 429:
        raise SourceError("it is limiting how fast we ask (too many requests)")
    if resp.status_code >= 400:
        raise SourceError(scraper._status_reason(resp.status_code))
    return resp


async def read_listing(url: str) -> str:
    """The text (markdown or HTML) of a listing page, by the first route that works: ScrapingAnt if you have a key,
    a normal visit, or a page-rendering service."""
    last: Optional[Exception] = None
    if config.SCRAPINGANT_API_KEY:
        metering.count("scrape")
        try:
            async with scraper._client(45.0) as client:
                resp = await client.get("https://api.scrapingant.com/v2/markdown", params={"url": url},
                                        headers={"x-api-key": config.SCRAPINGANT_API_KEY})
                resp.raise_for_status()
                return resp.text
        except Exception as err:
            last = err
    try:
        async with scraper._client(30.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.text
    except Exception as err:
        last = err
    if config.SCOUT_FALLBACKS:
        try:
            async with scraper._client(45.0) as client:
                resp = await client.get(f"https://r.jina.ai/{url}", headers={"Accept": "text/plain"})
                resp.raise_for_status()
                return resp.text
        except Exception as err:
            last = err
    raise SourceError("couldn't read the page") if last is None else SourceError(_why(last))


def _why(err: Exception) -> str:
    if isinstance(err, httpx.TimeoutException):
        return "it took too long to answer"
    if isinstance(err, httpx.HTTPStatusError):
        return scraper._status_reason(err.response.status_code)
    return "couldn't connect to it"


def text_of(markup: str) -> str:
    return re.sub(r"\s+", " ", BeautifulSoup(htmllib.unescape(markup or ""), "html.parser").get_text(" ")).strip()


_LINKEDIN_ID = re.compile(r"(?:/jobs/view/(?:[^/?#]*-)?|currentJobId=)(\d{6,})")


def linkedin_id(url: str) -> Optional[str]:
    m = _LINKEDIN_ID.search(url or "")
    return m.group(1) if m else None


def normalize_url(url: str) -> str:
    """One spelling per job, so the same posting found twice is recognised: LinkedIn's country sub-domains and tracking
    parameters are dropped; other links lose only the #fragment and trailing slash."""
    url = (url or "").strip().split("#")[0]
    if "linkedin.com" in urlparse(url).netloc and (job_id := linkedin_id(url)):
        return f"https://www.linkedin.com/jobs/view/{job_id}"
    return url.rstrip("/")


def find_job_dicts(data, limit: int = 200) -> list[dict]:
    """Job-like objects hidden anywhere in a page's embedded JSON: anything with a title and a company name. Used for
    sites (JobStreet, Hiredly) whose pages carry their results as JSON, so a change in the layout around it doesn't
    matter as long as the job objects keep a title and a company."""
    found: list[dict] = []

    def company_of(d: dict) -> str:
        for key in ("companyName", "company_name", "company", "employer", "advertiser", "hiringOrganization"):
            v = d.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
            if isinstance(v, dict):
                for k in ("name", "description", "label", "companyName"):
                    if isinstance(v.get(k), str) and v[k].strip():
                        return v[k].strip()
        return ""

    def walk(node) -> None:
        if len(found) >= limit:
            return
        if isinstance(node, dict):
            title = node.get("title") or node.get("jobTitle") or node.get("job_title") or node.get("name")
            if isinstance(title, str) and 3 <= len(title) <= 160 and company_of(node):
                found.append(node)
            for v in node.values():
                if isinstance(v, (dict, list)):
                    walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    return found


def job_fields(d: dict) -> tuple[str, str, str, str]:
    """(title, company, location, description) from a job-like dict, whichever spelling the site uses."""
    title = str(d.get("title") or d.get("jobTitle") or d.get("job_title") or d.get("name") or "").strip()
    company = ""
    for key in ("companyName", "company_name", "company", "employer", "advertiser", "hiringOrganization"):
        v = d.get(key)
        if isinstance(v, str) and v.strip():
            company = v.strip()
            break
        if isinstance(v, dict):
            company = next((str(v[k]).strip() for k in ("name", "description", "label", "companyName") if v.get(k)), "")
            if company:
                break
    loc = d.get("location") or d.get("locations") or d.get("jobLocation") or d.get("city") or ""
    if isinstance(loc, list) and loc:
        loc = loc[0]
    if isinstance(loc, dict):
        loc = loc.get("label") or loc.get("name") or loc.get("addressLocality") or ""
    desc = d.get("teaser") or d.get("description") or d.get("summary") or d.get("snippet") or ""
    return title, company, str(loc).strip(), text_of(str(desc))[:3000]
