from __future__ import annotations
import logging
import os
import re
import time
from typing import Callable, Optional

from service import config
from service.models import FactBulletWithScore, ProcessRequest, ProcessResponse, SkillAnswer
from service.extraction.extractor import extract_job_posting
from service.fact_store.store import load_facts, load_skeleton
from service.retrieval.retriever import retrieve_relevant_facts
from service.agentic.loop import run_generator_critic_loop
from service.latex.compile import TexCompileError
from service.latex.fit import fit_one_page
from service.github_evidence import check_github
from service import events

logger = logging.getLogger(__name__)

# Called with the tools the job wants that the user has no saved experience for; returns the user's answers.
SkillAsker = Callable[[list[str]], list[SkillAnswer]]


class PipelineError(Exception):
    """The run could not finish. The message is plain language and is shown to the user."""



def _slugify(*parts: str) -> str:
    joined = "_".join(parts)
    return re.sub(r"[^a-zA-Z0-9_]", "", joined.replace(" ", "_"))[:60]


def _skill_items(skeleton) -> list[str]:
    return [i for items in skeleton.skills.values() for i in items]


def _coverage(job, candidate_facts, skeleton=None) -> tuple[list[str], list[str]]:
    """Which job requirements the selected bullets (or your skills list) show, matched by tool name and its other names."""
    from service.matching import coverage_of_facts
    return coverage_of_facts(job, [f.fact for f in candidate_facts], _skill_items(skeleton) if skeleton else [])


def _summary(job, facts, candidate_facts, resume, skeleton, fit_dropped: list[str], covered, missing) -> dict:
    """What went onto the resume and what didn't, in plain words, for "What changed"."""
    by_id = {f.id: f for f in facts}
    names = {e.id: f"{e.title} at {e.company}" for e in skeleton.experience_entries}
    names.update({p.id: p.name for p in skeleton.project_entries})
    picked: dict[str, list[dict]] = {}
    for b in resume.bullets:
        src = by_id.get(b.fact_id)
        picked.setdefault(b.entry_id, []).append({"text": b.text, "reworded": bool(src and src.text.strip() != b.text.strip())})
    used = set(picked)
    left_out = [{"id": eid, "name": name} for eid, name in names.items() if eid not in used and any(f.entry_id == eid for f in facts)]
    leadership_out = [d.split(": ", 1)[1] for d in fit_dropped if d.startswith("leadership: ")]
    return {
        "picked": [{"id": eid, "name": names.get(eid, eid), "bullets": bl} for eid, bl in picked.items()],
        "left_out": left_out, "leadership_out": leadership_out,
        "bullets": len(resume.bullets), "facts": len(facts), "considered": len(candidate_facts),
        "reworded": sum(1 for bl in picked.values() for b in bl if b["reworded"]),
        "covered": covered, "missing": missing, "role": job.role, "company": job.company,
    }


def _skills_to_ask_about(job, missing: list[str]) -> list[str]:
    """Concrete tools from the job's tech stack that no saved fact covers. Vague phrases such as
    'data structures' are never asked about, and the number of questions is capped."""
    missing_keys = {m.lower() for m in missing}
    gaps: list[str] = []
    for term in job.core_tech_stack:
        if term.lower() in missing_keys and term.lower() not in {g.lower() for g in gaps}:
            gaps.append(term)
    return gaps[: config.MAX_SKILL_QUESTIONS]


def _apply_answers(answers: list[SkillAnswer], candidate_facts: list[FactBulletWithScore],
                   covered: list[str], missing: list[str]):
    """Fold the user's answers into the run: new bullet points join the candidates, and tools the user
    confirmed count as covered. Returns (candidate_facts, covered, missing, new_fact_ids)."""
    new_ids: set[str] = set()
    for a in answers:
        if not a.has:
            continue
        key = a.skill.lower()
        missing = [m for m in missing if m.lower() != key]
        if key not in {c.lower() for c in covered}:
            covered = covered + [a.skill]
        if a.fact is not None:
            candidate_facts = [FactBulletWithScore(fact=a.fact, similarity=1.0, keyword_boost=0.0)] + candidate_facts
            new_ids.add(a.fact.id)
    return candidate_facts, covered, missing, new_ids


