from __future__ import annotations
import json
import logging
from typing import Optional
from pydantic import BaseModel, Field

from service import config
from service.clients import instructor_client, openai_client
from service.admin import (
    AddEducationRequest,
    AddExperienceRequest,
    AddProjectRequest,
    AddLeadershipRequest,
    SKILL_CATEGORIES,
)

logger = logging.getLogger(__name__)


# ---------- Skill extraction model ----------

class SkillExtractionResult(BaseModel):
    category: str = Field(
        ...,
        description="One of: 'Languages', 'Frameworks & Libraries', 'Databases', 'AI/ML', 'Developer Tools & Infra'",
    )
    items: list[str] = Field(..., description="List of individual skill/tool names")


class TechTagsResult(BaseModel):
    tech_tags: list[str] = Field(default_factory=list, description="Extracted technology, tool, or framework names")


# ---------- Job posting normalization model ----------

class NormalizedJob(BaseModel):
    company: str = Field(..., description="Company name")
    job_title: str = Field(..., description="Job title / role")
    location: str = Field("Remote", description="City/country or 'Remote'")
    job_description: str = Field(..., description="2-4 sentences summarizing the role")
    job_requirements: str = Field(..., description="Key qualifications as bullet points separated by newline")


def extract_education(text: str) -> AddEducationRequest:
    prompt = (
        "Extract education details from the user's plain English description.\n"
        "Do not invent information not present in the description."
    )
    return instructor_client.chat.completions.create(
        model=config.EXTRACTOR_MODEL,
        response_model=AddEducationRequest,
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": text},
        ],
    )


def extract_experience(text: str) -> AddExperienceRequest:
    prompt = (
        "Extract work experience details from the user's plain English description.\n"
        "Extract 2-4 achievement bullets (strong action verbs + metrics if mentioned) "
        "and list tech_tags for each bullet.\n"
        f"Group extracted skills into suggested_skills using ONLY these exact keys: {list(SKILL_CATEGORIES)}.\n"
        "Do not invent information not present in the description."
    )
    return instructor_client.chat.completions.create(
        model=config.EXTRACTOR_MODEL,
        response_model=AddExperienceRequest,
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": text},
        ],
    )


def extract_project(text: str) -> AddProjectRequest:
    prompt = (
        "Extract project details from the user's plain English description.\n"
        "Extract tech_stack (comma-separated), date ('Mon YYYY'), and 2-4 achievement bullets "
        "with their tech_tags.\n"
        f"Group extracted skills into suggested_skills using ONLY these exact keys: {list(SKILL_CATEGORIES)}.\n"
        "Do not invent information not present in the description."
    )
    return instructor_client.chat.completions.create(
        model=config.EXTRACTOR_MODEL,
        response_model=AddProjectRequest,
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": text},
        ],
    )


def extract_leadership(text: str) -> AddLeadershipRequest:
    prompt = (
        "Extract leadership / extracurricular details from the user's plain English description.\n"
        "Extract title, org, location, dates, and 1-3 bullet strings.\n"
        f"Group any extracted technical skills into suggested_skills using ONLY these keys: {list(SKILL_CATEGORIES)}.\n"
        "Do not invent information not present in the description."
    )
    return instructor_client.chat.completions.create(
        model=config.EXTRACTOR_MODEL,
        response_model=AddLeadershipRequest,
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": text},
        ],
    )


def extract_skills(text: str) -> SkillExtractionResult:
    prompt = (
        "The user wants to add skills to their resume. Extract and categorise them.\n"
        f"YOU MUST choose one category from ONLY: {list(SKILL_CATEGORIES)}.\n"
        "Do not invent items not mentioned by the user."
    )
    return instructor_client.chat.completions.create(
        model=config.EXTRACTOR_MODEL,
        response_model=SkillExtractionResult,
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": text},
        ],
    )


def extract_tech_tags(bullet_text: str) -> list[str]:
    prompt = (
        "Extract technology tags from this resume bullet point.\n"
        "Each tag should be a concise technology, tool, or framework name.\n"
        "Only include tags for things explicitly mentioned in the text."
    )
    result = instructor_client.chat.completions.create(
        model=config.EXTRACTOR_MODEL,
        response_model=TechTagsResult,
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": bullet_text},
        ],
    )
    return result.tech_tags


def normalize_job_posting(raw_text: str) -> NormalizedJob:
    prompt = (
        "You extract structured job information from scraped job posting text.\n"
        "Return ONLY valid structured data with company, job_title, location, job_description (2-4 sentences), "
        "and job_requirements (key qualifications as bullet points separated by newline).\n"
        "Do not invent information. If a field is missing, use an empty string or 'Remote' for location."
    )
    return instructor_client.chat.completions.create(
        model=config.EXTRACTOR_MODEL,
        response_model=NormalizedJob,
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": raw_text[:8000]},  # truncate to prevent context blowout
        ],
    )
