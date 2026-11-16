"""LinkedIn: the public job search that its own website shows to visitors who are not signed in (no account, no login).
Most Malaysian internship postings appear here. Each posting also has a public page, which the Scout reads
(see ``job_page`` and scraper.py).

This is not an official API: LinkedIn can change or block it at any time, so the source can be switched off in
/profile, and its health shows in ⚙️ Status."""
from __future__ import annotations

from typing import Optional

from bs4 import BeautifulSoup

from bot.services.sources import base
from bot.services.sources.base import Candidate, KeywordSource

SEARCH = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
POSTING = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{id}"
PAGE_SIZE = 25
MAX_PAGES = 2


def parse_search(markup: str) -> list[Candidate]:
    """The job cards on one page of search results."""
    out: list[Candidate] = []
    for card in BeautifulSoup(markup, "html.parser").select("li"):
        link = card.select_one("a.base-card__full-link") or card.select_one('a[href*="/jobs/view/"]')
        title = card.select_one(".base-search-card__title")
        if not link or not title:
            continue
        company = card.select_one(".base-search-card__subtitle")
        place = card.select_one(".job-search-card__location")
        url = base.normalize_url(link.get("href", ""))
        if not base.linkedin_id(url):
            continue
        out.append(Candidate(title=title.get_text(" ", strip=True), company=company.get_text(" ", strip=True) if company else "",
                             url=url, location=place.get_text(" ", strip=True) if place else "", source="LinkedIn"))
    return out


def parse_posting(markup: str) -> str:
    """The text of one posting from its public page: title, company, location, description and the job facts."""
    soup = BeautifulSoup(markup, "html.parser")
    pick = lambda sel: (soup.select_one(sel).get_text(" ", strip=True) if soup.select_one(sel) else "")
    desc = soup.select_one("div.show-more-less-html__markup") or soup.select_one(".description__text")
    body = desc.get_text("\n", strip=True) if desc else ""
    if not body:
        return ""
    facts = [f"{h.get_text(' ', strip=True)}: {v.get_text(' ', strip=True)}"
             for li in soup.select("li.description__job-criteria-item")
             if (h := li.select_one(".description__job-criteria-subheader")) and (v := li.select_one(".description__job-criteria-text"))]
    parts = [f"Job title: {pick('.top-card-layout__title') or pick('h1') or pick('h2')}",
             f"Company: {pick('.topcard__org-name-link') or pick('a.topcard__org-name-link') or pick('.top-card-layout__card .topcard__flavor')}",
             f"Location: {pick('.topcard__flavor--bullet')}", f"Description:\n{body}"] + facts
    return "\n".join(p for p in parts if p.split(":", 1)[-1].strip())


async def job_page(url: str) -> Optional[str]:
    """The text of one LinkedIn posting through its public page, or None if the link isn't a LinkedIn posting."""
    job_id = base.linkedin_id(url)
    if not job_id:
        return None
    resp = await base.get(POSTING.format(id=job_id))
    return parse_posting(resp.text)


class LinkedIn(KeywordSource):
    key, name = "linkedin", "LinkedIn"

    async def search(self, keyword: str, places: list[str], limit: int, interns_only: bool) -> list[Candidate]:
        out: dict[str, Candidate] = {}
        for place in places or ["Malaysia"]:
            for page in range(MAX_PAGES):
                params = {"keywords": keyword, "location": place, "start": page * PAGE_SIZE, "f_TPR": "r604800"}
                if interns_only:
                    params["f_JT"] = "I"      # job type: internship
                if page:
                    await base.pause()
                cards = parse_search((await base.get(SEARCH, params=params)).text)
                for c in cards:
                    out.setdefault(c.url, c)
                if len(cards) < PAGE_SIZE or len(out) >= limit:
                    break
            if len(out) >= limit:
                break
        return list(out.values())[:limit]
