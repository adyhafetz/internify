"""Save a parsed resume into the user's saved experience (profile, jobs, projects, bullet points, skills).

Everything goes through the same admin functions the manual commands use, so ids, the embedding cache and the
skills list stay consistent whether an entry was typed one by one or imported."""
from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass, field
from typing import Optional

from service import config
from service.profile_data import (
    SKILL_CATEGORIES,
    AddEducationRequest,
    AddExperienceRequest,
    AddLeadershipRequest,
    AddProjectRequest,
    AddSkillsRequest,
    NewFactBullet,
    UpdateHeaderRequest,
    _evict_from_cache,
    _load_facts_raw,
    _load_skeleton_raw,
    _save_facts_raw,
    _save_skeleton_raw,
    add_education,
    add_experience,
    add_leadership,
    add_project,
    add_skills,
    update_header,
)

SAMPLE_NAME = "Jane Doe"  # the placeholder profile that ships with a fresh install


@dataclass
class ImportReport:
    added: dict[str, int] = field(default_factory=lambda: {
        "education": 0, "experience": 0, "projects": 0, "leadership": 0, "bullets": 0, "skills": 0})
    skipped: list[str] = field(default_factory=list)     # entries that were already saved (add mode)
    warnings: list[str] = field(default_factory=list)
    backup_dir: Optional[str] = None


def profile_is_sample() -> bool:
    return _load_skeleton_raw()["header"].get("name", "").strip() == SAMPLE_NAME


def backup_current() -> str:
    """Copy the saved profile and bullet points aside, so a replace can be undone by hand."""
    folder = os.path.join(os.path.dirname(config.FACT_STORE_PATH), "backups", time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(folder, exist_ok=True)
    for path in (config.SKELETON_PATH, config.FACT_STORE_PATH):
        shutil.copy2(path, os.path.join(folder, os.path.basename(path)))
    return folder


def _clear_everything() -> None:
    skeleton = _load_skeleton_raw()
    old_fact_ids = [f["id"] for f in _load_facts_raw()]
    skeleton["education"], skeleton["experience_entries"] = [], []
    skeleton["project_entries"], skeleton["leadership"] = [], []
    skeleton["skills"] = {category: [] for category in SKILL_CATEGORIES}
    _save_skeleton_raw(skeleton)
    _save_facts_raw([])
    _evict_from_cache(old_fact_ids)


def _norm(*parts: str) -> tuple[str, ...]:
    return tuple(p.strip().lower() for p in parts)


def apply_resume(parsed, mode: str) -> ImportReport:
    """mode 'replace': back up, clear what is saved, then import.  mode 'add': keep what is saved and skip
    entries that are already there."""
    if mode not in ("replace", "add"):
        raise ValueError(f"mode must be 'replace' or 'add', not {mode!r}")
    report = ImportReport()
    was_sample = profile_is_sample()

    if mode == "replace":
        report.backup_dir = backup_current()
        _clear_everything()

    # Contact details. Replacing (or still on the sample profile) sets every field, including empty ones, so no
    # placeholder LinkedIn/GitHub from the sample is left on a real resume.
    if mode == "replace" or was_sample:
        update_header(UpdateHeaderRequest(**parsed.header.model_dump()))

    sk = _load_skeleton_raw()
    have_edu = {_norm(e["institution"], e["degree_line"]) for e in sk["education"]}
    have_exp = {_norm(e["company"], e["title"]) for e in sk["experience_entries"]}
    have_proj = {_norm(e["name"]) for e in sk["project_entries"]}
    have_lead = {_norm(e["org"], e["title"]) for e in sk["leadership"]}

    for e in parsed.education:
        if _norm(e.institution, e.degree_line) in have_edu:
            report.skipped.append(f"{e.degree_line} at {e.institution}")
            continue
        add_education(AddEducationRequest(institution=e.institution, location=e.location, degree_line=e.degree_line,
                                          dates=e.dates, courses=e.courses, honor_bullets=e.honor_bullets))
        report.added["education"] += 1

    for e in parsed.experience:
        if _norm(e.company, e.title) in have_exp:
            report.skipped.append(f"{e.title} at {e.company}")
            continue
        bullets = [NewFactBullet(text=b.text.strip(), tech_tags=b.tech_tags) for b in e.bullets if b.text.strip()]
        add_experience(AddExperienceRequest(title=e.title, company=e.company, location=e.location, dates=e.dates,
                                            bullets=bullets))
        report.added["experience"] += 1
        report.added["bullets"] += len(bullets)
        if not bullets:
            report.warnings.append(f"{e.title} at {e.company} has no bullet points, so I can't use it when tailoring.")

    for e in parsed.projects:
        if _norm(e.name) in have_proj:
            report.skipped.append(f"project {e.name}")
            continue
        bullets = [NewFactBullet(text=b.text.strip(), tech_tags=b.tech_tags) for b in e.bullets if b.text.strip()]
        add_project(AddProjectRequest(name=e.name, tech_stack=e.tech_stack, date=e.date, bullets=bullets))
        report.added["projects"] += 1
        report.added["bullets"] += len(bullets)
        if not bullets:
            report.warnings.append(f"Project {e.name} has no bullet points, so I can't use it when tailoring.")

    for e in parsed.leadership:
        if _norm(e.org, e.title) in have_lead:
            report.skipped.append(f"{e.title} at {e.org}")
            continue
        add_leadership(AddLeadershipRequest(title=e.title, org=e.org, location=e.location, dates=e.dates,
                                            bullets=[b.strip() for b in e.bullets if b.strip()]))
        report.added["leadership"] += 1

    by_lower = {c.lower(): c for c in SKILL_CATEGORIES}
    for category, items in parsed.skills.items():
        canonical = by_lower.get(category.strip().lower())
        clean = [i.strip() for i in items if i.strip()]
        if canonical is None:
            if clean:
                report.warnings.append(f"Skipped {len(clean)} skill(s) under '{category}' (not one of the supported categories).")
            continue
        if clean:
            res = add_skills(AddSkillsRequest(category=canonical, items=clean))
            report.added["skills"] += sum(len(v) for v in res.skills_added.values())
    return report
