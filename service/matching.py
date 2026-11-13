"""How a job's requirements are matched against your experience, in code (no AI): the same rules everywhere.

  * A tool ("Postgres", "React", "JS") is matched by its proper name and its other names, as a whole word, using the
    shared tools list, so "Postgres" finds "PostgreSQL" and "Java" never matches inside "JavaScript".
  * A longer requirement ("Bachelor's degree in Computer Science") counts as met when most of its meaningful words
    appear in the text.
  * Soft skills ("good communication") are left out: a resume can't prove them and they never block a match.

Used by the Archivist (which requirements your saved bullets cover, what to ask about), by the Matcher in the daily
check, and after writing (how many of the job's requirements the finished resume actually shows)."""
from __future__ import annotations

import re
from typing import Iterable

from service import tools_catalog
from service.models import FactBullet, GeneratedResume, JobPosting, ResumeSkeleton

_STOP = {
    "and", "or", "the", "a", "an", "of", "in", "on", "for", "to", "with", "at", "by", "as", "is", "are", "be", "you",
    "your", "our", "we", "will", "can", "able", "ability", "strong", "good", "solid", "basic", "excellent", "proven",
    "knowledge", "understanding", "experience", "experienced", "familiarity", "familiar", "proficiency", "proficient",
    "skills", "skill", "plus", "preferred", "required", "must", "have", "has", "having", "least", "year", "years",
    "currently", "pursuing", "related", "field", "relevant", "using", "use", "working", "work", "etc", "e.g", "eg",
    "such", "like", "including", "other", "any", "least", "one", "more", "nice", "bonus", "hands-on", "hands",
    "candidate", "candidates", "who", "that", "this", "it", "its", "in-depth", "deep", "well", "least",
}
_WORD = re.compile(r"[a-z0-9][a-z0-9+#.]*")
_SOFT_WORDS = ("communication", "teamwork", "team player", "interpersonal", "attitude", "motivated", "eager", "passion",
               "willing", "fast learner", "detail-oriented", "attention to detail", "independently", "proactive",
               "leadership", "time management", "problem-solving", "problem solving", "critical thinking", "adaptab",
               "curious", "curiosity", "english", "bahasa", "mandarin", "language", "self-starter", "collaborat")


def is_soft(term: str) -> bool:
    low = term.lower()
    return tools_catalog.is_soft_skill(low) or any(w in low for w in _SOFT_WORDS)


def _words(text: str) -> list[str]:
    return [w.rstrip(".") for w in _WORD.findall(text.lower())]


def _content_words(text: str) -> set[str]:
    return {w for w in _words(text) if w not in _STOP and len(w) > 1}


def _stem(w: str) -> str:
    for suffix in ("ing", "ies", "es", "ed"):
        if len(w) > len(suffix) + 3 and w.endswith(suffix):
            return w[: -len(suffix)]
    return w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w


def term_met(term: str, text: str, tags: Iterable[str] = ()) -> bool:
    """Is this one requirement shown by the text (and the tool tags that go with it)?"""
    term = term.strip()
    if not term:
        return True
    tags = [t for t in tags if t]
    low = text.lower()
    tool = tools_catalog.canonical(term)
    if tool or len(_content_words(term)) <= 1:
        name = tool or term
        if any(tools_catalog.key(tools_catalog.canonical(t) or t) == tools_catalog.key(name) for t in tags):
            return True
        if tools_catalog.mentions(name, low) or any(tools_catalog.mentions(name, t) for t in tags):
            return True
        if not tool:   # a single plain word ("APIs", "testing"): whole word, ignoring plurals
            words = {_stem(w) for w in _words(low + " " + " ".join(tags))}
            return all(_stem(w) in words for w in _content_words(term)) if _content_words(term) else False
        return False
    # a longer phrase: any tool it names must be shown, and most of its other words must appear
    words = _content_words(term)
    tools_named = [w for w in words if tools_catalog.canonical(w)]
    if tools_named:   # "Python or Java", "React and Node.js": showing one of the tools it names is enough
        return any(term_met(t, text, tags) for t in tools_named)
    have = {_stem(w) for w in _words(low + " " + " ".join(tags))}
    hits = sum(1 for w in words if _stem(w) in have)
    return hits / len(words) >= 0.6


def job_requirements(job: JobPosting) -> list[str]:
    """The job's tools and hard requirements, once each, without soft skills."""
    seen: set[str] = set()
    out: list[str] = []
    for term in list(job.core_tech_stack) + list(job.hard_requirements):
        term = term.strip()
        k = tools_catalog.key(tools_catalog.canonical(term) or term)
        if not term or k in seen or is_soft(term):
            continue
        seen.add(k)
        out.append(term)
    return out


def coverage_of_facts(job: JobPosting, facts: list[FactBullet], skill_items: list[str] = ()) -> tuple[list[str], list[str]]:
    """(covered, missing): which requirements at least one of these bullets (or your skills list) shows."""
    text = "\n".join(f.text for f in facts) + "\n" + "\n".join(skill_items)
    tags = [t for f in facts for t in f.tech_tags]
    covered, missing = [], []
    for term in job_requirements(job):
        (covered if term_met(term, text, tags) else missing).append(term)
    return covered, missing


def resume_text(resume: GeneratedResume, skeleton: ResumeSkeleton) -> str:
    """Everything a recruiter reads on the finished resume."""
    parts = [b.text for b in resume.bullets]
    skills = resume.selected_skills if any(resume.selected_skills.values()) else skeleton.skills
    parts += [f"{c}: {', '.join(items)}" for c, items in skills.items()]
    for edu in skeleton.education:
        parts.append(edu.degree_line)
        parts += resume.selected_courses.get(edu.id, edu.courses[:5])
        parts += edu.honor_bullets
    used = {b.entry_id for b in resume.bullets}
    parts += [f"{e.title} {e.company}" for e in skeleton.experience_entries if e.id in used]
    parts += [f"{p.name} {p.tech_stack}" for p in skeleton.project_entries if p.id in used]
    for lead in skeleton.leadership:
        parts += [lead.title, lead.org] + lead.bullets
    return "\n".join(parts)


def coverage_of_resume(job: JobPosting, resume: GeneratedResume, skeleton: ResumeSkeleton) -> tuple[list[str], list[str]]:
    """(covered, missing) on the finished resume: what a recruiter or an ATS will actually see."""
    text = resume_text(resume, skeleton)
    covered, missing = [], []
    for term in job_requirements(job):
        (covered if term_met(term, text) else missing).append(term)
    return covered, missing


def coverage_percent(covered: list[str], missing: list[str]) -> float:
    total = len(covered) + len(missing)
    return 100.0 * len(covered) / total if total else 100.0
