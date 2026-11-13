"""The Curator: keeps your saved experience tidy.

Safe, mechanical fixes are made automatically (a backup is kept first):
  * the same skill listed twice, or under two spellings ("React" and "React.js") -> kept once, under its proper name;
  * tool tags on bullet points spelled several ways -> one spelling, no repeats;
  * the exact same bullet point saved twice under one entry -> kept once.

Anything that changes meaning becomes a suggestion you approve or skip:
  * things in your skills list that aren't technical skills ("Teamwork");
  * a skill in the wrong category ("Docker" under Languages);
  * tools your bullet points mention that your skills list doesn't;
  * bullet points that are nearly the same;
  * long or weakly worded bullet points, rewritten more tightly. A rewrite may never add a number, tool or name that wasn't
    in the original (the same check the one-page fitting uses)."""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Optional

from service import config, tools_catalog
from service.profile_data import SKILL_CATEGORIES, _evict_from_cache, _load_facts_raw, _load_skeleton_raw, _save_facts_raw, _save_skeleton_raw

logger = logging.getLogger(__name__)

WEAK_OPENERS = ("responsible for", "helped", "worked on", "assisted", "was involved", "involved in", "tasked with", "participated in")
LONG_BULLET = 220
MAX_REWRITES = 8


@dataclass
class Suggestion:
    id: str
    kind: str            # remove_skill | move_skill | add_skill | drop_duplicate | rewrite
    text: str            # what the user reads
    data: dict = field(default_factory=dict)


@dataclass
class TidyReport:
    fixed: list[str] = field(default_factory=list)          # done automatically
    suggestions: list[Suggestion] = field(default_factory=list)
    backup: Optional[str] = None


def _sid(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:8]


def _words(t: str) -> set[str]:
    return set(re.findall(r"[a-z0-9+#.]{2,}", t.lower()))


# ---------------------------------------------------------------- automatic fixes

def _safe_fixes(sk: dict, facts: list[dict]) -> tuple[list[str], list[str]]:
    """Returns (what was fixed, ids of bullet points whose stored data changed)."""
    fixed: list[str] = []
    seen: set[str] = set()
    for cat in list(sk.get("skills", {})):
        clean: list[str] = []
        for item in sk["skills"][cat]:
            proper = tools_catalog.canonical(item) or item.strip()
            k = tools_catalog.key(proper)
            if not k:
                continue
            if k in seen:
                fixed.append(f"Removed a repeated skill: {item}")
                continue
            if proper != item:
                fixed.append(f"Renamed skill {item} → {proper}")
            seen.add(k)
            clean.append(proper)
        sk["skills"][cat] = clean
    changed: list[str] = []
    retagged = 0
    by_entry: dict[str, set[str]] = {}
    keep: list[dict] = []
    for f in facts:
        tags, keys = [], set()
        for t in f.get("tech_tags", []):
            proper = tools_catalog.canonical(t) or t.strip()
            if proper and tools_catalog.key(proper) not in keys:
                keys.add(tools_catalog.key(proper))
                tags.append(proper)
        if tags != f.get("tech_tags", []):
            f["tech_tags"] = tags
            retagged += 1
            changed.append(f["id"])
        norm = " ".join(f["text"].lower().split())
        if norm in by_entry.setdefault(f["entry_id"], set()):
            fixed.append(f"Removed a bullet point saved twice: {f['text'][:60]}")
            changed.append(f["id"])
            continue
        by_entry[f["entry_id"]].add(norm)
        keep.append(f)
    facts[:] = keep
    if retagged:
        fixed.append(f"Tidied the tool tags on {retagged} bullet point{'s' if retagged != 1 else ''}")
    return fixed, changed


# ---------------------------------------------------------------- suggestions

