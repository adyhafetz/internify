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

app = FastAPI(title="Internify Resume Service")
app.include_router(admin_router)


def _slugify(*parts: str) -> str:
    joined = "_".join(parts)
    return re.sub(r"[^a-zA-Z0-9_]", "", joined.replace(" ", "_"))[:60]


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
    try:
        job = extract_job_posting(raw_text)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Extraction failed: {e}")

    facts = load_facts()
    if not facts:
        raise HTTPException(status_code=500, detail="Fact store is empty. Add your real bullets first.")

    skeleton = load_skeleton()
    candidate_facts = retrieve_relevant_facts(job, facts)

    try:
        loop_result = run_generator_critic_loop(
            job=job,
            candidate_facts=candidate_facts,
            skeleton=skeleton,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Generator/critic loop failed: {e}")

    filename_base = _slugify(job.company, job.role, str(int(time.time())))

    try:
        pdf_path = compile_tex(loop_result.resume, skeleton, filename_base=filename_base)
    except TexCompileError as e:
        return ProcessResponse(
            resume_status=loop_result.status,
            score=loop_result.feedback.score,
            pdf_path=None,
            comment=f"PDF compile failed after retries: {e.log[:300]}",
        )

    return ProcessResponse(
        resume_status=loop_result.status,
        score=loop_result.feedback.score,
        pdf_path=pdf_path,
        pdf_download_url=f"http://fastapi-service:8000/files/{os.path.basename(pdf_path)}",
        comment=loop_result.feedback.revision_notes,
    )
