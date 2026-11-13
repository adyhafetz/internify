"""Make the resume fit on one page.

The resume is built, and its pages are counted. If it spills over, this module fixes it in this order, rebuilding after each
step: leave out leadership that has nothing to do with the job; tighten the wording of long bullets (one AI call, with a
code check that it adds no number or tool that was not in the original); leave out the least relevant bullets, then the
least relevant projects and roles; finally trim courses and skills. Your saved profile is never changed: all of this
happens on copies, for this one resume."""
from __future__ import annotations

import logging
import math
import re
import subprocess
from dataclasses import dataclass, field
from typing import Callable, Optional

from pydantic import BaseModel

from service import config, events
from service.latex.compile import compile_tex
from service.models import FactBulletWithScore, GeneratedResume, JobPosting, ResumeSkeleton

logger = logging.getLogger(__name__)

LINE_CHARS = 100                 # about how many characters of a bullet fit on one line
SHORTEN_LIMITS = (190, 150)      # first pass: at most two lines; second pass: about a line and a half
MAX_ROUNDS = 6                   # rebuilds while leaving things out


@dataclass
class FitResult:
    pdf_path: str
    pages: Optional[int]                 # None when the pages could not be counted
    note: str = ""                       # plain-language summary for the user, "" when nothing had to change
    fits: bool = True
    shortened: int = 0
    dropped: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- measuring

def count_pages(pdf_path: str) -> Optional[int]:
    """The number of pages, or None if it can't be told. qpdf is in the Docker image; pypdf is the fallback."""
    try:
        out = subprocess.run(["qpdf", "--show-npages", pdf_path], capture_output=True, text=True, timeout=30)
        if out.returncode == 0 and out.stdout.strip().isdigit():
            return int(out.stdout.strip())
    except Exception:
        pass
    try:
        from pypdf import PdfReader
        return len(PdfReader(pdf_path).pages)
    except Exception:
        return None


def overflow_lines(pdf_path: str) -> int:
    """About how many text lines sit on the pages after the first (a guess of 6 if pypdf can't read them)."""
    try:
        from pypdf import PdfReader
        pages = PdfReader(pdf_path).pages[1:]
        return max(1, sum(len([l for l in (p.extract_text() or "").splitlines() if l.strip()]) for p in pages))
    except Exception:
        return 6


# ---------------------------------------------------------------- relevance

def job_terms(job: JobPosting) -> set[str]:
    words: set[str] = set()
    for term in list(job.core_tech_stack) + list(job.ats_keywords) + list(job.hard_requirements):
        low = term.lower().strip()
        if low:
            words.add(low)
        words.update(w for w in re.findall(r"[a-z0-9+#.]{3,}", low))
    return words


def overlap(text: str, terms: set[str]) -> int:
    low = text.lower()
    tokens = set(re.findall(r"[a-z0-9+#.]{3,}", low))
    return sum(1 for t in terms if (t in tokens) or (" " in t and t in low))


# ---------------------------------------------------------------- tightening wording (AI, with a code guard)

class _Item(BaseModel):
    key: str
    text: str


class _Shortened(BaseModel):
    items: list[_Item]


_FACT_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9.+#/_-]*|\d[\d,.]*%?")


def _fact_tokens(text: str) -> set[str]:
    """Numbers, and words that look like a tool or a name (digits, capitals after the first word, dots and so on)."""
    out: set[str] = set()
    for i, tok in enumerate(_FACT_TOKEN.findall(text)):
        if tok[0].isdigit() or any(c.isdigit() for c in tok) or any(c in tok for c in ".+#/") or any(c.isupper() for c in tok[1:]) \
                or (i > 0 and tok[0].isupper()):
            out.add(tok.lower().rstrip(".,"))
    return out


def adds_new_facts(original: str, shortened: str) -> bool:
    """True if the shorter text mentions a number, tool or name that the original did not. Words may be dropped or
    swapped for plain ones, but nothing may be added."""
    known: set[str] = set()
    for tok in _FACT_TOKEN.findall(original):
        low = tok.lower().rstrip(".,")
        known.add(low)
        known.update(p for p in re.split(r"[-/_]", low) if p)    # "weather-cli" also counts as "weather" and "cli"
    return bool(_fact_tokens(shortened) - known)


