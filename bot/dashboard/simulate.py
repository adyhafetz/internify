"""Scripted demo run that goes through the real event bus. No API keys, Telegram or Drive needed.

Used for developing the dashboard, and as a safe fallback when recording a demo."""
from __future__ import annotations

import time

from service import events as ev

URL = "https://careers.example.com/jobs/software-engineering-intern"

RAW_TEXT = """Software Engineering Intern, Payments Platform
Location: Remote (Malaysia)

About the role
We are looking for a Software Engineering Intern to join our payments platform team.
You will build robust backend services, work with distributed databases, and
containerize microservices that handle millions of transactions.

What you'll need
- Proficiency with Python and backend frameworks (FastAPI, Flask, or Django)
- Experience with relational databases such as PostgreSQL
- Understanding of Docker containerization and Git workflows
- Solid foundation in data structures, algorithms and REST APIs
- Bonus: exposure to Kubernetes and CI/CD pipelines
"""

STACK = ["Python", "FastAPI", "PostgreSQL", "Docker", "REST APIs", "Kubernetes"]
REQS = ["Python backend frameworks", "Relational databases", "Docker containerization",
        "Git workflows", "Data structures and algorithms", "CI/CD pipelines"]
KEYWORDS = ["Python", "FastAPI", "PostgreSQL", "Docker", "REST", "Git", "CI/CD", "Kubernetes", "microservices"]

FACTS = [
    ("exp1_f1", "exp1", "Built REST APIs with FastAPI and PostgreSQL serving 2,000+ daily requests", ["Python", "FastAPI", "PostgreSQL", "REST APIs"], 0.83),
    ("exp1_f2", "exp1", "Containerized the service with Docker and automated deploys through GitHub Actions", ["Docker", "Git", "CI/CD"], 0.79),
    ("proj1_f1", "proj1", "Designed an agentic resume pipeline with retrieval, a generator and a critic loop", ["Python", "OpenAI"], 0.71),
    ("proj2_f1", "proj2", "Implemented graph algorithms and data structures for a route planner in Python", ["Python", "Algorithms"], 0.64),
    ("exp2_f1", "exp2", "Wrote SQL migrations and query optimizations that cut report time by 40%", ["PostgreSQL", "SQL"], 0.62),
]

DRAFT1 = [
    ("exp1_f1", "exp1", "Built FastAPI and PostgreSQL REST APIs handling 2,000+ daily requests"),
    ("exp1_f2", "exp1", "Containerized services with Docker and automated deploys with GitHub Actions CI/CD"),
    ("proj1_f1", "proj1", "Engineered a Kubernetes-orchestrated agentic pipeline for resume tailoring"),
    ("exp2_f1", "exp2", "Optimized PostgreSQL queries, cutting report time by 40%"),
]
DRAFT2 = [
    ("exp1_f1", "exp1", "Built FastAPI and PostgreSQL REST APIs handling 2,000+ daily requests"),
    ("exp1_f2", "exp1", "Containerized microservices with Docker and automated Git-based CI/CD deploys"),
    ("proj1_f1", "proj1", "Engineered a Python agentic pipeline with retrieval and a generator-critic loop"),
    ("exp2_f1", "exp2", "Optimized PostgreSQL queries, cutting report time by 40%"),
    ("proj2_f1", "proj2", "Implemented graph algorithms and data structures in Python for a route planner"),
]


def _b(rows):
    return [{"fact_id": f, "entry_id": e, "text": t} for f, e, t in rows]


