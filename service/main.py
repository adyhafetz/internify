from __future__ import annotations
import os
import re
import time

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from service import config
from service.models import ProcessRequest, ProcessResponse
from service.extraction.extractor import extract_job_posting
from service.fact_store.store import load_facts, load_skeleton
from service.retrieval.retriever import retrieve_relevant_facts
from service.agentic.loop import run_generator_critic_loop
from service.latex.compile import compile_tex, TexCompileError
from service.admin import router as admin_router
from service import events

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
        raise HTTPException(status_code=500, detail="Your saved experience is empty. Add some with /addexperience or /addproject first.")

    skeleton = load_skeleton()
    events.emit("archivist", events.START, f"Searching {len(facts)} verified facts")
    candidate_facts = retrieve_relevant_facts(job, facts)
    matched, missing = _coverage(job, candidate_facts)
    events.emit(
        "archivist", events.DONE,
        f"Selected {len(candidate_facts)} of {len(facts)} facts; {len(matched)} requirements covered, {len(missing)} not found",
        total_facts=len(facts),
        facts=[
            {"id": f.fact.id, "entry_id": f.fact.entry_id, "text": f.fact.text,
             "tags": f.fact.tech_tags, "score": round(f.total_score, 3)}
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
