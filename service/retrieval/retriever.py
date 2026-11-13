from __future__ import annotations
import numpy as np

from service import config, outcomes, tools_catalog
from service.models import FactBullet, FactBulletWithScore, JobPosting, ResumeSkeleton
from service.fact_store.store import get_fact_embeddings, embed_query


def _cosine(a: list[float], b: list[float]) -> float:
    a_arr, b_arr = np.array(a), np.array(b)
    denom = np.linalg.norm(a_arr) * np.linalg.norm(b_arr)
    if denom == 0:
        return 0.0
    return float(np.dot(a_arr, b_arr) / denom)


def _key(term: str) -> str:
    return tools_catalog.key(tools_catalog.canonical(term) or term)


def _keyword_boost(fact: FactBullet, job: JobPosting) -> float:
    """A small, capped nudge for each of the job's tools or keywords this bullet names (by any of its names), so keyword
    overlap helps the ranking without outweighing meaning."""
    job_terms = {_key(t) for t in (job.core_tech_stack + job.ats_keywords + job.hard_requirements) if t.strip()}
    fact_terms = {_key(t) for t in fact.tech_tags if t.strip()}
    hits = len(job_terms & fact_terms)
    hits += sum(1 for t in job.core_tech_stack if _key(t) not in fact_terms and tools_catalog.canonical(t)
                and tools_catalog.mentions(t, fact.text))
    return min(hits * 0.05, 0.2)


def retrieve_relevant_facts(
    job: JobPosting,
    facts: list[FactBullet],
    top_k: int = config.TOP_K_FACTS,
    skeleton: ResumeSkeleton | None = None,
    keep_entries: list[str] | None = None,
) -> list[FactBulletWithScore]:
    """The bullet points to tailor from: the best ``top_k`` across your whole profile, plus the best
    ``MIN_FACTS_PER_JOB`` of every job or internship you have (and of any entry you asked to keep), so a real job
    never drops off the resume just because its bullets rank lower than your projects."""
    query_text = (
        f"{job.role} at {job.company}. "
        f"Tech stack: {', '.join(job.core_tech_stack)}. "
        f"Requirements: {', '.join(job.hard_requirements)}."
    )
    query_embedding = embed_query(query_text)
    fact_embeddings = get_fact_embeddings(facts)
    learned = outcomes.boosts()

    scored: list[FactBulletWithScore] = []
    for fact in facts:
        embedding = fact_embeddings.get(fact.id)
        similarity = _cosine(query_embedding, embedding) if embedding else 0.0
        boost = _keyword_boost(fact, job) + learned.get(fact.id, 0.0)
        scored.append(FactBulletWithScore(fact=fact, similarity=similarity, keyword_boost=boost))

    scored.sort(key=lambda x: x.total_score, reverse=True)
    chosen = scored[:top_k]
    guaranteed = set(keep_entries or [])
    per_entry = {e: config.MIN_FACTS_PER_JOB for e in guaranteed}
    if skeleton is not None:
        for e in skeleton.experience_entries:
            per_entry.setdefault(e.id, config.MIN_FACTS_PER_JOB)
    else:   # no skeleton given: every entry with an experience bullet counts as a job
        for f in facts:
            if f.section == "experience":
                per_entry.setdefault(f.entry_id, config.MIN_FACTS_PER_JOB)
    have = {}
    for f in chosen:
        have[f.fact.entry_id] = have.get(f.fact.entry_id, 0) + 1
    ids = {f.fact.id for f in chosen}
    for f in scored:   # best first
        need = per_entry.get(f.fact.entry_id, 0)
        if f.fact.id not in ids and have.get(f.fact.entry_id, 0) < need:
            chosen.append(f)
            ids.add(f.fact.id)
            have[f.fact.entry_id] = have.get(f.fact.entry_id, 0) + 1
    chosen.sort(key=lambda x: x.total_score, reverse=True)
    return chosen