def _suggestions(sk: dict, facts: list[dict], rewrite) -> list[Suggestion]:
    out: list[Suggestion] = []
    have = {tools_catalog.key(i): (cat, i) for cat, items in sk.get("skills", {}).items() for i in items}
    for cat, items in sk.get("skills", {}).items():
        for item in items:
            if tools_catalog.is_soft_skill(item):
                out.append(Suggestion(_sid("rm", item), "remove_skill", f"Remove “{item}” from your skills (it isn't a technical skill)", {"item": item}))
                continue
            right = tools_catalog.category(item)
            if right and right != cat and right in SKILL_CATEGORIES:
                out.append(Suggestion(_sid("mv", item, right), "move_skill", f"Move “{item}” from {cat} to {right}", {"item": item, "to": right}))
    missing: dict[str, str] = {}
    for f in facts:
        for t in f.get("tech_tags", []):
            proper = tools_catalog.canonical(t)
            if proper and tools_catalog.key(proper) not in have and tools_catalog.mentions(proper, f["text"]):
                missing.setdefault(proper, f["text"])
    for tool, example in missing.items():
        out.append(Suggestion(_sid("add", tool), "add_skill", f"Add “{tool}” to your skills (your bullet points mention it)",
                              {"item": tool, "category": tools_catalog.category(tool) or tools_catalog.DEV}))
    by_entry: dict[str, list[dict]] = {}
    for f in facts:
        by_entry.setdefault(f["entry_id"], []).append(f)
    dup_ids: set[str] = set()
    for items in by_entry.values():
        for i, a in enumerate(items):
            for b in items[i + 1:]:
                wa, wb = _words(a["text"]), _words(b["text"])
                if wa and wb and len(wa & wb) / len(wa | wb) >= 0.75:
                    drop = a if len(a["text"]) < len(b["text"]) else b
                    keep = b if drop is a else a
                    if drop["id"] in dup_ids:
                        continue
                    dup_ids.add(drop["id"])
                    out.append(Suggestion(_sid("dup", drop["id"]), "drop_duplicate",
                                          f"Remove a near-duplicate bullet point: “{drop['text'][:90]}” (you also have “{keep['text'][:60]}…”)",
                                          {"fact_id": drop["id"]}))
    flagged = {f["id"]: f["text"] for f in facts if f["id"] not in dup_ids and
               (len(f["text"]) > LONG_BULLET or f["text"].lower().startswith(WEAK_OPENERS))}
    if flagged and rewrite:
        from service.latex.fit import adds_new_facts
        items = dict(list(flagged.items())[:MAX_REWRITES])
        for fid, new in (rewrite(items) or {}).items():
            old = items.get(fid)
            if not old or not new or new.strip() == old.strip() or adds_new_facts(old, new) or len(new) > max(len(old), LONG_BULLET):
                continue
            out.append(Suggestion(_sid("rw", fid, new), "rewrite", f"Reword:\n“{old}”\n→ “{new}”", {"fact_id": fid, "old": old, "new": new}))
    return out


def _rewrite_with_ai(items: dict[str, str]) -> dict[str, str]:
    """Blocking. fact id -> tighter wording. Fails open."""
    from pydantic import BaseModel
    from service.clients import instructor_client

    class _Item(BaseModel):
        key: str
        text: str

    class _Out(BaseModel):
        items: list[_Item]
    try:
        r: _Out = instructor_client.chat.completions.create(
            model=config.GENERATOR_MODEL, response_model=_Out,
            messages=[{"role": "system", "content": (
                "Improve each resume bullet point: start with a strong past-tense action verb (no 'Responsible for', 'Helped', "
                "'Worked on'), keep the result or number first where there is one, and keep it under 200 characters. Use only "
                "facts already in that bullet: never add a number, technology, name or claim. Return every key.")},
                {"role": "user", "content": "\n".join(f"{k}: {v}" for k, v in items.items())}])
        return {i.key: i.text.strip() for i in r.items if i.key in items}
    except Exception as err:
        logger.info("Rewrites skipped: %s", err)
        return {}


# ---------------------------------------------------------------- running and applying

def tidy(rewrite=_rewrite_with_ai) -> TidyReport:
    """Make the safe fixes now (with a backup first) and return suggestions for the rest."""
    from service.importer import backup_current
    sk, facts = _load_skeleton_raw(), _load_facts_raw()
    report = TidyReport()
    before = (repr(sk.get("skills")), repr(facts))
    report.fixed, changed = _safe_fixes(sk, facts)
    if (repr(sk.get("skills")), repr(facts)) != before:
        report.backup = backup_current()
        _save_skeleton_raw(sk)
        _save_facts_raw(facts)
        _evict_from_cache(changed)
    report.suggestions = _suggestions(sk, facts, rewrite)
    return report


def apply(s: Suggestion) -> str:
    """Do one approved suggestion. Returns a short confirmation, or why it couldn't be done (things may have changed)."""
    sk, facts = _load_skeleton_raw(), _load_facts_raw()
    d = s.data
    if s.kind in ("remove_skill", "move_skill"):
        for cat, items in sk["skills"].items():
            if d["item"] in items:
                items.remove(d["item"])
                if s.kind == "move_skill":
                    sk["skills"].setdefault(d["to"], []).append(d["item"])
                _save_skeleton_raw(sk)
                return ("Removed " if s.kind == "remove_skill" else "Moved ") + d["item"]
        return f"{d['item']} is no longer in your skills"
    if s.kind == "add_skill":
        cat = d["category"] if d["category"] in SKILL_CATEGORIES else tools_catalog.DEV
        if any(tools_catalog.key(i) == tools_catalog.key(d["item"]) for items in sk["skills"].values() for i in items):
            return f"{d['item']} is already in your skills"
        sk["skills"].setdefault(cat, []).append(d["item"])
        _save_skeleton_raw(sk)
        return f"Added {d['item']} to {cat}"
    f = next((x for x in facts if x["id"] == d["fact_id"]), None)
    if f is None:
        return "That bullet point is no longer there"
    if s.kind == "drop_duplicate":
        facts.remove(f)
        msg = "Removed the near-duplicate"
    else:
        if f["text"] != d["old"]:
            return "That bullet point was edited since, so I left it"
        f["text"] = d["new"]
        msg = "Reworded it"
    _save_facts_raw(facts)
    _evict_from_cache([d["fact_id"]])
    return msg


def to_json(suggestions: list[Suggestion]) -> list[dict]:
    return [asdict(s) for s in suggestions]


def from_json(items: list[dict]) -> list[Suggestion]:
    return [Suggestion(**i) for i in items]
