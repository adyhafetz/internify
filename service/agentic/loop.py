from __future__ import annotations

from service import config, events, matching
from service.models import JobPosting, FactBulletWithScore, GeneratedResume, CriticFeedback, ResumeSkeleton, LoopResult
from service.agentic.generator import generate_resume
from service.agentic.critic import AI_WEIGHT, critique, keep_to_facts


def _quality_label(score: float) -> str:
    """Converts a numeric ATS score into a human-readable quality label."""
    if score >= 85:
        return "Excellent"
    if score >= 70:
        return "Good"
    if score >= 55:
        return "Fair"
    return "Weak"


def _settle(resume: GeneratedResume, feedback: CriticFeedback, job: JobPosting, facts: list[FactBulletWithScore],
            skeleton: ResumeSkeleton) -> CriticFeedback:
    """Make the chosen draft safe to send: any bullet that still adds something goes back to its fact's own words.
    The score is then worked out again for what is actually sent."""
    fixed = keep_to_facts(resume, facts)
    if not feedback.unsupported_claims and not fixed:
        return feedback
    covered, missing = matching.coverage_of_resume(job, resume, skeleton)
    coverage = matching.coverage_percent(covered, missing)
    events.emit("critic", events.PROGRESS,
                f"Put {len(fixed)} bullet(s) back to your own words" if fixed else "Removed bullets that cited the wrong place",
                kept_to_facts=fixed)
    return feedback.model_copy(update={
        "score": round(AI_WEIGHT * feedback.ai_score + (1 - AI_WEIGHT) * coverage, 1),
        "unsupported_claims": [], "coverage": round(coverage, 1), "covered": covered, "missing": missing,
        "reworded": fixed,
    })


def run_generator_critic_loop(
    job: JobPosting,
    candidate_facts: list[FactBulletWithScore],
    skeleton: ResumeSkeleton,
) -> LoopResult:
    """Scribe writes, Critic checks, up to MAX_GENERATOR_ITERATIONS times. Stops early once the score clears the bar
    or a rewrite stops helping, and keeps the best draft (not simply the last one)."""
    feedback: CriticFeedback | None = None
    best: tuple[GeneratedResume, CriticFeedback] | None = None

    for iteration in range(1, config.MAX_GENERATOR_ITERATIONS + 1):
        events.emit(
            "scribe", events.START,
            f"Draft {iteration}: writing bullets" if feedback is None
            else f"Draft {iteration}: revising from Critic's notes",
            iteration=iteration,
        )
        resume = generate_resume(
            job=job,
            candidate_facts=candidate_facts,
            skeleton=skeleton,
            previous_feedback=feedback,
        )
        events.emit(
            "scribe", events.DONE, f"Draft {iteration} ready: {len(resume.bullets)} bullets",
            iteration=iteration,
            bullets=[{"fact_id": b.fact_id, "entry_id": b.entry_id, "text": b.text} for b in resume.bullets],
        )
        events.emit("scribe", events.HANDOFF, f"Draft {iteration} for review", to="critic", iteration=iteration)
        events.emit("critic", events.START, "Checking every bullet against your facts and scoring the fit",
                    iteration=iteration)
        previous = feedback
        feedback = critique(resume=resume, job=job, facts=candidate_facts, skeleton=skeleton)
        events.emit(
            "critic", events.DONE,
            f"Draft {iteration} scored {feedback.score:.0f}/100"
            + (f", {len(feedback.unsupported_claims)} unsupported claim(s) caught" if feedback.unsupported_claims else ", all claims verified"),
            iteration=iteration, score=feedback.score,
            unsupported=feedback.unsupported_claims,
            notes=feedback.revision_notes, coverage_notes=feedback.keyword_coverage_notes,
            threshold=config.SCORE_THRESHOLD, ai_score=feedback.ai_score, coverage=feedback.coverage,
            covered=feedback.covered, missing=feedback.missing,
        )
        if best is None or feedback.score > best[1].score:
            best = (resume, feedback)

        if feedback.score >= config.SCORE_THRESHOLD:
            break
        # A rewrite that fixed nothing and covers no more of the job won't be helped by another one.
        if previous is not None and not feedback.unsupported_claims and feedback.coverage <= previous.coverage:
            break
        if iteration < config.MAX_GENERATOR_ITERATIONS:
            events.emit("critic", events.HANDOFF, feedback.revision_notes[:200], to="scribe", iteration=iteration)

    resume, chosen = best
    chosen = _settle(resume, chosen, job, candidate_facts, skeleton)
    return LoopResult(
        resume=resume,
        feedback=chosen,
        iterations_used=iteration,
        status=_quality_label(chosen.score),
    )
