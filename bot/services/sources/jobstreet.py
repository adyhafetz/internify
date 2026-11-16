"""JobStreet Malaysia, through the search that its own website uses (the same JSON the page loads). Malaysia's biggest
job board. Not an official API: it can change at any time, so the source can be switched off, and the reader looks for
job objects wherever they are in the answer instead of relying on one exact layout."""
from __future__ import annotations

from bot.services.sources import base
from bot.services.sources.base import Candidate, KeywordSource

API = "https://my.jobstreet.com/api/jobsearch/v5/search"
JOB_URL = "https://my.jobstreet.com/job/{id}"


def parse(data) -> list[Candidate]:
    out: list[Candidate] = []
    for d in base.find_job_dicts(data):
        title, company, place, desc = base.job_fields(d)
        job_id = d.get("id") or d.get("jobId")
        url = d.get("url") or d.get("jobUrl") or (JOB_URL.format(id=job_id) if job_id else "")
        if not (title and company and url):
            continue
        if url.startswith("/"):
            url = "https://my.jobstreet.com" + url
        types = " ".join(str(t) for t in (d.get("workTypes") or []) if isinstance(t, str))
        out.append(Candidate(title=title, company=company, url=base.normalize_url(url), location=place,
                             snippet=(types + " " + desc).strip(), source="JobStreet"))
    return out


class JobStreet(KeywordSource):
    key, name = "jobstreet", "JobStreet"

    async def search(self, keyword: str, places: list[str], limit: int, interns_only: bool) -> list[Candidate]:
        params = {"siteKey": "MY-Main", "sourcesystem": "houston", "page": 1, "pageSize": min(limit, 40), "locale": "en-MY",
                  "keywords": keyword, "where": (places or ["Malaysia"])[0]}
        try:
            data = (await base.get(API, params=params)).json()
        except ValueError:
            raise base.SourceError("it answered in a way I couldn't read")
        found = parse(data)
        seen: dict[str, Candidate] = {}
        for c in found:
            seen.setdefault(c.url, c)
        return list(seen.values())[:limit]
