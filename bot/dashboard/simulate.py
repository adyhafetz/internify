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
# What the user "tells" the Archivist when it asks about Kubernetes (becomes a new saved bullet point)
NEW_FACT = ("exp1_f3", "exp1", "Deployed our API to Kubernetes with Helm charts for zero-downtime releases", ["Kubernetes", "Helm"], 1.0)

DRAFT1 = [
    ("exp1_f1", "exp1", "Built FastAPI and PostgreSQL REST APIs handling 2,000+ daily requests"),
    ("exp1_f3", "exp1", "Deployed APIs to Kubernetes with Helm charts for zero-downtime releases"),
    ("exp1_f2", "exp1", "Containerized services with Docker and automated deploys with GitHub Actions CI/CD"),
    ("proj1_f1", "proj1", "Engineered an agentic pipeline with Redis caching for resume tailoring"),
    ("exp2_f1", "exp2", "Optimized PostgreSQL queries, cutting report time by 40%"),
]
DRAFT2 = [
    ("exp1_f1", "exp1", "Built FastAPI and PostgreSQL REST APIs handling 2,000+ daily requests"),
    ("exp1_f3", "exp1", "Deployed APIs to Kubernetes with Helm charts for zero-downtime releases"),
    ("exp1_f2", "exp1", "Containerized microservices with Docker and automated Git-based CI/CD deploys"),
    ("proj1_f1", "proj1", "Engineered a Python agentic pipeline with retrieval and a generator-critic loop"),
    ("exp2_f1", "exp2", "Optimized PostgreSQL queries, cutting report time by 40%"),
    ("proj2_f1", "proj2", "Implemented graph algorithms and data structures in Python for a route planner"),
]


def _b(rows):
    return [{"fact_id": f, "entry_id": e, "text": t} for f, e, t in rows]


def _attempt(n, total, method, label, status, **extra):
    ev.emit("scout", ev.PROGRESS, label, attempt=n, total=total, method=method, label=label, status=status, **extra)


ROUTES = [
    ("direct", "a normal visit"),
    ("reader", "a page-rendering service"),
    ("archive", "a saved copy from the web archive"),
    ("search", "the same posting on another site"),
]
FAIL_REASONS = {
    "direct": "the site blocked us (HTTP 403)",
    "reader": "the page came back empty",
    "archive": "the web archive has no saved copy",
    "search": "no other site had a matching posting",
}


def run_simulation(url: str = URL, scenario: str = "normal", speed: float = 1.0) -> str:
    """scenario: normal | blocked (page blocked, another route works) | paste (every route fails, user pastes the text)."""
    def wait(sec: float) -> None:
        time.sleep(sec / max(speed, 0.1))

    rid = ev.start_run(url=url)
    wait(0.5)

    ev.emit("publisher", ev.PROGRESS, "New URL, not in tracker yet", duplicate=False)
    ev.emit("scout", ev.START, "Opening the job page", url=url)
    total = len(ROUTES)
    via = dict(via_fallback=False, label="a normal visit", note="", method="direct")
    if scenario == "normal":
        _attempt(1, total, "direct", "a normal visit", "trying"); wait(1.6)
        _attempt(1, total, "direct", "a normal visit", "ok", chars=len(RAW_TEXT))
    else:
        winner = 4 if scenario == "blocked" else None        # the 4th route (the same posting elsewhere) works
        for n, (method, label) in enumerate(ROUTES, start=1):
            _attempt(n, total, method, label, "trying"); wait(1.5)
            if n == winner:
                _attempt(n, total, method, label, "ok", chars=len(RAW_TEXT), note="found on jobs.example.org",
                         source_url="https://jobs.example.org/software-engineering-intern")
                via = dict(via_fallback=True, label=label, note="found on jobs.example.org", method=method)
                break
            _attempt(n, total, method, label, "failed", reason=FAIL_REASONS[method]); wait(0.7)
        else:
            ev.emit("scout", ev.ASK, "Asking you to paste the job description", paste=True)
            wait(5.0)                                          # ...you copy the text from the page and paste it in Telegram
            via = dict(via_fallback=True, label="the text you pasted", note="", method="pasted")
    ev.emit("scout", ev.DONE, f"Read {len(RAW_TEXT):,} characters from the page", chars=len(RAW_TEXT),
            preview=RAW_TEXT, url=url, source_url=url, **via)
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
    ev.emit("archivist", ev.ASK, "Asking you about Kubernetes", skills=["Kubernetes"])
    wait(3.2)                                  # ...you read the question on Telegram and tap Yes
    ev.emit("archivist", ev.PROGRESS, "Said yes", skill="Kubernetes", answer="yes")
    wait(3.0)                                  # ...pick where you used it, type one sentence
    ev.emit("archivist", ev.PROGRESS, "Saved", skill="Kubernetes", saved=True,
            where="Software Engineer Intern at Acme", text=NEW_FACT[2])
    wait(1.6)
    covered = ["Python", "FastAPI", "PostgreSQL", "Docker", "REST APIs", "Python backend frameworks",
               "Relational databases", "Docker containerization", "Git workflows",
               "Data structures and algorithms", "CI/CD pipelines", "Kubernetes"]
    all_facts = [NEW_FACT] + FACTS
    ev.emit("archivist", ev.DONE,
            f"Selected {len(all_facts)} of 32 facts (1 new from you); {len(covered)} requirements covered, 0 not found",
            total_facts=32,
            facts=[{"id": i, "entry_id": e, "text": t, "tags": g, "score": sc, "new": i == NEW_FACT[0]}
                   for i, e, t, g, sc in all_facts],
            covered=covered, missing=[])
    ev.emit("archivist", ev.HANDOFF, "Ranked facts to tailor from", to="scribe")
    wait(1.0)

    ev.emit("scribe", ev.START, "Draft 1: writing bullets", iteration=1)
    wait(2.0)
    ev.emit("scribe", ev.DONE, f"Draft 1 ready: {len(DRAFT1)} bullets", iteration=1, bullets=_b(DRAFT1))
    ev.emit("scribe", ev.HANDOFF, "Draft 1 for review", to="critic", iteration=1)
    ev.emit("critic", ev.START, "Checking every claim against your facts and scoring ATS fit", iteration=1)
    wait(1.8)
    bad = 'Bullet "Engineered an agentic pipeline with Redis caching" adds Redis, which is not in fact proj1_f1'
    ev.emit("critic", ev.DONE, "Draft 1 scored 40/100, 1 unsupported claim(s) caught", iteration=1, score=40.0,
            unsupported=[bad], notes="Remove the Redis claim and add the route-planner project to show algorithms.",
            coverage_notes="Good Python and PostgreSQL coverage.", threshold=85.0)
    ev.emit("critic", ev.HANDOFF, "Remove the Redis claim; add algorithms evidence", to="scribe", iteration=1)
    wait(1.0)

    ev.emit("scribe", ev.START, "Draft 2: revising from Critic's notes", iteration=2)
    wait(2.0)
    ev.emit("scribe", ev.DONE, f"Draft 2 ready: {len(DRAFT2)} bullets", iteration=2, bullets=_b(DRAFT2))
    ev.emit("scribe", ev.HANDOFF, "Draft 2 for review", to="critic", iteration=2)
    ev.emit("critic", ev.START, "Checking every claim against your facts and scoring ATS fit", iteration=2)
    wait(1.6)
    ev.emit("critic", ev.DONE, "Draft 2 scored 88/100, all claims verified", iteration=2, score=88.0,
            unsupported=[], notes="Strong match. Every requirement now has evidence.",
            coverage_notes="All stack items covered.", threshold=85.0)
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
