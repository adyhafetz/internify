from __future__ import annotations

from service import config
from service.clients import instructor_client
from service.models import JobPosting

EXTRACTION_SYSTEM_PROMPT = """You extract structured job posting data from raw text.
Only use information explicitly present in the text. Do not invent tech stacks,
requirements, or keywords that aren't mentioned or clearly implied. If a field
isn't present, leave the list empty or the field null rather than guessing."""


def extract_job_posting(raw_text: str) -> JobPosting:
    return instructor_client.chat.completions.create(
        model=config.EXTRACTOR_MODEL,
        response_model=JobPosting,
        messages=[
            {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
            {"role": "user", "content": raw_text},
        ],
    )
