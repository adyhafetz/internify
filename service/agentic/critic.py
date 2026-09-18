from __future__ import annotations

from pydantic import BaseModel, Field

from service import config
from service.clients import instructor_client
from service.models import JobPosting, FactBulletWithScore, GeneratedResume, CriticFeedback


class _LLMCriticScore(BaseModel):
    ats_score: float = Field(..., ge=0, le=100, description="Keyword/requirement match score")
    keyword_coverage_notes: str
    overflow_risk: bool = Field(..., description="True if content looks too long for one page")
    revision_notes: str


CRITIC_SYSTEM_PROMPT = """You are an ATS and resume-fit critic. Score how well the
provided resume bullets match the job posting's core tech stack, hard requirements,
and ATS keywords. Also flag if the bullet count/length looks likely to overflow a
single page (rough guideline: more than ~14 bullets total is risky). Be specific
and actionable in revision_notes so a rewrite can act on it directly."""


def _find_unsupported_claims(
    resume: GeneratedResume,
    facts: list[FactBulletWithScore],
) -> list[str]:
    """Code-level grounding check: every bullet's fact_id must exist, AND its
    entry_id must match that fact's real entry_id. This does not require an
    LLM call and cannot be talked out of flagging a violation."""
    facts_by_id = {f.fact.id: f.fact for f in facts}
    problems = []
    for bullet in resume.bullets:
        fact = facts_by_id.get(bullet.fact_id)
        if fact is None:
            problems.append(f"Bullet references unknown fact_id '{bullet.fact_id}': \"{bullet.text}\"")
        elif fact.entry_id != bullet.entry_id:
            problems.append(
                f"Bullet fact_id '{bullet.fact_id}' belongs under entry '{fact.entry_id}' "
                f"but was placed under '{bullet.entry_id}': \"{bullet.text}\""
            )
    return problems


def critique(
    resume: GeneratedResume,
    job: JobPosting,
    facts: list[FactBulletWithScore],
) -> CriticFeedback:
    unsupported = _find_unsupported_claims(resume, facts)

    bullets_text = "\n".join(f"- [{b.entry_id}] {b.text}" for b in resume.bullets)
    user_content = job.format_context() + f"Resume bullets:\n{bullets_text}"

    llm_result: _LLMCriticScore = instructor_client.chat.completions.create(
        model=config.CRITIC_MODEL,
        response_model=_LLMCriticScore,
        messages=[
            {"role": "system", "content": CRITIC_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
    )

    # Any unsupported claim caps the score hard, regardless of what the LLM scored,
    # since a hallucinated bullet is disqualifying no matter how well it matches keywords.
    final_score = llm_result.ats_score
    if unsupported:
        final_score = min(final_score, 40.0)

    return CriticFeedback(
        score=final_score,
        keyword_coverage_notes=llm_result.keyword_coverage_notes,
        unsupported_claims=unsupported,
        overflow_risk=llm_result.overflow_risk,
        revision_notes=llm_result.revision_notes,
    )