def run_pipeline(request: ProcessRequest, ask_skills: Optional[SkillAsker] = None) -> ProcessResponse:
    """The whole tailoring run. ``ask_skills`` lets the Archivist ask the user about missing tools."""
    raw_text = (
        f"Company: {request.company}\n"
        f"Job Title: {request.job_title}\n"
        f"Location: {request.location}\n\n"
        f"Job Description:\n{request.job_description}\n\n"
        f"Requirements:\n{request.job_requirements}"
    )
    events.emit("analyst", events.START, "Extracting tech stack, requirements and ATS keywords")
    try:
        job = extract_job_posting(raw_text)
    except Exception as e:
        events.emit("analyst", events.FAIL, f"Extraction failed: {e}")
        raise PipelineError(f"Couldn't understand the job posting ({e})")
    events.emit(
        "analyst", events.DONE,
        f"Found {len(job.core_tech_stack)} technologies, {len(job.hard_requirements)} requirements",
        company=job.company, role=job.role, seniority=job.seniority,
        core_tech_stack=job.core_tech_stack, hard_requirements=job.hard_requirements,
        ats_keywords=job.ats_keywords,
    )
    events.emit("analyst", events.HANDOFF, "Requirements and keywords", to="archivist")

    facts = load_facts()
    if not facts:
        events.emit("archivist", events.FAIL, "No saved experience")
        raise PipelineError("Your saved experience is empty. Send me your resume file (/importresume) or add some in /profile first.")

    skeleton = load_skeleton()
    events.emit("archivist", events.START, f"Searching {len(facts)} verified facts")
    keep = [e for e in request.keep_entries if e]
    candidate_facts = retrieve_relevant_facts(job, facts, skeleton=skeleton, keep_entries=keep)
    matched, missing = _coverage(job, candidate_facts, skeleton)

    # Tools the job wants that the user has no saved experience for: first look for proof in their public GitHub repos
    # (found tools are added with no question), then ask the user about what is still missing instead of guessing.
    new_fact_ids: set[str] = set()
    github = check_github(job, missing)
    if github.answers:
        candidate_facts, matched, missing, found_ids = _apply_answers(github.answers, candidate_facts, matched, missing)
        new_fact_ids |= found_ids
        skeleton = load_skeleton()
    open_gaps = _skills_to_ask_about(job, missing)   # also offered after delivery ("add these and rebuild")
    gaps = open_gaps if ask_skills else []
    if gaps:
        events.emit("archivist", events.ASK, f"Asking you about {', '.join(gaps)}", skills=gaps)
        try:
            answers = ask_skills(gaps)
        except Exception as e:  # a failed question must never end the run: carry on without the answers
            logger.warning("Asking about missing skills failed: %s", e)
            answers = []
        candidate_facts, matched, missing, asked_ids = _apply_answers(answers, candidate_facts, matched, missing)
        new_fact_ids |= asked_ids
        if any(a.has for a in answers):
            skeleton = load_skeleton()  # a confirmed tool may have been added to the skills list

    events.emit(
        "archivist", events.DONE,
        f"Selected {len(candidate_facts)} of {len(facts)} facts"
        + (f" ({len(new_fact_ids)} new from you)" if new_fact_ids else "")
        + f"; {len(matched)} requirements covered, {len(missing)} not found",
        total_facts=len(facts),
        facts=[
            {"id": f.fact.id, "entry_id": f.fact.entry_id, "text": f.fact.text,
             "tags": f.fact.tech_tags, "score": round(f.total_score, 3), "new": f.fact.id in new_fact_ids}
            for f in candidate_facts
        ],
        covered=matched, missing=missing,
    )
    events.emit("archivist", events.HANDOFF, "Ranked facts to tailor from", to="scribe")

    try:
        loop_result = run_generator_critic_loop(
            job=job,
            candidate_facts=candidate_facts,
            skeleton=skeleton,
        )
    except Exception as e:
        events.emit("scribe", events.FAIL, f"Writing failed: {e}")
        raise PipelineError(f"Writing and checking the resume failed ({e})")

    filename_base = _slugify(job.company, job.role, str(int(time.time())))

    events.emit("critic", events.HANDOFF, f"Approved draft, score {loop_result.feedback.score:.0f}", to="publisher")
    events.emit("publisher", events.START, "Rendering LaTeX and compiling a 1-page PDF")
    try:
        fit = fit_one_page(job, candidate_facts, loop_result.resume, skeleton, filename_base, keep_entries=keep)
        pdf_path = fit.pdf_path
    except TexCompileError as e:
        events.emit("publisher", events.FAIL, "PDF compile failed after self-repair attempts")
        return ProcessResponse(
            resume_status=loop_result.status,
            score=loop_result.feedback.score,
            pdf_path=None,
            comment=f"The PDF could not be built even after automatic repairs. Details: {e.log[:300]}",
        )

    events.emit("publisher", events.PROGRESS, "PDF compiled", pdf=os.path.basename(pdf_path))
    from service.matching import coverage_of_resume, coverage_percent
    from service.agentic.critic import AI_WEIGHT
    from service.agentic.loop import _quality_label
    final_resume = fit.resume or loop_result.resume
    final_sk = fit.skeleton or skeleton
    covered, missing_now = coverage_of_resume(job, final_resume, final_sk)   # after trimming: what the PDF really shows
    coverage = coverage_percent(covered, missing_now)
    fb = loop_result.feedback
    score = round(AI_WEIGHT * fb.ai_score + (1 - AI_WEIGHT) * coverage, 1) if fb.ai_score else fb.score
    gaps_left = [g for g in open_gaps if g.lower() not in {m.lower() for m in matched}]
    return ProcessResponse(
        resume_status=_quality_label(score),
        score=score,
        pdf_path=pdf_path,
        comment=fb.revision_notes,
        fit_note=fit.note,
        github_note=("Added from your GitHub: " + ", ".join(github.added)) if github.added else "",
        ai_score=fb.ai_score, coverage=round(coverage, 1), covered=covered, missing=missing_now,
        gaps=gaps_left, facts_used=[b.fact_id for b in final_resume.bullets],
        summary=_summary(job, facts, candidate_facts, final_resume, final_sk, fit.dropped, covered, missing_now),
    )