def run_simulation(url: str = URL, fail_scrape: bool = False, speed: float = 1.0) -> str:
    def wait(sec: float) -> None:
        time.sleep(sec / max(speed, 0.1))

    rid = ev.start_run(url=url)
    wait(0.5)

    ev.emit("publisher", ev.PROGRESS, "New URL, not in tracker yet", duplicate=False)
    ev.emit("scout", ev.START, "Opening the job page", url=url)
    wait(0.8)
    ev.emit("scout", ev.PROGRESS, "Fetching page through ScrapingAnt", method="scrapingant")
    wait(1.0)
    if fail_scrape:
        ev.emit("scout", ev.PROGRESS, "ScrapingAnt returned 403, switching to direct fetch", method="direct")
        wait(1.0)
        ev.emit("scout", ev.FAIL, "Page returned too little text (anti-bot wall or JavaScript)")
        ev.emit_run("failed", error="Scraping failed")
        return rid
    ev.emit("scout", ev.DONE, f"Read {len(RAW_TEXT):,} characters from the page",
            chars=len(RAW_TEXT), preview=RAW_TEXT, url=url)
    ev.emit("scout", ev.HANDOFF, "Raw job posting text", to="analyst")
    wait(1.2)

    ev.emit("analyst", ev.START, "Normalizing the raw page into a clean job posting")
    wait(1.4)
    ev.emit("analyst", ev.PROGRESS, "Identified Software Engineering Intern at Stripe",
            company="Stripe", role="Software Engineering Intern", location="Remote")
    ev.emit("analyst", ev.START, "Extracting tech stack, requirements and ATS keywords")
    wait(1.4)
    ev.emit("analyst", ev.DONE, f"Found {len(STACK)} technologies, {len(REQS)} requirements",
            company="Stripe", role="Software Engineering Intern", seniority="intern",
            core_tech_stack=STACK, hard_requirements=REQS, ats_keywords=KEYWORDS)
    ev.emit("analyst", ev.HANDOFF, "Requirements and keywords", to="archivist")
    wait(1.0)

    ev.emit("archivist", ev.START, "Searching 31 verified facts")
    wait(1.6)
    covered = ["Python", "FastAPI", "PostgreSQL", "Docker", "REST APIs", "Python backend frameworks",
               "Relational databases", "Docker containerization", "Git workflows",
               "Data structures and algorithms", "CI/CD pipelines"]
    ev.emit("archivist", ev.DONE,
            f"Selected {len(FACTS)} of 31 facts; {len(covered)} requirements covered, 1 not found",
            total_facts=31,
            facts=[{"id": i, "entry_id": e, "text": t, "tags": g, "score": s} for i, e, t, g, s in FACTS],
            covered=covered, missing=["Kubernetes"])
    ev.emit("archivist", ev.HANDOFF, "Ranked facts to tailor from", to="scribe")
    wait(1.0)

    ev.emit("scribe", ev.START, "Draft 1: writing bullets", iteration=1)
    wait(2.0)
    ev.emit("scribe", ev.DONE, f"Draft 1 ready: {len(DRAFT1)} bullets", iteration=1, bullets=_b(DRAFT1))
    ev.emit("scribe", ev.HANDOFF, "Draft 1 for review", to="critic", iteration=1)
    ev.emit("critic", ev.START, "Checking every claim against your facts and scoring ATS fit", iteration=1)
    wait(1.8)
    bad = 'Bullet "Engineered a Kubernetes-orchestrated agentic pipeline" adds Kubernetes, which is not in fact proj1_f1'
    ev.emit("critic", ev.DONE, "Draft 1 scored 40/100, 1 unsupported claim(s) caught", iteration=1, score=40.0,
            unsupported=[bad], notes="Remove the Kubernetes claim and add the route-planner project to show algorithms.",
            coverage_notes="Good Python and PostgreSQL coverage.", threshold=85.0)
    ev.emit("critic", ev.HANDOFF, "Remove the Kubernetes claim; add algorithms evidence", to="scribe", iteration=1)
    wait(1.0)

    ev.emit("scribe", ev.START, "Draft 2: revising from Critic's notes", iteration=2)
    wait(2.0)
    ev.emit("scribe", ev.DONE, f"Draft 2 ready: {len(DRAFT2)} bullets", iteration=2, bullets=_b(DRAFT2))
    ev.emit("scribe", ev.HANDOFF, "Draft 2 for review", to="critic", iteration=2)
    ev.emit("critic", ev.START, "Checking every claim against your facts and scoring ATS fit", iteration=2)
    wait(1.6)
    ev.emit("critic", ev.DONE, "Draft 2 scored 88/100, all claims verified", iteration=2, score=88.0,
            unsupported=[], notes="Strong match. Kubernetes is the only requirement without evidence.",
            coverage_notes="All other stack items covered.", threshold=85.0)
    ev.emit("critic", ev.HANDOFF, "Approved draft, score 88", to="publisher")
    wait(1.0)

    ev.emit("publisher", ev.START, "Rendering LaTeX and compiling a 1-page PDF")
    wait(1.2)
    ev.emit("publisher", ev.PROGRESS, "LaTeX error, asking LLM to repair the source (attempt 1/2)", repair_attempt=1)
    wait(1.4)
    ev.emit("publisher", ev.PROGRESS, "PDF compiled", pdf="")
    wait(0.8)
    ev.emit("publisher", ev.PROGRESS, "Uploading PDF to Google Drive")
    wait(1.0)
    ev.emit("publisher", ev.PROGRESS, "Uploaded to Drive", drive_link="https://drive.google.com/")
    wait(0.8)
    ev.emit("publisher", ev.DONE, "Resume filed and tracker updated", sheet_row=14,
            drive_link="https://drive.google.com/", pdf="")
    ev.emit_run("finished", score=88.0, status="Excellent", pdf="", drive_link="https://drive.google.com/",
                company="Stripe", role="Software Engineering Intern")
    return rid
