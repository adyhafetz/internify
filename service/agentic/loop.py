from __future__ import annotations

from service import config
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
        resume = generate_resume(
            job=job,
            candidate_facts=candidate_facts,
            skeleton=skeleton,
            previous_feedback=feedback,
        )
        feedback = critique(resume=resume, job=job, facts=candidate_facts)

        # Early exit once the score clears the threshold — no point in more iterations.
        if feedback.score >= config.SCORE_THRESHOLD:
            break

    return LoopResult(
        resume=resume,
        feedback=feedback,
        iterations_used=iteration,
        status=_quality_label(feedback.score),
    )
