"""The Curator: keeps your saved experience tidy and up to date.

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
    in the original (the same check the one-page fitting uses).

With your GitHub username set, it also keeps your profile in step with your public repos:
  * automatically: a tool your repos use that your skills list lacks is added, always with one bullet point that shows it,
    under the matching project (or a new project made from that repo);
  * as suggestions: a project's tech stack line updated with the tools its repo uses, and its bullet points reworded to
    match what the repo shows (using only facts from the old bullet point or the repo)."""
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
MAX_REWRITES = 30


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

def _suggestions(sk: dict, facts: list[dict], rewrite, already_covered=None, review_ids: Optional[set] = None) -> list[Suggestion]:
    """``already_covered(tools, skills) -> set`` (AI) catches tools listed under a broader name the catalog can't see.
    ``review_ids``: bullet points to review for wording even if they aren't long or weak (every project bullet point
    that has no repo to compare against)."""
    out: list[Suggestion] = []
    skill_items = [i for items in sk.get("skills", {}).values() for i in items]
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
            if proper and not tools_catalog.covered_by(proper, skill_items) and tools_catalog.mentions(proper, f["text"]):
                missing.setdefault(proper, f["text"])
    if missing and already_covered:
        try:
            for tool in already_covered(list(missing), sk.get("skills", {})) or set():
                missing.pop(tool, None)
        except Exception as err:
            logger.info("Skill coverage check skipped: %s", err)
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
    review = review_ids or set()
    flagged = {f["id"]: f["text"] for f in facts if f["id"] not in dup_ids and
               (f["id"] in review or len(f["text"]) > LONG_BULLET or f["text"].lower().startswith(WEAK_OPENERS))}
    if flagged and rewrite:
        from service.latex.fit import adds_new_facts
        items = dict(list(flagged.items())[:MAX_REWRITES])
        for fid, new in (rewrite(items) or {}).items():
            old = items.get(fid)
            if not old or not new or new.strip() == old.strip() or adds_new_facts(old, new) or len(new) > max(len(old), LONG_BULLET):
                continue
            out.append(Suggestion(_sid("rw", fid, new), "rewrite", f"Reword:\n“{old}”\n→ “{new}”", {"fact_id": fid, "old": old, "new": new}))
    return out


def _covered_with_ai(tools: list[str], skills: dict[str, list[str]]) -> set[str]:
    """Blocking. Which of these tools the skills list already covers, perhaps under a broader name ("LLM APIs" covers
    "OpenAI API"). Fails open: on an error nothing is treated as covered."""
    from pydantic import BaseModel
    from service.clients import instructor_client

    class _Out(BaseModel):
        already_covered: list[str]
    try:
        r: _Out = instructor_client.chat.completions.create(
            model=config.EXTRACTOR_MODEL, response_model=_Out,
            messages=[{"role": "system", "content": "Given a resume's skills list and some tools, return the tools that the skills "
                       "list already covers, even under a broader or differently written name. Only return names from the tool list."},
                      {"role": "user", "content": "Skills:\n" + "\n".join(f"{c}: {', '.join(i)}" for c, i in skills.items())
                       + "\n\nTools:\n" + "\n".join(tools)}])
        return {t for t in r.already_covered if t in tools}
    except Exception as err:
        logger.info("Coverage check skipped: %s", err)
        return set()


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
                "Review each resume bullet point. Rewrite it only if it can be clearer or stronger: a strong past-tense action "
                "verb first (no 'Responsible for', 'Helped', 'Worked on'), the result or number early where there is one, one "
                "sentence under 200 characters. Use only facts already in that bullet: never add a number, technology, name or "
                "claim. Leave out of your answer any bullet that is already good.")},
                {"role": "user", "content": "\n".join(f"{k}: {v}" for k, v in items.items())}])
        return {i.key: i.text.strip() for i in r.items if i.key in items}
    except Exception as err:
        logger.info("Rewrites skipped: %s", err)
        return {}


# ---------------------------------------------------------------- running and applying

def tidy(rewrite=_rewrite_with_ai, github: bool = True, already_covered=_covered_with_ai) -> TidyReport:
    """Make the safe fixes now (with a backup first), add tools from your GitHub repos that your profile is missing
    (each with one bullet point), and return suggestions for the rest, including updates from your repos."""
    from service import github_evidence
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
    use_github = github and bool(config.GITHUB_USERNAME)
    if use_github:
        if report.backup is None:
            report.backup = backup_current()
        try:
            for a in github_evidence.sync_from_github():
                report.fixed.append(f"Added from GitHub: {a.label()}")
        except Exception as err:
            logger.warning("GitHub sync skipped: %s", err)
        sk, facts = _load_skeleton_raw(), _load_facts_raw()
    # every project bullet point is reviewed: against its repo when there is one (below), otherwise for wording here
    repo_projects: set[str] = set()
    if use_github:
        idx = github_evidence.load_index()
        repo_names = [r["name"] for r in idx.get("repos", {}).values()]
        repo_projects = {p["id"] for p in sk.get("project_entries", []) if any(github_evidence.names_match(n, p["name"]) for n in repo_names)}
    review = {f["id"] for f in facts if f.get("section") == "project" and f["entry_id"] not in repo_projects}
    report.suggestions = _suggestions(sk, facts, rewrite, already_covered, review)
    if use_github:
        try:
            ups = github_evidence.project_updates(sk, facts)
            from_repo = {u["fact_id"] for u in ups if u.get("fact_id")}
            # a bullet point reworded from its repo replaces a plain rewording of the same bullet point
            report.suggestions = [x for x in report.suggestions if not (x.kind == "rewrite" and x.data.get("fact_id") in from_repo)]
            for u in ups:
                key = u.get("fact_id") or u["entry_id"]
                data = {k: u[k] for k in ("fact_id", "entry_id", "old", "new") if k in u}
                report.suggestions.append(Suggestion(_sid(u["kind"], key, u["new"]), u["kind"], u["text"], data))
        except Exception as err:
            logger.warning("Project updates skipped: %s", err)
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
    if s.kind == "update_stack":
        p = next((x for x in sk.get("project_entries", []) if x["id"] == d["entry_id"]), None)
        if p is None or p.get("tech_stack", "") != d["old"]:
            return "That project changed since, so I left it"
        p["tech_stack"] = d["new"]
        _save_skeleton_raw(sk)
        return f"Updated the tech stack of {p['name']}"
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
