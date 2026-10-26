"""
End-to-End Mock Pipeline Test for Internify.
Tests:
1. SQLite Database Initialization
2. Job Request processing through the Agentic Loop (Extractor + Retriever + Generator + Critic)
3. Tectonic LaTeX PDF compilation
4. SQLite status updates & verification
"""

from __future__ import annotations
import os
import sys
import time

# Ensure root directory is on python path
sys.path.insert(0, os.path.abspath("."))

from bot.services.db import (
    init_db,
    add_application,
    update_application,
    is_duplicate_url,
    get_recent_applications,
)
from service.models import ProcessRequest
from service.main import process


def run_test():
    print("=" * 60)
    print("🧪 STARTING INTERNIFY END-TO-END MOCK PIPELINE TEST")
    print("=" * 60)

    # 1. Initialize SQLite Database
    print("\n[Step 1] Initializing SQLite database...")
    init_db()
    print("  ✅ Database initialized successfully.")

    # 2. Setup mock job posting
    mock_url = f"https://careers.mocktech.com/jobs/backend-intern-{int(time.time())}"
    mock_job = {
        "company": "Stripe",
        "job_title": "Software Engineering Intern",
        "location": "Remote",
        "job_description": (
            "We are looking for a Software Engineering Intern to join our payments platform. "
            "You will build robust backend services, work with distributed databases, and containerize microservices."
        ),
        "job_requirements": (
            "• Proficiency with Python and backend frameworks (FastAPI, Flask, or Django)\n"
            "• Experience with relational databases like PostgreSQL\n"
            "• Understanding of Docker containerization and Git workflows\n"
            "• Solid foundation in data structures, algorithms, and REST APIs"
        ),
        "job_link": mock_url,
    }

    # 3. Test duplicate check & initial record insertion
    print("\n[Step 2] Testing SQLite application logging...")
    assert not is_duplicate_url(mock_url), "New URL should not be a duplicate"
    app_id = add_application(
        company=mock_job["company"],
        job_title=mock_job["job_title"],
        location=mock_job["location"],
        job_description=mock_job["job_description"],
        job_requirements=mock_job["job_requirements"],
        job_link=mock_url,
        status="Processing",
    )
    print(f"  ✅ Application logged with ID: {app_id}")
    assert is_duplicate_url(mock_url), "URL should now be detected as duplicate"
    print("  ✅ Duplicate detection verified.")

    # 4. Run the core AI pipeline (Extractor + Retriever + Generator-Critic Loop + Tectonic LaTeX)
    print("\n[Step 3] Running Agentic Pipeline (Retrieval -> Generator -> Critic -> LaTeX)...")
    req = ProcessRequest(**mock_job)
    t0 = time.time()
    res = process(req)
    duration = time.time() - t0
    print(f"  ⏱️ Pipeline completed in {duration:.1f} seconds")

    print(f"\n[Step 4] Pipeline Results:")
    print(f"  • Quality Status: {res.resume_status}")
    print(f"  • ATS Match Score: {res.score:.1f}/100")
    print(f"  • PDF Path: {res.pdf_path}")
    print(f"  • Critic Comment: {res.comment[:200]}...")

    # 5. Verify PDF output
    assert res.pdf_path, "PDF path must not be None"
    assert os.path.exists(res.pdf_path), f"Generated PDF does not exist at {res.pdf_path}"
    pdf_size = os.path.getsize(res.pdf_path)
    print(f"  ✅ PDF file verified on disk ({pdf_size:,} bytes)")
    assert pdf_size > 10_000, "PDF file is suspiciously small"

    # 6. Update SQLite record
    print("\n[Step 5] Updating SQLite record...")
    update_application(
        app_id=app_id,
        status="Ready",
        score=res.score,
        resume_status=res.resume_status,
        pdf_path=res.pdf_path,
        comment=res.comment,
    )
    recent = get_recent_applications(limit=3)
    matching = next((a for a in recent if a["id"] == app_id), None)
    assert matching, "Application should be present in recent applications"
    assert matching["status"] == "Ready"
    print(f"  ✅ Verified SQLite record state: status={matching['status']}, score={matching['score']}")

    print("\n" + "=" * 60)
    print("🎉 ALL END-TO-END PIPELINE CHECKS PASSED SUCCESSFULLY!")
    print("=" * 60)


if __name__ == "__main__":
    run_test()