def _shorten_with_ai(items: dict[str, str], limit: int) -> dict[str, str]:
    """Blocking. key -> shorter text. Fails open: an error returns nothing and the original wording stays."""
    from service.clients import instructor_client
    try:
        result: _Shortened = instructor_client.chat.completions.create(
            model=config.GENERATOR_MODEL, response_model=_Shortened,
            messages=[
                {"role": "system", "content": (
                    f"You shorten resume bullet points so a resume fits on one page. Rewrite each bullet in at most {limit} characters. "
                    "Keep the strongest result or number and the key technologies. Use only words and facts already in that bullet: "
                    "you may remove or tighten words, but you must NOT add any number, technology, name or claim. "
                    "Return every key, with its shortened text. If a bullet is already short enough, return it unchanged.")},
                {"role": "user", "content": "\n".join(f"{k}: {v}" for k, v in items.items())}])
        return {i.key: i.text.strip() for i in result.items if i.key in items and i.text.strip()}
    except Exception as err:
        logger.info("Shortening skipped: %s", err)
        return {}


# ---------------------------------------------------------------- the fitting

def _est_lines(text: str) -> float:
    return math.ceil(len(text) / LINE_CHARS) + 0.25


def fit_one_page(job: JobPosting, candidate_facts: list[FactBulletWithScore], resume: GeneratedResume, skeleton: ResumeSkeleton,
                 filename_base: str, *, compile_fn: Callable = compile_tex, pages_fn: Callable = count_pages,
                 overflow_fn: Callable = overflow_lines, shorten_fn: Callable = _shorten_with_ai) -> FitResult:
    """Build the PDF and, if it is longer than one page, shorten and trim copies until it fits."""
    work_resume, work_sk = resume.model_copy(deep=True), skeleton.model_copy(deep=True)
    terms = job_terms(job)
    score = {f.fact.id: f.total_score for f in candidate_facts}
    changes: dict = {"shortened": 0, "dropped": [], "courses": False, "skills": False}

    def build() -> tuple[str, Optional[int]]:
        path = compile_fn(work_resume, work_sk, filename_base)
        return path, pages_fn(path)

    path, pages = build()
    if pages is None or pages <= 1:
        return FitResult(path, pages)
    events.emit("publisher", events.PROGRESS, "Over one page, trimming", fit="start", pages=pages)

    def done(fits: bool) -> FitResult:
        return FitResult(path, pages, _note(changes, fits), fits, changes["shortened"], changes["dropped"])

    # 1. leadership with nothing to do with this job goes first
    gone = [e for e in work_sk.leadership if overlap(" ".join([e.title, e.org] + e.bullets), terms) == 0]
    if gone:
        work_sk.leadership = [e for e in work_sk.leadership if e not in gone]
        changes["dropped"] += [f"leadership: {e.title}" for e in gone]
        events.emit("publisher", events.PROGRESS, "Left out leadership that doesn't relate to this job", fit="leadership", left_out=len(gone))
        path, pages = build()
        if pages is None or pages <= 1:
            return done(True)

    # 2. tighten the wording of long bullets, then once more more firmly
    for limit in SHORTEN_LIMITS:
        long_items: dict[str, str] = {}
        for i, b in enumerate(work_resume.bullets):
            if len(b.text) > limit:
                long_items[f"b{i}"] = b.text
        for li, e in enumerate(work_sk.leadership):
            for bi, text in enumerate(e.bullets):
                if len(text) > limit:
                    long_items[f"l{li}.{bi}"] = text
        if not long_items:
            continue
        events.emit("publisher", events.PROGRESS, "Tightening long bullets", fit="shorten", count=len(long_items), limit=limit)
        new = shorten_fn(long_items, limit)
        for key, text in new.items():
            old = long_items[key]
            if len(text) >= len(old) or adds_new_facts(old, text):
                continue                                     # not shorter, or it added something: keep the original wording
            if key[0] == "b":
                work_resume.bullets[int(key[1:])].text = text
            else:
                li, bi = (int(x) for x in key[1:].split("."))
                work_sk.leadership[li].bullets[bi] = text
            changes["shortened"] += 1
        path, pages = build()
        if pages is None or pages <= 1:
            return done(True)

    # 3. leave out the least relevant bullets, then entries, a few at a time (guided by how far over it is)
    for _ in range(MAX_ROUNDS):
        need = overflow_fn(path)
        freed = 0.0
        while freed < need:
            victim = _least_relevant(work_resume, work_sk, score, terms)
            if victim is None:
                break
            freed += _remove(victim, work_resume, work_sk, changes)
        if freed == 0:
            break
        events.emit("publisher", events.PROGRESS, "Leaving out the least relevant items", fit="drop", dropped=len(changes["dropped"]))
        path, pages = build()
        if pages is None or pages <= 1:
            return done(True)

    # 4. last resort: fewer courses, only the relevant skills
    for edu_id, courses in list(work_resume.selected_courses.items()):
        if len(courses) > 3:
            work_resume.selected_courses[edu_id] = courses[:3]
            changes["courses"] = True
    source = work_resume.selected_skills if any(work_resume.selected_skills.values()) else work_sk.skills
    skills = {c: [i for i in items if overlap(i, terms)] for c, items in source.items()}
    skills = {c: i for c, i in skills.items() if i}
    if len(skills) >= 2:
        work_resume.selected_skills = skills
        changes["skills"] = True
    if changes["courses"] or changes["skills"]:
        path, pages = build()
    fits = pages is None or pages <= 1
    if not fits:
        events.emit("publisher", events.PROGRESS, "Couldn't fit on one page", fit="fail", pages=pages)
    return done(fits)


