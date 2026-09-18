from __future__ import annotations

from service import config
from service.clients import instructor_client
from service.models import (
    JobPosting,
    FactBulletWithScore,
    GeneratedResume,
    CriticFeedback,
    ResumeSkeleton,
)

GENERATOR_SYSTEM_PROMPT = f"""You tailor resume bullets, relevant coursework, and skills to a specific job posting.

BULLET RULES:
- Every bullet you produce MUST set fact_id to one of the provided fact ids, and
  entry_id must exactly match that fact's entry_id.
- You may reword, reorder, and recalibrate emphasis/verb choice to match the job's
  language, but you must NOT invent metrics, technologies, or claims that are not
  present in that fact's original text.
- Do not fabricate new bullets with no matching fact_id.
- Never move a bullet under an entry_id other than the one its fact belongs to.
- Produce at most {config.MAX_BULLETS_PER_ENTRY} bullets per entry_id. If an entry
  has fewer available facts than that, use all of them.
- Prefer facts whose tech_tags overlap with the job's core_tech_stack, hard_requirements,
  and ats_keywords, but every selected bullet still needs a real supporting fact.
- Cover every entry_id that has at least one available fact - do not skip an entire
  entry unless it truly has zero relevant facts.
- If revision notes are provided from a previous critique, address them directly.

COURSE SELECTION RULES (selected_courses):
- For each education entry id, pick 3 to 5 courses from its master list that best match
  the job's tech stack and requirements.
- If no courses are a strong match, still include the 3 most broadly applicable ones.
- Use the exact course name strings as provided — do not paraphrase or invent names.

SKILL SELECTION RULES (selected_skills):
- Select a relevant subset from the master skills list.
- Only include a category if at least one item in it is relevant to this job.
- Within each included category, trim to items that are relevant — you do not have to
  include the full category list.
- Use exact category and item name strings as provided — do not paraphrase or invent."""


def _entries_block(skeleton: ResumeSkeleton, facts_by_entry: dict[str, list[FactBulletWithScore]]) -> str:
    lines = []
    for exp in skeleton.experience_entries:
        lines.append(f"\nEntry id={exp.id} ({exp.title} at {exp.company}):")
        for f in facts_by_entry.get(exp.id, []):
            lines.append(f"  - fact_id={f.fact.id} | tags={f.fact.tech_tags} | text: {f.fact.text}")
    for proj in skeleton.project_entries:
        lines.append(f"\nEntry id={proj.id} (Project: {proj.name}, {proj.tech_stack}):")
        for f in facts_by_entry.get(proj.id, []):
            lines.append(f"  - fact_id={f.fact.id} | tags={f.fact.tech_tags} | text: {f.fact.text}")
    return "\n".join(lines)


def _education_courses_block(skeleton: ResumeSkeleton) -> str:
    lines = ["\nAvailable education courses (use entry id as key in selected_courses):"]
    for edu in skeleton.education:
        lines.append(f"  id={edu.id} ({edu.degree_line}): {', '.join(edu.courses)}")
    return "\n".join(lines)


def _master_skills_block(skeleton: ResumeSkeleton) -> str:
    lines = ["\nMaster skills list (use exact strings in selected_skills):"]
    for category, items in skeleton.skills.items():
        lines.append(f"  {category}: {', '.join(items)}")
    return "\n".join(lines)


def generate_resume(
    job: JobPosting,
    candidate_facts: list[FactBulletWithScore],
    skeleton: ResumeSkeleton,
    previous_feedback: CriticFeedback | None = None,
) -> GeneratedResume:
    facts_by_entry: dict[str, list[FactBulletWithScore]] = {}
    for f in candidate_facts:
        facts_by_entry.setdefault(f.fact.entry_id, []).append(f)

    user_content = (
        job.format_context()
        + f"Available entries and their facts (only these ids may be used):"
        + f"{_entries_block(skeleton, facts_by_entry)}\n"
        + _education_courses_block(skeleton)
        + "\n"
        + _master_skills_block(skeleton)
        + "\n"
    )

    if previous_feedback:
        user_content += (
            f"\nPrevious critique score: {previous_feedback.score}/100\n"
            f"Revision notes to address: {previous_feedback.revision_notes}\n"
            f"Unsupported claims to remove or fix: {previous_feedback.unsupported_claims}\n"
        )

    return instructor_client.chat.completions.create(
        model=config.GENERATOR_MODEL,
        response_model=GeneratedResume,
        messages=[
            {"role": "system", "content": GENERATOR_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
    )
