"""One way to browse and edit everything the user has saved: education, jobs, projects and leadership.

Jobs and projects keep their bullet points as "facts" (with technology tags). Leadership bullets and education
honors are plain lists in the profile. Callers (the Telegram browser) don't need to know that difference: every
entry is an ``EntryView`` with numbered ``Item`` s, and the same five operations work on all of them.

Entries and items are addressed with short references (``kind`` + ``ref``, and an item ``token``) so they fit in
Telegram buttons; users never see or type internal IDs."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Optional

from service.profile_data import (
    SKILL_CATEGORIES,
    AddSkillsRequest,
    NewFactBullet,
    UpdateFactRequest,
    UpdateHeaderRequest,
    add_skills,
    update_header,
    _load_facts_raw,
    _load_skeleton_raw,
    _save_skeleton_raw,
    add_fact_to_entry,
    delete_education,
    delete_experience,
    delete_fact,
    delete_project,
    update_fact,
)

MAX_ITEM_CHARS = 400

EDU, EXP, PROJ, LEAD = "edu", "exp", "proj", "lead"
KINDS = (EDU, EXP, PROJ, LEAD)
FACT_KINDS = (EXP, PROJ)  # bullet points stored as facts, with technology tags


class EditError(ValueError):
    """Something the user can fix. The message is plain language and is shown to them."""


@dataclass(frozen=True)
class Item:
    token: str  # short reference used in buttons
    text: str


@dataclass(frozen=True)
class EntrySummary:
    kind: str
    ref: str    # entry id (education, jobs, projects) or position in the list (leadership)
    label: str
    count: int


@dataclass
class EntryView:
    kind: str
    ref: str
    title: str
    subtitle: str
    items: list[Item]
    noun: str                    # what the items are called: "bullet point" or "honor"
    courses: Optional[list[str]] = None   # education only
    has_tags: bool = False       # items carry technology tags (jobs and projects)


# ---------------------------------------------------------------- helpers

def _fact_token(fact_id: str) -> str:
    return hashlib.sha1(fact_id.encode("utf-8")).hexdigest()[:8]


def _clean(text: str) -> str:
    text = re.sub(r"\s+", " ", (text or "")).strip()
    if not text:
        raise EditError("That was empty. Please send the text.")
    if len(text) > MAX_ITEM_CHARS:
        raise EditError(f"That's {len(text)} characters. A resume line should stay under {MAX_ITEM_CHARS}. Please shorten it.")
    return text


def _join(*parts: str, sep: str = " · ") -> str:
    return sep.join(p.strip() for p in parts if p and p.strip())


def _lead_index(skeleton: dict, ref: str) -> Optional[int]:
    return int(ref) if ref.isdigit() and int(ref) < len(skeleton["leadership"]) else None


def _find_entry(skeleton: dict, kind: str, ref: str) -> Optional[dict]:
    if kind == LEAD:
        i = _lead_index(skeleton, ref)
        return skeleton["leadership"][i] if i is not None else None
    key = {EDU: "education", EXP: "experience_entries", PROJ: "project_entries"}.get(kind)
    return next((e for e in skeleton.get(key, []) if e["id"] == ref), None) if key else None


def _plain_list(entry: dict, kind: str) -> list[str]:
    return entry["bullets"] if kind == LEAD else entry["honor_bullets"]


# ---------------------------------------------------------------- reading

def list_entries() -> list[EntrySummary]:
    """Everything that can be opened, in the order of a resume: education, jobs, projects, leadership."""
    sk, facts = _load_skeleton_raw(), _load_facts_raw()
    count = {}
    for f in facts:
        count[f["entry_id"]] = count.get(f["entry_id"], 0) + 1
    out = [EntrySummary(EDU, e["id"], _join(e["degree_line"], e["institution"]), len(e.get("honor_bullets", [])))
           for e in sk["education"]]
    out += [EntrySummary(EXP, e["id"], _join(e["title"], e["company"]), count.get(e["id"], 0)) for e in sk["experience_entries"]]
    out += [EntrySummary(PROJ, e["id"], e["name"], count.get(e["id"], 0)) for e in sk["project_entries"]]
    out += [EntrySummary(LEAD, str(i), _join(e["title"], e["org"]), len(e.get("bullets", [])))
            for i, e in enumerate(sk["leadership"])]
    return out


def get_entry(kind: str, ref: str) -> Optional[EntryView]:
    sk = _load_skeleton_raw()
    entry = _find_entry(sk, kind, ref)
    if entry is None:
        return None
    if kind in FACT_KINDS:
        items = [Item(_fact_token(f["id"]), f["text"]) for f in _load_facts_raw() if f["entry_id"] == ref]
        if kind == EXP:
            return EntryView(kind, ref, _join(entry["title"], entry["company"]), _join(entry["dates"], entry["location"]),
                             items, "bullet point", has_tags=True)
        return EntryView(kind, ref, entry["name"], _join(entry["tech_stack"], entry["date"]), items, "bullet point", has_tags=True)
    if kind == LEAD:
        items = [Item(str(i), t) for i, t in enumerate(entry.get("bullets", []))]
        return EntryView(kind, ref, _join(entry["title"], entry["org"]), _join(entry["dates"], entry["location"]), items, "bullet point")
    items = [Item(str(i), t) for i, t in enumerate(entry.get("honor_bullets", []))]
    return EntryView(kind, ref, entry["degree_line"], _join(entry["institution"], entry["dates"], entry["location"]),
                     items, "honor", courses=list(entry.get("courses", [])))


# ---------------------------------------------------------------- changing

def add_item(kind: str, ref: str, text: str, tags: Optional[list[str]] = None) -> Item:
    text = _clean(text)
    if kind in FACT_KINDS:
        if _find_entry(_load_skeleton_raw(), kind, ref) is None:
            raise EditError("That entry no longer exists.")
        res = add_fact_to_entry(ref, NewFactBullet(text=text, tech_tags=tags or []))
        return Item(_fact_token(res.fact_ids[0]), text)
    sk = _load_skeleton_raw()
    entry = _find_entry(sk, kind, ref)
    if entry is None or kind not in (EDU, LEAD):
        raise EditError("That entry no longer exists.")
    lst = _plain_list(entry, kind)
    lst.append(text)
    _save_skeleton_raw(sk)
    return Item(str(len(lst) - 1), text)


def edit_item(kind: str, ref: str, token: str, text: str, tags: Optional[list[str]] = None) -> None:
    text = _clean(text)
    if kind in FACT_KINDS:
        fact = next((f for f in _load_facts_raw() if f["entry_id"] == ref and _fact_token(f["id"]) == token), None)
        if fact is None:
            raise EditError("That bullet point no longer exists. Open the entry again.")
        update_fact(fact["id"], UpdateFactRequest(text=text, tech_tags=tags if tags is not None else fact.get("tech_tags", [])))
        return
    sk = _load_skeleton_raw()
    entry = _find_entry(sk, kind, ref)
    lst = _plain_list(entry, kind) if entry is not None and kind in (EDU, LEAD) else None
    if lst is None or not token.isdigit() or int(token) >= len(lst):
        raise EditError("That line no longer exists. Open the entry again.")
    lst[int(token)] = text
    _save_skeleton_raw(sk)


def delete_item(kind: str, ref: str, token: str) -> None:
    if kind in FACT_KINDS:
        fact = next((f for f in _load_facts_raw() if f["entry_id"] == ref and _fact_token(f["id"]) == token), None)
        if fact is None:
            raise EditError("That bullet point no longer exists. Open the entry again.")
        delete_fact(fact["id"])
        return
    sk = _load_skeleton_raw()
    entry = _find_entry(sk, kind, ref)
    lst = _plain_list(entry, kind) if entry is not None and kind in (EDU, LEAD) else None
    if lst is None or not token.isdigit() or int(token) >= len(lst):
        raise EditError("That line no longer exists. Open the entry again.")
    lst.pop(int(token))
    _save_skeleton_raw(sk)


def delete_entry(kind: str, ref: str) -> int:
    """Delete a whole entry (and its bullet points). Returns how many bullet points went with it."""
    if kind == EXP:
        return len(delete_experience(ref).fact_ids)
    if kind == PROJ:
        return len(delete_project(ref).fact_ids)
    sk = _load_skeleton_raw()
    if kind == EDU:
        entry = _find_entry(sk, EDU, ref)
        if entry is None:
            raise EditError("That entry no longer exists.")
        n = len(entry.get("honor_bullets", []))
        delete_education(ref)
        return n
    i = _lead_index(sk, ref)
    if i is None:
        raise EditError("That entry no longer exists.")
    n = len(sk["leadership"][i].get("bullets", []))
    sk["leadership"].pop(i)   # by position, so two roles with the same title can't be confused
    _save_skeleton_raw(sk)
    return n


def add_course(ref: str, name: str) -> bool:
    """Add a course to a degree. False if it was already there."""
    name = _clean(name)
    sk = _load_skeleton_raw()
    entry = _find_entry(sk, EDU, ref)
    if entry is None:
        raise EditError("That entry no longer exists.")
    courses = entry.setdefault("courses", [])
    if name.lower() in (c.lower() for c in courses):
        return False
    courses.append(name)
    _save_skeleton_raw(sk)
    return True


def remove_course(ref: str, index: int) -> str:
    """Remove a course by its position. Returns its name."""
    sk = _load_skeleton_raw()
    entry = _find_entry(sk, EDU, ref)
    if entry is None or not 0 <= index < len(entry.get("courses", [])):
        raise EditError("That course no longer exists. Open the entry again.")
    name = entry["courses"].pop(index)
    _save_skeleton_raw(sk)
    return name


# ---------------------------------------------------------------- contact details

CONTACT_FIELDS = (("name", "Name"), ("phone", "Phone"), ("email", "Email"),
                  ("linkedin_url", "LinkedIn"), ("github_url", "GitHub"))
_REQUIRED = {"name"}


def get_contact() -> list[tuple[str, str, str]]:
    """[(field, label, value)] as shown at the top of the resume. LinkedIn/GitHub show their short form."""
    header = _load_skeleton_raw()["header"]
    shown = {"linkedin_url": header.get("linkedin_display", ""), "github_url": header.get("github_display", "")}
    return [(key, label, shown.get(key) or header.get(key, "")) for key, label in CONTACT_FIELDS]


def _profile_url(value: str, site: str, path: str, label: str) -> str:
    """Accept a full link, 'site.com/name' or just the name, and return a full https link."""
    if re.match(r"^https?://", value, re.I):
        return value
    if site in value.lower():
        return "https://" + value.lstrip("/")
    if re.fullmatch(r"[A-Za-z0-9._%-]+", value):
        return f"https://{site}/{path}{value}"
    raise EditError(f"That doesn't look like a {label} link. Send the full link, for example https://{site}/{path}yourname")


def set_contact(field: str, value: str) -> str:
    """Save one contact detail. '-' clears an optional one. Returns the cleaned value that was saved."""
    if field not in dict(CONTACT_FIELDS):
        raise EditError("Unknown contact field.")
    value = re.sub(r"\s+", " ", (value or "")).strip()
    if value == "-":
        if field in _REQUIRED:
            raise EditError("Your name can't be removed. Send your new name instead.")
        value = ""
    elif not value:
        raise EditError("That was empty. Please send the text.")
    if value and len(value) > 200:
        raise EditError("That's too long for a contact detail.")
    if field == "name" and not re.search(r"[^\W\d_]", value):
        raise EditError("That doesn't look like a name. Please send your full name.")
    if value and field == "email" and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
        raise EditError("That doesn't look like an email address. Send it like name@example.com.")
    if value and field == "linkedin_url":
        value = _profile_url(value, "linkedin.com", "in/", "LinkedIn")
    if value and field == "github_url":
        value = _profile_url(value, "github.com", "", "GitHub")
    update_header(UpdateHeaderRequest(**{field: value}))
    return value


# ---------------------------------------------------------------- skills

@dataclass(frozen=True)
class Skill:
    token: str
    name: str


def _skill_token(name: str) -> str:
    return hashlib.sha1(name.lower().encode("utf-8")).hexdigest()[:8]


def list_skill_categories() -> list[tuple[int, str, int]]:
    """[(position, category name, how many skills)] in a fixed order."""
    skills = _load_skeleton_raw().get("skills", {})
    return [(i, c, len(skills.get(c, []))) for i, c in enumerate(SKILL_CATEGORIES)]


def get_skills(category_index: int) -> tuple[str, list[Skill]]:
    if not 0 <= category_index < len(SKILL_CATEGORIES):
        raise EditError("Unknown skill category.")
    category = SKILL_CATEGORIES[category_index]
    return category, [Skill(_skill_token(n), n) for n in _load_skeleton_raw().get("skills", {}).get(category, [])]


def add_skills_text(category_index: int, text: str) -> tuple[list[str], list[str]]:
    """Add skills typed as 'Python, Go, Rust' (commas, semicolons or new lines). Returns (added, already_there)."""
    category, current = get_skills(category_index)
    have = {s.name.lower() for s in current}
    added: list[str] = []
    already: list[str] = []
    for raw in re.split(r"[,;\n]+", text or ""):
        name = re.sub(r"\s+", " ", raw).strip()
        if not name:
            continue
        if len(name) > 40:
            raise EditError(f"“{name[:30]}…” is too long for a skill. Separate skills with commas.")
        (already if name.lower() in have else added).append(name)
        have.add(name.lower())
    if not added and not already:
        raise EditError("I didn't find any skills in that. Send them separated by commas, like: Python, Go, Rust")
    if added:
        add_skills(AddSkillsRequest(category=category, items=added))
    return added, already


def remove_skill(category_index: int, token: str) -> str:
    category, current = get_skills(category_index)
    skill = next((s for s in current if s.token == token), None)
    if skill is None:
        raise EditError("That skill has already changed. Open the category again.")
    sk = _load_skeleton_raw()
    sk["skills"][category] = [n for n in sk["skills"][category] if n != skill.name]
    _save_skeleton_raw(sk)
    return skill.name