def _least_relevant(resume: GeneratedResume, sk: ResumeSkeleton, score: dict[str, float], terms: set[str]):
    """The next thing to leave out, by lowest relevance across the whole resume: ("bullet", i) or ("lead_bullet", li, bi)
    when an entry has several bullets (its weakest goes), or a whole entry ("entry", id) / ("lead_entry", li) once it has
    just one. The last work experience is never removed."""
    cands: list[tuple[float, tuple]] = []
    per_entry: dict[str, list[int]] = {}
    for i, b in enumerate(resume.bullets):
        per_entry.setdefault(b.entry_id, []).append(i)
    work_ids = {e.id for e in sk.experience_entries}
    work_left = sum(1 for k in per_entry if k in work_ids)
    for entry_id, idxs in per_entry.items():
        if len(idxs) > 1:
            worst = min(idxs, key=lambda i: score.get(resume.bullets[i].fact_id, 0.0))
            cands.append((score.get(resume.bullets[worst].fact_id, 0.0), ("bullet", worst)))
        elif not (entry_id in work_ids and work_left <= 1):
            cands.append((score.get(resume.bullets[idxs[0]].fact_id, 0.0), ("entry", entry_id)))
    for li, e in enumerate(sk.leadership):
        if len(e.bullets) > 1:
            bi = min(range(len(e.bullets)), key=lambda b: overlap(e.bullets[b], terms))
            cands.append((min(1.0, 0.15 * overlap(e.bullets[bi], terms)), ("lead_bullet", li, bi)))
        else:
            cands.append((min(1.0, 0.15 * overlap(" ".join([e.title, e.org] + e.bullets), terms)), ("lead_entry", li)))
    return min(cands, key=lambda c: c[0])[1] if cands else None


def _remove(victim: tuple, resume: GeneratedResume, sk: ResumeSkeleton, changes: dict) -> float:
    kind = victim[0]
    if kind == "bullet":
        b = resume.bullets.pop(victim[1]); changes["dropped"].append("bullet: " + b.text[:60])
        return _est_lines(b.text)
    if kind == "lead_bullet":
        text = sk.leadership[victim[1]].bullets.pop(victim[2]); changes["dropped"].append("leadership bullet: " + text[:60])
        return _est_lines(text)
    if kind == "entry":
        gone = [b for b in resume.bullets if b.entry_id == victim[1]]
        resume.bullets = [b for b in resume.bullets if b.entry_id != victim[1]]
        changes["dropped"].append(f"entry: {victim[1]}")
        return 2 + sum(_est_lines(b.text) for b in gone)
    e = sk.leadership.pop(victim[1]); changes["dropped"].append(f"leadership: {e.title}")
    return 2 + sum(_est_lines(t) for t in e.bullets)


def _note(changes: dict, fits: bool) -> str:
    bits = []
    if changes["shortened"]:
        bits.append(f"shortened {changes['shortened']} bullet{'s' if changes['shortened'] != 1 else ''}")
    left = len(changes["dropped"])
    if left:
        bits.append(f"left out {left} less relevant item{'s' if left != 1 else ''}")
    if changes["courses"] or changes["skills"]:
        bits.append("trimmed courses and skills")
    if not fits:
        return "I couldn't get it onto one page" + (" even after I " + " and ".join(bits) if bits else "") + ". Please check it."
    return ("To fit one page I " + " and ".join(bits) + ".") if bits else ""
