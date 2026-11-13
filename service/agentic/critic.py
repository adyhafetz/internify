from __future__ import annotations

from pydantic import BaseModel, Field

from service import config, matching
from service.clients import instructor_client
from service.latex.fit import new_claims
from service.models import JobPosting, FactBulletWithScore, GeneratedResume, CriticFeedback, ResumeSkeleton


class _LLMCriticScore(BaseModel):
    ats_score: float = Field(..., ge=0, le=100, description="Keyword/requirement match score")
    keyword_coverage_notes: str
    overflow_risk: bool = Field(..., description="True if content looks too long for one page")
    revision_notes: str


CRITIC_SYSTEM_PROMPT = """You are an ATS and resume-fit critic. Score how well the
provided resume bullets match the job posting's core tech stack, hard requirements,
and ATS keywords. Also flag if the bullet count/length looks likely to overflow a
single page (rough guideline: more than ~14 bullets total is risky). Be specific
and actionable in revision_notes so a rewrite can act on it directly. Never suggest
adding a tool, number or claim the bullets don't already support. Score the whole page:
education, courses, skills and leadership are on it too, so never ask for an education
section, a degree line or a skill that is already there. Revision notes are only about
the bullets (the only part that is rewritten)."""

def _rest_of_resume(resume: GeneratedResume, sk: ResumeSkeleton) -> str:
    """Education, courses, honors, skills and leadership as they will appear on the page (fixed, not rewritten)."""
    lines = ["The rest of the same one-page resume (already on it, not rewritten by the Scribe):"]
    for e in sk.education:
        courses = resume.selected_courses.get(e.id) or e.courses[:5]
        lines.append(f"Education: {e.degree_line}, {e.institution} ({e.dates})"
                     + (f". Courses: {', '.join(courses)}" if courses else "")
                     + (f". {' '.join(e.honor_bullets)}" if e.honor_bullets else ""))
    skills = resume.selected_skills if any(resume.selected_skills.values()) else sk.skills
    for cat, items in skills.items():
        if items:
            lines.append(f"Skills, {cat}: {', '.join(items)}")
    for lead in sk.leadership:
        lines.append(f"Leadership: {lead.title}, {lead.org}" + (f": {' '.join(lead.bullets)}" if lead.bullets else ""))
    return "\n".join(lines)


AI_WEIGHT = 0.5   # the shown score: half the AI reviewer's opinion, half the requirements the resume visibly covers


def _find_unsupported_claims(
    resume: GeneratedResume,
    facts: list[FactBulletWithScore],
) -> list[str]:
    """Code-level grounding check (no AI, can't be talked out of it). Every bullet must cite a real fact id under that
    fact's own entry, AND its wording may not add a number, tool or name the fact doesn't have."""
    problems, _ = _grounding(resume, facts)
    return problems


def _grounding(resume: GeneratedResume, facts: list[FactBulletWithScore]) -> tuple[list[str], list[str]]:
    """(problems, fact ids whose wording added something)."""
    facts_by_id = {f.fact.id: f.fact for f in facts}
    problems, reworded = [], []
    for bullet in resume.bullets:
        fact = facts_by_id.get(bullet.fact_id)
        if fact is None:
            problems.append(f"Bullet references unknown fact_id '{bullet.fact_id}': \"{bullet.text}\"")
        elif fact.entry_id != bullet.entry_id:
            problems.append(
                f"Bullet fact_id '{bullet.fact_id}' belongs under entry '{fact.entry_id}' "
                f"but was placed under '{bullet.entry_id}': \"{bullet.text}\""
            )
        else:
            added = new_claims(fact.text + " " + " ".join(fact.tech_tags), bullet.text)
            if added:
                problems.append(
                    f"Bullet for fact_id '{bullet.fact_id}' adds {', '.join(added)}, which its fact doesn't say. "
                    f"Use only what the fact says: \"{fact.text}\""
                )
                reworded.append(bullet.fact_id)
    return problems, reworded


def keep_to_facts(resume: GeneratedResume, facts: list[FactBulletWithScore]) -> list[str]:
    """Last line of defence: any bullet that still adds something goes back to the fact's own words, and bullets that
    cite an unknown fact or the wrong entry are dropped. Returns the fact ids that were put back."""
    facts_by_id = {f.fact.id: f.fact for f in facts}
    fixed, kept = [], []
    for b in resume.bullets:
        fact = facts_by_id.get(b.fact_id)
        if fact is None or fact.entry_id != b.entry_id:
            continue
        if new_claims(fact.text + " " + " ".join(fact.tech_tags), b.text):
            b.text = fact.text
            fixed.append(fact.id)
        kept.append(b)
    resume.bullets = kept
    return fixed


def critique(
    resume: GeneratedResume,
    job: JobPosting,
    facts: list[FactBulletWithScore],
    skeleton: ResumeSkeleton | None = None,
) -> CriticFeedback:
    unsupported, reworded = _grounding(resume, facts)

    bullets_text = "\n".join(f"- [{b.entry_id}] {b.text}" for b in resume.bullets)
    user_content = job.format_context() + f"Resume bullets:\n{bullets_text}"
    if skeleton is not None:   # the rest of the page, so the review doesn't ask for things that are already there
        user_content += "\n\n" + _rest_of_resume(resume, skeleton)

    llm_result: _LLMCriticScore = instructor_client.chat.completions.create(
        model=config.CRITIC_MODEL,
        response_model=_LLMCriticScore,
        messages=[
            {"role": "system", "content": CRITIC_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
    )

    covered, missing = matching.coverage_of_resume(job, resume, skeleton) if skeleton else ([], [])
    coverage = matching.coverage_percent(covered, missing) if skeleton else llm_result.ats_score
    final_score = AI_WEIGHT * llm_result.ats_score + (1 - AI_WEIGHT) * coverage
    # Any unsupported claim caps the score hard, regardless of what the LLM scored,
    # since a hallucinated bullet is disqualifying no matter how well it matches keywords.
    if unsupported:
        final_score = min(final_score, 40.0)
    notes = llm_result.revision_notes
    if missing:
        notes += (" Requirements not visible on the resume: " + ", ".join(missing[:8])
                  + ". Bring them out only where a fact really supports them.")

    return CriticFeedback(
        score=round(final_score, 1),
        keyword_coverage_notes=llm_result.keyword_coverage_notes,
        unsupported_claims=unsupported,
        overflow_risk=llm_result.overflow_risk,
        revision_notes=notes,
        ai_score=llm_result.ats_score,
        coverage=round(coverage, 1),
        covered=covered,
        missing=missing,
        reworded=reworded,
    )
