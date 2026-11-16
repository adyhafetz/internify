"""Hiredly, popular with Malaysian graduates. Its pages are built in the browser, so the search page is read the way any
career page is (ScrapingAnt if you have a key, a normal visit, then a page-rendering service). The jobs are taken from
the data inside the page when there is some, otherwise from the job links. This is the least certain source: the
AI-less fallback only finds what is in the page's own links. It can be switched off."""
from __future__ import annotations

import json
import re
from urllib.parse import quote_plus, urljoin

from bs4 import BeautifulSoup

from bot.services.sources import base
from bot.services.sources.base import Candidate, KeywordSource

SEARCH = "https://my.hiredly.com/jobs?search={q}"
_JOB_HREF = re.compile(r"/(jobs?|job-detail|jobs/detail)/[^/\s?#]+", re.I)


def parse(markup: str, page_url: str) -> list[Candidate]:
    out: dict[str, Candidate] = {}
    soup = BeautifulSoup(markup, "html.parser")
    blobs = [t.string for t in soup.find_all("script", id="__NEXT_DATA__") if t.string]
    blobs += [t.string for t in soup.find_all("script", type="application/ld+json") if t.string]
    for blob in blobs:
        try:
            data = json.loads(blob)
        except ValueError:
            continue
        for d in base.find_job_dicts(data):
            title, company, place, desc = base.job_fields(d)
            slug = d.get("slug") or d.get("url") or d.get("id")
            if not (title and company and slug):
                continue
            url = urljoin("https://my.hiredly.com/jobs/", str(slug)) if not str(slug).startswith("http") else str(slug)
            out.setdefault(base.normalize_url(url), Candidate(title, company, base.normalize_url(url), place, desc, "Hiredly"))
    if out:
        return list(out.values())
    for a in soup.find_all("a", href=True):   # no data in the page: use the job links
        title = a.get_text(" ", strip=True)
        href = urljoin(page_url, a["href"])
        if _JOB_HREF.search(href) and 8 <= len(title) <= 140 and "hiredly" in href:
            out.setdefault(base.normalize_url(href), Candidate(title, "", base.normalize_url(href), "", "", "Hiredly"))
    return list(out.values())


class Hiredly(KeywordSource):
    key, name = "hiredly", "Hiredly"

    async def search(self, keyword: str, places: list[str], limit: int, interns_only: bool) -> list[Candidate]:
        url = SEARCH.format(q=quote_plus(keyword))
        markup = await base.read_listing(url)
        found = parse(markup, url)
        if not found and "<a " not in markup.lower():   # a text answer (page-rendering service): job links are in the text
            from bot.services.discovery import extract_job_links
            found = [Candidate(t, "", base.normalize_url(u), "", "", "Hiredly") for t, u in extract_job_links(markup, url)
                     if "hiredly" in u]
        return found[:limit]
