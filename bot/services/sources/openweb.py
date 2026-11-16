"""The open web: any page, not a fixed list of sites. The keywords are searched on Bing (then DuckDuckGo) with no site
restriction, and the AI picks out the results that are single job postings (plain rules if the AI is off). This is
how company career sites and smaller boards, where many Malaysian internships are posted, are found."""
from __future__ import annotations

import re
from urllib.parse import urlparse

from bot.services import ai, websearch
from bot.services.sources import base
from bot.services.sources.base import Candidate, KeywordSource

# Sites that are never a single job posting we can use
SKIP_HOSTS = ("youtube.", "facebook.", "instagram.", "twitter.", "x.com", "tiktok.", "reddit.", "wikipedia.", "quora.",
              "medium.com", "glassdoor.", "indeed.", "pinterest.", "t.me", "telegram.", "carousell.", "scribd.", "github.com")
JOB_PATH = re.compile(r"/(jobs?|careers?|positions?|openings?|vacanc\w*|postings?|roles?|apply|opportunit\w*|job-detail)(/|$|\?|-)|gh_jid=|/j/|/o/", re.I)
ROLE_WORDS = ("intern", "internship", "trainee", "industrial training", "latihan industri", "pelatih", "graduate", "engineer", "developer", "analyst")


def rules(results: list[tuple[str, str]]) -> list[int]:
    """Without the AI: results whose link looks like a job page and whose title names a role."""
    keep = []
    for i, (title, url) in enumerate(results):
        host = urlparse(url).netloc.lower()
        if any(h in host for h in SKIP_HOSTS):
            continue
        if JOB_PATH.search(urlparse(url).path + ("?" + urlparse(url).query if urlparse(url).query else "")) and \
                any(w in title.lower() for w in ROLE_WORDS):
            keep.append(i)
    return keep


class OpenWeb(KeywordSource):
    key, name = "openweb", "Web search"

    async def search(self, keyword: str, places: list[str], limit: int, interns_only: bool) -> list[Candidate]:
        place = (places or ["Malaysia"])[0]
        query = f'"{keyword}" {place}' + (" internship" if interns_only and "intern" not in keyword.lower() else "")
        results = [(t, base.normalize_url(u)) for t, u in await websearch.search(query)]
        results = [(t, u) for t, u in results if not any(h in urlparse(u).netloc.lower() for h in SKIP_HOSTS)]
        if not results:
            return []
        picked = await ai.pick_postings(results[:30], keyword, place)
        if picked is None:
            picked = rules(results)
        out = []
        for i in picked:
            if 0 <= i < len(results):
                title, url = results[i]
                out.append(Candidate(title=websearch._clean_title(title) or title, company="", url=url, source="Web search"))
        return out[:limit]
