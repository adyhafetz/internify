"""One way to browse and edit everything the user has saved: education, jobs, projects and leadership.

Jobs and projects keep their bullet points as "facts" (with technology tags). Leadership bullets and education
honors are plain lists in the profile. Callers (the Telegram browser) don't need to know that difference: every
entry is an ``EntryView`` with numbered ``Item`` s, and the same five operations work on all of them.

Entries and items are addressed with short references (``kind`` + ``ref``, and an item ``token``) so they fit in
Telegram buttons; users never see or type internal IDs."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Optional

from service.admin import (
    NewFactBullet,
    UpdateFactRequest,
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


def find_entries(query: str) -> list[EntrySummary]:
    """Match what the user typed against names (all words must appear), or an exact internal id as a shortcut."""
    q = (query or "").strip().lower()
    if not q:
        return []
    sk = _load_skeleton_raw()
    entries = list_entries()
    exact = [e for e in entries if e.kind != LEAD and e.ref.lower() == q]
    if exact:
        return exact
    words = q.split()
    out = []
    for e in entries:
        raw = _find_entry(sk, e.kind, e.ref) or {}
        haystack = (e.label + " " + " ".join(str(v) for v in raw.values() if isinstance(v, str))).lower()
        if all(w in haystack for w in words):
            out.append(e)
    return out


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
