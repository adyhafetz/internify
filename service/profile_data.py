from __future__ import annotations
import json
import re
from datetime import datetime
from typing import Literal, get_args

from pydantic import BaseModel, Field

from service import config
from service.fact_store.store import _load_cache, _save_cache
from service.latex.compile import compile_tex
from service.models import GeneratedResume, ResumeBullet, ResumeSkeleton


class NotFound(LookupError):
    """Something the caller asked for isn't saved. The message is plain language."""


# ---------- Fixed skill categories (locked — cannot be extended via the API) ----------

SkillCategory = Literal[
    "Languages",
    "Frameworks & Libraries",
    "Databases",
    "AI/ML",
    "Developer Tools & Infra",
]

SKILL_CATEGORIES: tuple[str, ...] = get_args(SkillCategory)


# ---------- Helpers ----------

def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:30]


def _unique_id(base_id: str, existing: set[str]) -> str:
    if base_id not in existing:
        return base_id
    i = 2
    while f"{base_id}_{i}" in existing:
        i += 1
    return f"{base_id}_{i}"


def _load_skeleton_raw() -> dict:
    with open(config.SKELETON_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_skeleton_raw(data: dict) -> None:
    with open(config.SKELETON_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _load_facts_raw() -> list[dict]:
    with open(config.FACT_STORE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_facts_raw(data: list[dict]) -> None:
    with open(config.FACT_STORE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _evict_from_cache(fact_ids: list[str]) -> None:
    cache = _load_cache(config.EMBEDDING_CACHE_PATH)
    for fid in fact_ids:
        cache.pop(fid, None)
    _save_cache(config.EMBEDDING_CACHE_PATH, cache)


def _insert_suggested_skills(skeleton: dict, suggested: dict[str, list[str]]) -> dict[str, list[str]]:
    """
    Merges suggested skills into the skeleton's skills dict.
    - Only accepts keys that are one of the 5 fixed SKILL_CATEGORIES — unknown categories are silently dropped.
    - Deduplicates: items already in the list are not added again.
    Returns {category: [newly inserted items]} for reporting.
    """
    added: dict[str, list[str]] = {}
    for category, items in suggested.items():
        if category not in SKILL_CATEGORIES:
            continue
        existing = set(skeleton["skills"].get(category, []))
        new_items = [item for item in items if item not in existing]
        if new_items:
            skeleton["skills"].setdefault(category, []).extend(new_items)
            added[category] = new_items
    return added


# ---------- Request / Response models ----------

class NewFactBullet(BaseModel):
    text: str = Field(..., description="The factual bullet text as you'd write it yourself")
    tech_tags: list[str] = Field(default_factory=list)


class AddProjectRequest(BaseModel):
    name: str
    tech_stack: str = Field(..., description="Comma-separated tech stack shown in the resume header")
    date: str = Field(..., description="e.g. 'May 2026'")
    bullets: list[NewFactBullet]
    suggested_skills: dict[str, list[str]] = Field(
        default_factory=dict,
        description=(
            "Skills extracted from the project description, keyed by category. "
            f"Must use only these exact category names: {list(SKILL_CATEGORIES)}"
        ),
    )


class AddExperienceRequest(BaseModel):
    title: str
    company: str
    location: str = Field(..., description="e.g. 'Kuala Lumpur, Malaysia'")
    dates: str = Field(..., description="e.g. 'Sep 2024 -- Feb 2025'")
    bullets: list[NewFactBullet]
    suggested_skills: dict[str, list[str]] = Field(
        default_factory=dict,
        description=(
            "Skills extracted from the experience description, keyed by category. "
            f"Must use only these exact category names: {list(SKILL_CATEGORIES)}"
        ),
    )


class AddLeadershipRequest(BaseModel):
    title: str
    org: str
    location: str
    dates: str
    bullets: list[str] = Field(..., description="Plain bullet strings — no fact store needed for leadership")
    suggested_skills: dict[str, list[str]] = Field(
        default_factory=dict,
        description=(
            "Skills extracted from the leadership description, keyed by category. "
            f"Must use only these exact category names: {list(SKILL_CATEGORIES)}"
        ),
    )


class AddSkillsRequest(BaseModel):
    category: SkillCategory = Field(
        ...,
        description=f"Must be one of: {list(SKILL_CATEGORIES)}",
    )
    items: list[str]


class AdminResponse(BaseModel):
    message: str
    entry_id: str
    fact_ids: list[str] = Field(default_factory=list)
    skills_added: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Skills automatically inserted, grouped by category",
    )


class UpdateHeaderRequest(BaseModel):
    """PATCH semantics — only provided fields are updated."""
    name: str | None = None
    phone: str | None = None
    email: str | None = None
    linkedin_url: str | None = None
    github_url: str | None = None


class AddEducationRequest(BaseModel):
    institution: str
    location: str
    degree_line: str = Field(..., description="e.g. 'Bachelor of Computer Science (Hons)'")
    dates: str = Field(..., description="e.g. 'Sep 2022 -- Present'")
    courses: list[str] = Field(default_factory=list)
    honor_bullets: list[str] = Field(default_factory=list)


class UpdateFactRequest(BaseModel):
    text: str
    tech_tags: list[str] = Field(default_factory=list)


# ---------- Saving and removing resume data ----------

def get_skills() -> dict:
    """Returns the full master skills list grouped by category."""
    skeleton = _load_skeleton_raw()
    return {"skills": skeleton["skills"]}


def add_project(req: AddProjectRequest) -> AdminResponse:
    skeleton = _load_skeleton_raw()
    facts = _load_facts_raw()

    existing_entry_ids = {e["id"] for e in skeleton["project_entries"]}
    entry_id = _unique_id(f"proj_{_slugify(req.name)}", existing_entry_ids)

    skeleton["project_entries"].append({
        "id": entry_id,
        "name": req.name,
        "tech_stack": req.tech_stack,
        "date": req.date,
    })

    existing_fact_ids = {f["id"] for f in facts}
    new_fact_ids: list[str] = []
    for i, bullet in enumerate(req.bullets, start=1):
        fact_id = _unique_id(f"{entry_id}_{i}", existing_fact_ids)
        facts.append({
            "id": fact_id,
            "entry_id": entry_id,
            "section": "project",
            "text": bullet.text,
            "tech_tags": bullet.tech_tags,
        })
        existing_fact_ids.add(fact_id)
        new_fact_ids.append(fact_id)

    skills_added = _insert_suggested_skills(skeleton, req.suggested_skills)

    _save_skeleton_raw(skeleton)
    _save_facts_raw(facts)

    return AdminResponse(
        message=f"Project '{req.name}' added with {len(new_fact_ids)} bullet(s). entry_id={entry_id}",
        entry_id=entry_id,
        fact_ids=new_fact_ids,
        skills_added=skills_added,
    )


def add_experience(req: AddExperienceRequest) -> AdminResponse:
    skeleton = _load_skeleton_raw()
    facts = _load_facts_raw()

    existing_entry_ids = {e["id"] for e in skeleton["experience_entries"]}
    entry_id = _unique_id(f"exp_{_slugify(req.company)}", existing_entry_ids)

    skeleton["experience_entries"].append({
        "id": entry_id,
        "title": req.title,
        "company": req.company,
        "location": req.location,
        "dates": req.dates,
    })

    existing_fact_ids = {f["id"] for f in facts}
    new_fact_ids: list[str] = []
    for i, bullet in enumerate(req.bullets, start=1):
        fact_id = _unique_id(f"{entry_id}_{i}", existing_fact_ids)
        facts.append({
            "id": fact_id,
            "entry_id": entry_id,
            "section": "experience",
            "text": bullet.text,
            "tech_tags": bullet.tech_tags,
        })
        existing_fact_ids.add(fact_id)
        new_fact_ids.append(fact_id)

    skills_added = _insert_suggested_skills(skeleton, req.suggested_skills)

    _save_skeleton_raw(skeleton)
    _save_facts_raw(facts)

    return AdminResponse(
        message=f"Experience '{req.title} at {req.company}' added with {len(new_fact_ids)} bullet(s). entry_id={entry_id}",
        entry_id=entry_id,
        fact_ids=new_fact_ids,
        skills_added=skills_added,
    )


def add_leadership(req: AddLeadershipRequest) -> AdminResponse:
    skeleton = _load_skeleton_raw()

    skeleton["leadership"].append({
        "title": req.title,
        "org": req.org,
        "location": req.location,
        "dates": req.dates,
        "bullets": req.bullets,
    })

    skills_added = _insert_suggested_skills(skeleton, req.suggested_skills)

    _save_skeleton_raw(skeleton)
    entry_id = _slugify(req.title)

    return AdminResponse(
        message=f"Leadership '{req.title} at {req.org}' added.",
        entry_id=entry_id,
        skills_added=skills_added,
    )


def add_skills(req: AddSkillsRequest) -> AdminResponse:
    """
    Explicitly add skills to a fixed category.
    Category must be one of the 5 fixed values — Pydantic rejects anything else with a 422.
    """
    skeleton = _load_skeleton_raw()

    existing = set(skeleton["skills"].get(req.category, []))
    new_items = [item for item in req.items if item not in existing]
    skeleton["skills"].setdefault(req.category, []).extend(new_items)
    msg = f"Added {len(new_items)} new item(s) to '{req.category}'."

    _save_skeleton_raw(skeleton)

    return AdminResponse(
        message=msg,
        entry_id=req.category,
        skills_added={req.category: new_items} if new_items else {},
    )


def add_fact_to_entry(entry_id: str, req: NewFactBullet) -> AdminResponse:
    """Add a single extra fact bullet to an existing experience or project entry."""
    skeleton = _load_skeleton_raw()
    facts = _load_facts_raw()

    all_exp_ids = {e["id"] for e in skeleton["experience_entries"]}
    all_proj_ids = {e["id"] for e in skeleton["project_entries"]}
    all_entry_ids = all_exp_ids | all_proj_ids

    if entry_id not in all_entry_ids:
        raise NotFound(f"Entry id '{entry_id}' not found in skeleton.")

    section = "experience" if entry_id in all_exp_ids else "project"
    existing_fact_ids = {f["id"] for f in facts}
    count = sum(1 for f in facts if f["entry_id"] == entry_id)
    fact_id = _unique_id(f"{entry_id}_{count + 1}", existing_fact_ids)

    facts.append({
        "id": fact_id,
        "entry_id": entry_id,
        "section": section,
        "text": req.text,
        "tech_tags": req.tech_tags,
    })

    _save_facts_raw(facts)

    return AdminResponse(
        message=f"Fact bullet added to '{entry_id}'.",
        entry_id=entry_id,
        fact_ids=[fact_id],
    )


def delete_fact(fact_id: str) -> AdminResponse:
    facts = _load_facts_raw()
    original_count = len(facts)
    facts = [f for f in facts if f["id"] != fact_id]

    if len(facts) == original_count:
        raise NotFound(f"Fact id '{fact_id}' not found.")

    _save_facts_raw(facts)
    _evict_from_cache([fact_id])

    return AdminResponse(
        message=f"Fact '{fact_id}' deleted and removed from embedding cache.",
        entry_id=fact_id,
    )


def delete_project(entry_id: str) -> AdminResponse:
    """Delete a project entry and ALL its fact bullets."""
    skeleton = _load_skeleton_raw()
    facts = _load_facts_raw()

    if not any(e["id"] == entry_id for e in skeleton["project_entries"]):
        raise NotFound(f"Project entry '{entry_id}' not found.")

    skeleton["project_entries"] = [e for e in skeleton["project_entries"] if e["id"] != entry_id]

    removed_fact_ids = [f["id"] for f in facts if f["entry_id"] == entry_id]
    facts = [f for f in facts if f["entry_id"] != entry_id]

    _save_skeleton_raw(skeleton)
    _save_facts_raw(facts)
    _evict_from_cache(removed_fact_ids)

    return AdminResponse(
        message=f"Project '{entry_id}' deleted along with {len(removed_fact_ids)} fact bullet(s).",
        entry_id=entry_id,
        fact_ids=removed_fact_ids,
    )


def delete_experience(entry_id: str) -> AdminResponse:
    """Delete an experience entry and ALL its fact bullets."""
    skeleton = _load_skeleton_raw()
    facts = _load_facts_raw()

    if not any(e["id"] == entry_id for e in skeleton["experience_entries"]):
        raise NotFound(f"Experience entry '{entry_id}' not found.")

    skeleton["experience_entries"] = [e for e in skeleton["experience_entries"] if e["id"] != entry_id]

    removed_fact_ids = [f["id"] for f in facts if f["entry_id"] == entry_id]
    facts = [f for f in facts if f["entry_id"] != entry_id]

    _save_skeleton_raw(skeleton)
    _save_facts_raw(facts)
    _evict_from_cache(removed_fact_ids)

    return AdminResponse(
        message=f"Experience '{entry_id}' deleted along with {len(removed_fact_ids)} fact bullet(s).",
        entry_id=entry_id,
        fact_ids=removed_fact_ids,
    )


# ---------- Header ----------

def update_header(req: UpdateHeaderRequest) -> AdminResponse:
    """
    Update one or more header fields.
    For linkedin_url and github_url, the display text is automatically set to the same value.
    """
    skeleton = _load_skeleton_raw()
    updated: list[str] = []

    data = req.model_dump(exclude_none=True)
    for field, value in data.items():
        skeleton["header"][field] = value
        updated.append(field)
        # Mirror URL -> clean display text so they stay in sync
        if field == "linkedin_url":
            clean_disp = re.sub(r"^https?://(www\.)?", "", value).rstrip("/")
            skeleton["header"]["linkedin_display"] = clean_disp or value
        elif field == "github_url":
            clean_disp = re.sub(r"^https?://(www\.)?", "", value).rstrip("/")
            skeleton["header"]["github_display"] = clean_disp or value

    _save_skeleton_raw(skeleton)
    return AdminResponse(
        message=f"Header updated: {', '.join(updated)}.",
        entry_id="header",
    )


# ---------- Education ----------

def add_education(req: AddEducationRequest) -> AdminResponse:
    """Add a new education entry. ID is auto-generated from the institution name."""
    skeleton = _load_skeleton_raw()
    existing_ids = {e["id"] for e in skeleton["education"]}
    entry_id = _unique_id(f"edu_{_slugify(req.institution)}", existing_ids)

    skeleton["education"].append({
        "id": entry_id,
        "institution": req.institution,
        "location": req.location,
        "degree_line": req.degree_line,
        "dates": req.dates,
        "courses": req.courses,
        "honor_bullets": req.honor_bullets,
    })
    _save_skeleton_raw(skeleton)

    return AdminResponse(
        message=f"Education '{req.institution}' added. entry_id={entry_id}",
        entry_id=entry_id,
    )


def delete_education(edu_id: str) -> AdminResponse:
    """Delete an education entry by its ID."""
    skeleton = _load_skeleton_raw()

    if not any(e["id"] == edu_id for e in skeleton["education"]):
        raise NotFound(f"Education entry '{edu_id}' not found.")

    skeleton["education"] = [e for e in skeleton["education"] if e["id"] != edu_id]
    _save_skeleton_raw(skeleton)

    return AdminResponse(
        message=f"Education entry '{edu_id}' deleted.",
        entry_id=edu_id,
    )


def add_course(edu_id: str, course: str) -> AdminResponse:
    """Add a single course to an education entry's master course list."""
    skeleton = _load_skeleton_raw()
    entry = next((e for e in skeleton["education"] if e["id"] == edu_id), None)

    if not entry:
        raise NotFound(f"Education entry '{edu_id}' not found.")

    if course in entry.get("courses", []):
        return AdminResponse(
            message=f"Course '{course}' already exists in '{edu_id}'.",
            entry_id=edu_id,
        )

    entry.setdefault("courses", []).append(course)
    _save_skeleton_raw(skeleton)

    return AdminResponse(
        message=f"Course '{course}' added to '{edu_id}'.",
        entry_id=edu_id,
    )


# ---------- Fact update ----------

def update_fact(fact_id: str, req: UpdateFactRequest) -> AdminResponse:
    """
    Replace the text and tech_tags of an existing fact bullet.
    The old embedding is evicted from cache so it gets re-embedded on next use.
    """
    facts = _load_facts_raw()
    fact = next((f for f in facts if f["id"] == fact_id), None)

    if not fact:
        raise NotFound(f"Fact '{fact_id}' not found.")

    fact["text"] = req.text
    fact["tech_tags"] = req.tech_tags
    _save_facts_raw(facts)
    _evict_from_cache([fact_id])

    return AdminResponse(
        message=f"Fact '{fact_id}' updated and re-queued for embedding.",
        entry_id=fact_id,
        fact_ids=[fact_id],
    )


# ---------- Master Resume Compilation ----------

def generate_master_resume() -> dict:
    """
    Renders and compiles the full master resume containing all skeleton data
    and facts without any tailoring or personalization.
    """
    skeleton_raw = _load_skeleton_raw()
    skeleton = ResumeSkeleton.model_validate(skeleton_raw)
    facts_raw = _load_facts_raw()

    # Include all facts as bullets
    bullets = [
        ResumeBullet(
            entry_id=f["entry_id"],
            text=f["text"],
            fact_id=f["id"],
        )
        for f in facts_raw
    ]

    # Include all courses across education entries
    selected_courses = {
        edu.id: edu.courses
        for edu in skeleton.education
    }

    # Include all skills across all categories
    selected_skills = skeleton.skills

    resume = GeneratedResume(
        bullets=bullets,
        selected_courses=selected_courses,
        selected_skills=selected_skills,
    )

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    name_slug = _slugify(skeleton.header.name) or "master"
    filename_base = f"master_resume_{name_slug}_{timestamp}"

    try:
        pdf_path = compile_tex(resume, skeleton, filename_base)
    except Exception as e:
        raise RuntimeError(f"LaTeX compilation error: {e}") from e

    return {
        "message": "Master resume compiled successfully.",
        "name": skeleton.header.name,
        "filename": f"{skeleton.header.name.replace(' ', '_')}_Master_Resume.pdf",
        "pdf_path": pdf_path,
    }

