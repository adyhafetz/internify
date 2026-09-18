from __future__ import annotations
import numpy as np

from service import config
from service.models import FactBullet, FactBulletWithScore, JobPosting
from service.fact_store.store import get_fact_embeddings, embed_query


def _cosine(a: list[float], b: list[float]) -> float:
    a_arr, b_arr = np.array(a), np.array(b)
    denom = np.linalg.norm(a_arr) * np.linalg.norm(b_arr)
    if denom == 0:
        return 0.0
    return float(np.dot(a_arr, b_arr) / denom)


def _keyword_boost(fact: FactBullet, job: JobPosting) -> float:
    job_terms = {t.lower() for t in (job.core_tech_stack + job.ats_keywords + job.hard_requirements)}
    fact_terms = {t.lower() for t in fact.tech_tags}
    overlap = job_terms & fact_terms
    # small, capped boost so keyword overlap nudges ranking without dominating semantic similarity
    return min(len(overlap) * 0.05, 0.2)


def retrieve_relevant_facts(
    job: JobPosting,
    facts: list[FactBullet],
    top_k: int = config.TOP_K_FACTS,
) -> list[FactBulletWithScore]:
    query_text = (
        f"{job.role} at {job.company}. "
        f"Tech stack: {', '.join(job.core_tech_stack)}. "
        f"Requirements: {', '.join(job.hard_requirements)}."
    )
    query_embedding = embed_query(query_text)
    fact_embeddings = get_fact_embeddings(facts)

    scored: list[FactBulletWithScore] = []
    for fact in facts:
        embedding = fact_embeddings.get(fact.id)
        similarity = _cosine(query_embedding, embedding) if embedding else 0.0
        boost = _keyword_boost(fact, job)
        scored.append(FactBulletWithScore(fact=fact, similarity=similarity, keyword_boost=boost))

    scored.sort(key=lambda x: x.total_score, reverse=True)
    return scored[:top_k]
