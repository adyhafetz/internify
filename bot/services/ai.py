"""Small AI helpers for the places where plain rules are weak: telling a real job posting from a login page,
reading an unfamiliar career page, judging whether two pages are the same job, and explaining a match in one sentence.

Every helper fails open: if the AI call errors or AI_HELPERS is off, the caller carries on with its plain rules,
so a flaky model can never stop a job."""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional
from urllib.parse import urljoin, urldefrag

from pydantic import BaseModel, Field

from bot import config
from service import config as service_config

logger = logging.getLogger(__name__)


def enabled() -> bool:
    return bool(config.AI_HELPERS and service_config.OPENAI_API_KEY)


def _ask(model: str, response_model, system: str, user: str):
    """Blocking. One structured call. Tests replace this."""
    from service.clients import instructor_client
    return instructor_client.chat.completions.create(
        model=model, response_model=response_model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])


async def _run(model: str, response_model, system: str, user: str):
    return await asyncio.to_thread(_ask, model, response_model, system, user)


# ---------------------------------------------------------------- Scout: is this really a job posting?

class _PostingCheck(BaseModel):
    is_job_posting: bool = Field(description="True only if the text is the description of ONE specific job")
    what_it_is: str = Field(description="If not a job posting, what it looks like, in 6 words or fewer (e.g. 'a login page')")


async def looks_like_posting(text: str) -> tuple[bool, str]:
    """(True, "") when the text is one real job posting. Fails open (True) if the AI is unavailable."""
    if not enabled():
        return True, ""
    try:
        r: _PostingCheck = await _run(
            service_config.SCOUT_MODEL, _PostingCheck,
            "You check text a web scraper fetched. Say whether it is the description of one specific job posting. "
            "A login or cookie page, a captcha, an error page, a list of many jobs or a company homepage is NOT a job posting.",
            text[:3500])
        return r.is_job_posting, ("" if r.is_job_posting else r.what_it_is.strip() or "something else")
    except Exception as err:
        logger.info("Posting check skipped: %s", err)
        return True, ""


class _SameJob(BaseModel):
    same_job: bool = Field(description="True if the candidate is the same job (same role at the same company) as the wanted one")


async def is_same_job(wanted: str, candidate_text: str) -> bool:
    """Used when the plain word-overlap test could not decide. Fails closed (False): a wrong job is worse than none."""
    if not enabled():
        return False
    try:
        r: _SameJob = await _run(service_config.SCOUT_MODEL, _SameJob,
                                 "Decide if the candidate page is the posting for the same job (same role, same company) as the wanted one.",
                                 f"Wanted job (from a link): {wanted}\n\nCandidate page:\n{candidate_text[:3000]}")
        return r.same_job
    except Exception as err:
        logger.info("Same-job check skipped: %s", err)
        return False


# ---------------------------------------------------------------- Discoverer: read a career page

class _FoundJob(BaseModel):
    title: str
    url: str = Field(description="The link exactly as written on the page")


class _FoundJobs(BaseModel):
    jobs: list[_FoundJob] = Field(default_factory=list)


async def find_jobs_on_page(page_text: str, base_url: str) -> Optional[list[tuple[str, str]]]:
    """(title, url) for each individual job on a listing page, or None if the AI is unavailable (use the rules).
    Links the AI invents are dropped: a link counts only if it really appears in the page."""
    if not enabled():
        return None
    try:
        r: _FoundJobs = await _run(
            service_config.DISCOVERER_MODEL, _FoundJobs,
            "You read a company's careers page. List every individual job opening on it. Skip navigation, "
            "'about', 'apply later', filters, social links and general pages. Copy each link exactly as it appears; never invent one.",
            page_text[:14000])
    except Exception as err:
        logger.info("AI page reading skipped: %s", err)
        return None
    out, seen = [], set()
    for j in r.jobs:
        href = j.url.strip()
        if not href or href not in page_text:
            continue
        url = urldefrag(urljoin(base_url, href))[0]
        if url in seen or url.rstrip("/") == base_url.rstrip("/"):
            continue
        seen.add(url)
        out.append((re.sub(r"\s+", " ", j.title).strip()[:140], url))
    return out


# ---------------------------------------------------------------- Matcher: say why in one sentence

class _Why(BaseModel):
    sentence: str = Field(description="One plain sentence, 25 words or fewer, no jargon")


async def explain_match(role: str, company: str, covered: list[str], missing: list[str], fallback: str) -> str:
    if not enabled():
        return fallback
    try:
        r: _Why = await _run(
            service_config.MATCHER_MODEL, _Why,
            "In one friendly plain sentence tell a student why this job does or doesn't suit them. Use only the facts given. "
            "No jargon, no percentages.",
            f"Job: {role} at {company}\nTools and skills the student already has experience with: {', '.join(covered) or 'none'}\n"
            f"Wanted but not in their experience: {', '.join(missing) or 'nothing'}")
        return r.sentence.strip() or fallback
    except Exception as err:
        logger.info("Match explanation skipped: %s", err)
        return fallback
