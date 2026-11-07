"""The Matcher: picks the jobs worth tailoring a resume for.

Stage 1 (cheap): every candidate's title is compared, by meaning, with what you tend to go for: the jobs you sent
recently and the roles on your own resume. Only the best few go on.
Stage 2 (careful): those are read in full and checked against your real saved experience: how many of the job's
tools and requirements your bullet points actually cover. The final fit mixes the two.

Nothing here writes a resume; it only decides which jobs get one."""
from __future__ import annotations

import asyncio
import logging
import math
import re
from dataclasses import dataclass, field
from typing import Optional

from bot.services import ai, db
from bot.services.discovery import Candidate
from bot.services.scraper import scrape_job_url
from service import config as service_config
from service import events

logger = logging.getLogger(__name__)

STAGE1_MIN, STAGE1_MAX = 6, 8     # how many titles are read in full, at most
COVERAGE_WEIGHT, TITLE_WEIGHT = 0.65, 0.35


@dataclass
class Scored:
    cand: Candidate
    title_sim: float
    fit: float = 0.0
    role: str = ""
    company: str = ""
    covered: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    why: str = ""


@dataclass
class MatchResult:
    kept: list[Scored]
    total: int
    considered: int          # how many were read in full
    top: Optional[float]     # the best fit found (even if below the bar)
    scored: list[Scored] = field(default_factory=list)   # everything that was read in full


# ---------------------------------------------------------------- stage 1: titles by meaning

def _cosine(a: list[float], b: list[float]) -> float:
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b)) / (na * nb) if na and nb else 0.0


def _embed(texts: list[str]) -> list[list[float]]:
    """Blocking. Embeddings for many texts in as few calls as possible."""
    from service.clients import openai_client
    out: list[list[float]] = []
    for i in range(0, len(texts), 96):
        resp = openai_client.embeddings.create(model=service_config.EMBEDDING_MODEL, input=texts[i:i + 96])
        out += [d.embedding for d in resp.data]
    return out


def taste_texts() -> list[str]:
    """What you go for: the jobs you sent lately, plus the roles and projects on your resume."""
    texts = db.recent_job_texts(15)
    try:
        from service.fact_store.store import load_skeleton
        sk = load_skeleton()
        texts += [f"{e.title} at {e.company}" for e in sk.experience_entries]
        texts += [f"{p.name}: {p.tech_stack}" for p in sk.project_entries]
    except Exception as err:
        logger.info("No resume data for the Matcher yet: %s", err)
    return [t for t in texts if t.strip()]


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9+#.]{3,}", text.lower())}


def _title_scores(cands: list[Candidate], taste: list[str]) -> list[float]:
    """How close each candidate's title is to what you go for (0 to 1). Falls back to shared words if the
    embedding service is unavailable, so a scan still works."""
    if not taste:
        return [0.0] * len(cands)
    try:
        vecs = _embed(taste + [f"{c.title}. {c.location}. {c.snippet[:200]}" for c in cands])
        tv, cv = vecs[:len(taste)], vecs[len(taste):]
        return [sum(sorted((_cosine(c, t) for t in tv), reverse=True)[:3]) / min(3, len(tv)) for c in cv]
    except Exception as err:
        logger.warning("Embedding failed, matching titles by shared words instead: %s", err)
        tw = [_words(t) for t in taste]
        return [max((len(_words(c.title) & w) / max(1, len(_words(c.title))) for w in tw), default=0.0) for c in cands]


# ---------------------------------------------------------------- stage 2: read it and check it against you

async def _read(cand: Candidate) -> Optional[str]:
    """The posting's text. APIs already gave us the description; for other pages the Scout reads it (quietly)."""
    if len(cand.snippet) >= 400:
        return f"{cand.title}\n{cand.company}\n{cand.location}\n{cand.snippet}"
    with events.muted():
        page = await scrape_job_url(cand.url)
    return page.text if page else None


def _analyse(raw_text: str) -> tuple[str, str, list[str], list[str]]:
    """Blocking. (role, company, covered, missing): which of the job's tools and requirements your bullets cover."""
    from service.extraction.extractor import extract_job_posting
    from service.fact_store.store import load_facts
    from service.pipeline import _coverage
    from service.retrieval.retriever import retrieve_relevant_facts
    job = extract_job_posting(raw_text)
    covered, missing = _coverage(job, retrieve_relevant_facts(job, load_facts()))
    return job.role, job.company, covered, missing


def _why(covered: list[str], missing: list[str]) -> str:
    text = f"Matches {', '.join(covered[:4])}." if covered else "Little overlap with your saved experience."
    return text + (f" Missing: {', '.join(missing[:2])}." if missing else "")


async def match(cands: list[Candidate], max_keep: int, min_fit: float) -> MatchResult:
    """Score the candidates and keep the best ``max_keep`` that clear ``min_fit``."""
    if not cands:
        return MatchResult([], 0, 0, None)
    taste = await asyncio.to_thread(taste_texts)
    sims = await asyncio.to_thread(_title_scores, cands, taste)
    ranked = sorted((Scored(c, s) for c, s in zip(cands, sims)), key=lambda x: x.title_sim, reverse=True)
    shortlist = ranked[:max(STAGE1_MIN, min(STAGE1_MAX, 2 * max_keep))]
    events.emit("matcher", events.PROGRESS, "Reading the best in detail", reading=len(shortlist))

    scored: list[Scored] = []
    for item in shortlist:
        try:
            text = await _read(item.cand)
            if not text:
                continue
            role, company, covered, missing = await asyncio.to_thread(_analyse, text)
        except Exception as err:   # an unreadable or odd posting is skipped, never fatal
            logger.info("Matcher skipped %s: %s", item.cand.url, err)
            continue
        total = len(covered) + len(missing)
        coverage = len(covered) / total if total else 0.5
        norm = min(1.0, max(0.0, (item.title_sim - 0.15) / 0.45))
        item.fit = COVERAGE_WEIGHT * coverage + TITLE_WEIGHT * norm
        item.role, item.company = role or item.cand.title, company or item.cand.company
        item.covered, item.missing, item.why = covered, missing, _why(covered, missing)
        scored.append(item)

    scored.sort(key=lambda x: x.fit, reverse=True)
    kept = [x for x in scored if x.fit >= min_fit][:max_keep]
    for item in kept:   # a plain-language sentence for each pick (the template reason stays if the AI is off)
        item.why = await ai.explain_match(item.role, item.company, item.covered, item.missing, item.why)
    return MatchResult(kept, len(cands), len(shortlist), scored[0].fit if scored else None, scored)
