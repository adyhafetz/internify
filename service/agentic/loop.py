from __future__ import annotations

from service import config, events
from service.models import JobPosting, FactBulletWithScore, GeneratedResume, CriticFeedback, ResumeSkeleton, LoopResult
from service.agentic.generator import generate_resume
from service.agentic.critic import critique


def _quality_label(score: float) -> str:
    """Converts a numeric ATS score into a human-readable quality label."""
    if score >= 85:
        return "Excellent"
    if score >= 70:
        return "Good"
    if score >= 55:
        return "Fair"
    return "Weak"


def run_generator_critic_loop(
    job: JobPosting,
    candidate_facts: list[FactBulletWithScore],
    skeleton: ResumeSkeleton,
) -> LoopResult:
    feedback: CriticFeedback | None = None
    resume: GeneratedResume | None = None

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
        events.emit("critic", events.START, "Checking every claim against your facts and scoring ATS fit",
                    iteration=iteration)
        feedback = critique(resume=resume, job=job, facts=candidate_facts)
        events.emit(
            "critic", events.DONE,
            f"Draft {iteration} scored {feedback.score:.0f}/100"
            + (f", {len(feedback.unsupported_claims)} unsupported claim(s) caught" if feedback.unsupported_claims else ", all claims verified"),
            iteration=iteration, score=feedback.score,
            unsupported=feedback.unsupported_claims,
            notes=feedback.revision_notes, coverage_notes=feedback.keyword_coverage_notes,
            threshold=config.SCORE_THRESHOLD,
        )

        # Early exit once the score clears the threshold — no point in more iterations.
        if feedback.score >= config.SCORE_THRESHOLD:
            break
        if iteration < config.MAX_GENERATOR_ITERATIONS:
            events.emit("critic", events.HANDOFF, feedback.revision_notes[:200], to="scribe", iteration=iteration)

    return LoopResult(
        resume=resume,
        feedback=feedback,
        iterations_used=iteration,
        status=_quality_label(feedback.score),
    )
