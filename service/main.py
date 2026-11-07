from __future__ import annotations
import logging
import os
import re
import time
from typing import Callable, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from service import config
from service.models import FactBulletWithScore, ProcessRequest, ProcessResponse, SkillAnswer
from service.extraction.extractor import extract_job_posting
from service.fact_store.store import load_facts, load_skeleton
from service.retrieval.retriever import retrieve_relevant_facts
from service.agentic.loop import run_generator_critic_loop
from service.latex.compile import compile_tex, TexCompileError
from service.admin import router as admin_router
from service import events

logger = logging.getLogger(__name__)

# Called with the tools the job wants that the user has no saved experience for; returns the user's answers.
SkillAsker = Callable[[list[str]], list[SkillAnswer]]

app = FastAPI(title="Internify Resume Service")
app.include_router(admin_router)


def _slugify(*parts: str) -> str:
    joined = "_".join(parts)
    return re.sub(r"[^a-zA-Z0-9_]", "", joined.replace(" ", "_"))[:60]


def _coverage(job, candidate_facts) -> tuple[list[str], list[str]]:
    """Which job requirements are backed by at least one selected fact (by tech tag)."""
    fact_tags = {t.lower() for f in candidate_facts for t in f.fact.tech_tags}
    seen: set[str] = set()
    covered: list[str] = []
    missing: list[str] = []
    for term in job.core_tech_stack + job.hard_requirements:
        key = term.lower()
        if key in seen:
            continue
        seen.add(key)
        # match on whole tag or a tag contained in a longer requirement phrase
        if key in fact_tags or any(tag in key for tag in fact_tags if len(tag) > 2):
            covered.append(term)
        else:
            missing.append(term)
    return covered, missing


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


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/files/{filename}")
def get_file(filename: str):
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    file_path = os.path.join(config.OUTPUT_DIR, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(file_path, media_type="application/pdf", filename=filename)


@app.post("/process", response_model=ProcessResponse)
def process(request: ProcessRequest) -> ProcessResponse:
    return run_pipeline(request)


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
        raise HTTPException(status_code=422, detail=f"Couldn't understand the job posting ({e})")
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
        raise HTTPException(status_code=500, detail="Your saved experience is empty. Send me your resume file (/importresume) or add some in /profile first.")

    skeleton = load_skeleton()
    events.emit("archivist", events.START, f"Searching {len(facts)} verified facts")
    candidate_facts = retrieve_relevant_facts(job, facts)
    matched, missing = _coverage(job, candidate_facts)

    # Tools the job wants that the user has no saved experience for: ask the user instead of guessing.
    new_fact_ids: set[str] = set()
    gaps = _skills_to_ask_about(job, missing) if ask_skills else []
    if gaps:
        events.emit("archivist", events.ASK, f"Asking you about {', '.join(gaps)}", skills=gaps)
        try:
            answers = ask_skills(gaps)
        except Exception as e:  # a failed question must never end the run: carry on without the answers
            logger.warning("Asking about missing skills failed: %s", e)
            answers = []
        candidate_facts, matched, missing, new_fact_ids = _apply_answers(answers, candidate_facts, matched, missing)
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
        raise HTTPException(status_code=500, detail=f"Writing and checking the resume failed ({e})")

    filename_base = _slugify(job.company, job.role, str(int(time.time())))

    events.emit("critic", events.HANDOFF, f"Approved draft, score {loop_result.feedback.score:.0f}", to="publisher")
    events.emit("publisher", events.START, "Rendering LaTeX and compiling a 1-page PDF")
    try:
        pdf_path = compile_tex(loop_result.resume, skeleton, filename_base=filename_base)
    except TexCompileError as e:
        events.emit("publisher", events.FAIL, "PDF compile failed after self-repair attempts")
        return ProcessResponse(
            resume_status=loop_result.status,
            score=loop_result.feedback.score,
            pdf_path=None,
            comment=f"The PDF could not be built even after automatic repairs. Details: {e.log[:300]}",
        )

    events.emit("publisher", events.PROGRESS, "PDF compiled", pdf=os.path.basename(pdf_path))
    return ProcessResponse(
        resume_status=loop_result.status,
        score=loop_result.feedback.score,
        pdf_path=pdf_path,
        pdf_download_url=f"http://fastapi-service:8000/files/{os.path.basename(pdf_path)}",
        comment=loop_result.feedback.revision_notes,
    )
