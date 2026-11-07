from __future__ import annotations
import logging
from pydantic import BaseModel, Field

from service import config
from service.clients import instructor_client
from service.profile_data import (
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


# ---------- Whole-resume import ----------

class ParsedBullet(BaseModel):
    text: str = Field(..., description="The bullet exactly as written in the resume, only cleaned of line breaks and bullet symbols")
    tech_tags: list[str] = Field(default_factory=list, description="Technologies, tools or frameworks explicitly named in this bullet")


class ParsedHeader(BaseModel):
    name: str = ""
    phone: str = ""
    email: str = ""
    linkedin_url: str = Field("", description="Full URL, e.g. https://linkedin.com/in/name. Empty if not in the resume")
    github_url: str = Field("", description="Full URL, e.g. https://github.com/name. Empty if not in the resume")


class ParsedEducation(BaseModel):
    institution: str
    location: str = ""
    degree_line: str = Field(..., description="Degree and, if written, the GPA/CGPA on the same line, e.g. 'B.S. in Computer Science, GPA: 3.8/4.0'")
    dates: str = Field("", description="'Mon YYYY -- Mon YYYY' or 'Mon YYYY -- Present'")
    courses: list[str] = Field(default_factory=list, description="Only if the resume lists courses / relevant coursework")
    honor_bullets: list[str] = Field(default_factory=list, description="Honors, dean's list, scholarships, awards listed under education")


class ParsedExperience(BaseModel):
    title: str
    company: str
    location: str = ""
    dates: str = Field("", description="'Mon YYYY -- Mon YYYY' or 'Mon YYYY -- Present'")
    bullets: list[ParsedBullet] = Field(default_factory=list)


class ParsedProject(BaseModel):
    name: str
    tech_stack: str = Field("", description="Comma-separated technologies shown next to the project name")
    date: str = Field("", description="'Mon YYYY'")
    bullets: list[ParsedBullet] = Field(default_factory=list)


class ParsedLeadership(BaseModel):
    title: str
    org: str
    location: str = ""
    dates: str = Field("", description="'Mon YYYY -- Mon YYYY' or 'Mon YYYY -- Present'")
    bullets: list[str] = Field(default_factory=list)


class ParsedResume(BaseModel):
    header: ParsedHeader = Field(default_factory=ParsedHeader)
    education: list[ParsedEducation] = Field(default_factory=list)
    experience: list[ParsedExperience] = Field(default_factory=list)
    projects: list[ParsedProject] = Field(default_factory=list)
    leadership: list[ParsedLeadership] = Field(default_factory=list)
    skills: dict[str, list[str]] = Field(
        default_factory=dict,
        description=f"Skills from the resume's skills section, keyed ONLY by these categories: {list(SKILL_CATEGORIES)}",
    )

    def is_empty(self) -> bool:
        return not (self.education or self.experience or self.projects or self.leadership or self.skills)


RESUME_IMPORT_PROMPT = f"""You read the text of a computer-science student's resume and return it as structured data.

RULES
- Use ONLY what is written in the resume. Never invent, add, improve or exaggerate anything: no new metrics,
  technologies, dates or claims. If something is not in the resume, leave it empty.
- Keep each bullet's wording as written. Only fix line breaks, hyphenation from PDF extraction and bullet symbols.
- Put each item in the right section:
  * experience = paid jobs, internships, research assistant roles
  * projects = personal, academic, open-source or hackathon projects
  * leadership = clubs, societies, volunteering, committee roles and other activities
  * education = degrees (with GPA on the degree line), plus honors/awards/scholarships as honor_bullets,
    and courses only if the resume lists them
- Dates: write 'Mon YYYY -- Mon YYYY' (or 'Mon YYYY -- Present'), e.g. 'Jun 2024 -- Aug 2024'. For projects use 'Mon YYYY'.
  If only a year is given, use just the year.
- tech_tags per bullet: technologies, tools or frameworks that bullet explicitly names (e.g. 'FastAPI', 'Docker').
- skills: sort the resume's skills section into ONLY these categories: {list(SKILL_CATEGORIES)}.
  Skip soft skills and anything that fits none of them.
- Resume text extracted from a PDF can have columns mixed together. Use the context to put lines back together."""


def extract_resume(text: str) -> ParsedResume:
    """One structured reading of a whole resume. The caller should show the result to the user before saving it."""
    return instructor_client.chat.completions.create(
        model=config.IMPORT_MODEL,
        response_model=ParsedResume,
        messages=[
            {"role": "system", "content": RESUME_IMPORT_PROMPT},
            {"role": "user", "content": text[:15000]},  # a one-page resume is ~4k characters; guards cost on huge files
        ],
    )
