from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, Field


# ---------- Ingestion ----------

class ProcessRequest(BaseModel):
    company: str = Field(..., description="Company name")
    job_title: str = Field(..., description="Job title")
    location: str = Field(..., description="Location")
    job_description: str = Field(..., description="Job description summary")
    job_requirements: str = Field(..., description="Requirements and qualifications")
    job_link: str = Field(..., description="Original job posting URL")
    keep_entries: list[str] = Field(default_factory=list, description="Entry ids that must stay on the resume")


# ---------- Extraction ----------

class JobPosting(BaseModel):
    company: str
    role: str
    location: str = Field("", description="City/country, or 'Remote', as stated in the posting")
    deadline: Optional[str] = Field(None, description="Application closing date as YYYY-MM-DD, only if the posting states one")
    is_internship: Optional[bool] = Field(None, description="True for an internship, industrial training, placement or trainee role")
    core_tech_stack: list[str] = Field(default_factory=list)
    hard_requirements: list[str] = Field(default_factory=list)
    soft_skills: list[str] = Field(default_factory=list)
    ats_keywords: list[str] = Field(default_factory=list)
    seniority: Optional[str] = Field(None, description="e.g. intern, new grad, senior")

    def format_context(self) -> str:
        """Shared job context header used in generator and critic prompts."""
        return (
            f"Job: {self.role} at {self.company}\n"
            f"Core tech stack: {self.core_tech_stack}\n"
            f"Hard requirements: {self.hard_requirements}\n"
            f"ATS keywords: {self.ats_keywords}\n\n"
        )


# ---------- Static resume skeleton (never touched by the LLM) ----------

class ResumeHeader(BaseModel):
    name: str
    phone: str
    email: str
    linkedin_url: str
    linkedin_display: str
    github_url: str
    github_display: str


class EducationEntry(BaseModel):
    id: str
    institution: str
    location: str
    degree_line: str
    dates: str
    courses: list[str] = Field(default_factory=list, description="Master list of all courses — generator picks the relevant subset")
    honor_bullets: list[str] = Field(default_factory=list, description="Static bullets (Honors, Awards) always shown as-is")


class ExperienceEntryMeta(BaseModel):
    id: str
    title: str
    dates: str
    company: str
    location: str


class ProjectEntryMeta(BaseModel):
    id: str
    name: str
    tech_stack: str
    date: str


class LeadershipEntry(BaseModel):
    title: str
    dates: str
    org: str
    location: str
    bullets: list[str] = Field(default_factory=list)


class ResumeSkeleton(BaseModel):
    header: ResumeHeader
    education: list[EducationEntry] = Field(default_factory=list)
    experience_entries: list[ExperienceEntryMeta] = Field(default_factory=list)
    project_entries: list[ProjectEntryMeta] = Field(default_factory=list)
    leadership: list[LeadershipEntry] = Field(default_factory=list)
    skills: dict[str, list[str]] = Field(default_factory=dict)


# ---------- Fact store (tailorable bullets only: experience + project entries) ----------

class FactBullet(BaseModel):
    id: str
    entry_id: str = Field(..., description="Must match an experience_entries.id or project_entries.id in the skeleton")
    section: str = Field(..., description="experience or project")
    text: str = Field(..., description="The real, factual bullet as you'd write it yourself")
    tech_tags: list[str] = Field(default_factory=list)


class FactBulletWithScore(BaseModel):
    fact: FactBullet
    similarity: float
    keyword_boost: float

    @property
    def total_score(self) -> float:
        return self.similarity + self.keyword_boost


# ---------- Generation ----------

class ResumeBullet(BaseModel):
    fact_id: str = Field(..., description="Must reference an id from the provided fact list")
    entry_id: str = Field(..., description="Must match the fact's entry_id exactly")
    text: str = Field(..., description="Reworded/recalibrated bullet, facts must not change")


class GeneratedResume(BaseModel):
    bullets: list[ResumeBullet]
    selected_courses: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Map of education entry id → selected course names (subset of that entry's courses master list)",
    )
    selected_skills: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Map of skill category → selected skill items (subset of skeleton master skills)",
    )


# ---------- Critique ----------

class CriticFeedback(BaseModel):
    score: float = Field(..., ge=0, le=100)
    keyword_coverage_notes: str
    unsupported_claims: list[str] = Field(default_factory=list)
    overflow_risk: bool
    revision_notes: str
    ai_score: float = 0.0                              # the AI reviewer's own opinion (0-100)
    coverage: float = 0.0                              # % of the job's requirements the resume shows (counted in code)
    covered: list[str] = Field(default_factory=list)   # requirements the resume shows
    missing: list[str] = Field(default_factory=list)   # requirements it doesn't
    reworded: list[str] = Field(default_factory=list)  # fact ids whose bullet added something; kept to its own words


# ---------- Asking the user about missing skills ----------

class SkillAnswer(BaseModel):
    """The user's answer when the Archivist asked "do you have experience with X?"."""
    skill: str
    has: bool = False
    fact: Optional[FactBullet] = None  # new saved bullet point (under one of the user's entries), if they described it


# ---------- Output ----------

class ProcessResponse(BaseModel):
    resume_status: str 
    score: float
    pdf_path: Optional[str] = None
    comment: str
    fit_note: str = ""        # what was shortened or left out to fit one page, "" if nothing
    github_note: str = ""     # tools found in the user's GitHub repos and added to their profile, "" if none
    ai_score: float = 0.0     # the AI reviewer's opinion alone
    coverage: float = 0.0     # % of the job's requirements the resume shows, counted in code
    covered: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)          # tools the job wants that you have no saved experience of
    facts_used: list[str] = Field(default_factory=list)    # fact ids on the finished resume
    summary: dict = Field(default_factory=dict)             # what was picked, left out and reworded (for "What changed")


# ---------- Agentic loop result ----------

class LoopResult(BaseModel):
    resume: GeneratedResume
    feedback: CriticFeedback
    iterations_used: int
    status: str  #

