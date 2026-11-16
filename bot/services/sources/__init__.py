"""Job sources that are searched with your keywords (LinkedIn, JobStreet, Hiredly, the open web).

``search_all`` runs every source that is switched on for every keyword, one request at a time with short pauses, and
reports how each source did. Each source keeps a small health record (shown in ⚙️ Status): a source that fails three
times in a row is left alone for a day, so a changed or blocking site never slows the daily check. One source failing
never stops the others."""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass

from bot.services import watchlist
from bot.services.sources import base
from bot.services.sources.base import Candidate, KeywordSource, SourceError
from bot.services.sources.hiredly import Hiredly
from bot.services.sources.jobstreet import JobStreet
from bot.services.sources.linkedin import LinkedIn
from bot.services.sources.openweb import OpenWeb

logger = logging.getLogger(__name__)

ALL: list[KeywordSource] = [LinkedIn(), JobStreet(), Hiredly(), OpenWeb()]   # in this order
PER_KEYWORD = 25        # most results taken from one source for one keyword
MAX_KEYWORDS = 4
FAILS_BEFORE_REST = 3
REST_SECONDS = 24 * 3600
MALAY_TERM = "latihan industri"   # "industrial training": what Malaysian universities call a compulsory internship


@dataclass
class SearchReport:
    key: str
    name: str
    ok: bool
    found: int = 0
    reason: str = ""
    skipped: bool = False      # rested after repeated failures


# ---------------------------------------------------------------- which sources are on

def is_on(key: str) -> bool:
    return watchlist.get_setting(f"src_{key}", "1") == "1"


def enabled() -> list[KeywordSource]:
    return [s for s in ALL if is_on(s.key)]


def switch(key: str) -> bool:
    """Flip a source on or off. Returns whether it is on now."""
    return watchlist.toggle(f"src_{key}")


# ---------------------------------------------------------------- health

def health(key: str) -> dict:
    try:
        return json.loads(watchlist.get_setting(f"src_health_{key}", "{}")) or {}
    except ValueError:
        return {}


def _record(key: str, ok: bool, text: str) -> None:
    h = health(key)
    h["fails"] = 0 if ok else int(h.get("fails", 0)) + 1
    h["last_try"] = time.time()
    h["status"] = text
    h["when"] = time.strftime("%d %b %H:%M")
    if ok:
        h["last_ok"] = h["when"]
    watchlist.set_setting(f"src_health_{key}", json.dumps(h))


def resting(key: str) -> bool:
    h = health(key)
    return int(h.get("fails", 0)) >= FAILS_BEFORE_REST and time.time() - float(h.get("last_try", 0)) < REST_SECONDS


def status_lines() -> list[str]:
    """One plain line per source for ⚙️ Status."""
    lines = []
    for s in ALL:
        if not is_on(s.key):
            lines.append(f"{s.name}: off")
            continue
        h = health(s.key)
        lines.append(f"{s.name}: {h['status']} ({h['when']})" if h.get("status") else f"{s.name}: not run yet")
    return lines


# ---------------------------------------------------------------- keywords and places

def keywords(prefs) -> list[str]:
    """What to search for: your roles (or the roles on your resume), plus one Malay form for internships."""
    from bot.services.websearch import default_roles
    roles = list(prefs.roles) or default_roles()
    words = roles[:MAX_KEYWORDS - 1] if prefs.interns_only else roles[:MAX_KEYWORDS]
    if prefs.interns_only and roles:
        base_role = " ".join(w for w in roles[0].split() if w.lower() not in ("intern", "internship", "trainee"))
        if base_role:
            words.append(f"{base_role} {MALAY_TERM}")
    seen, out = set(), []
    for w in words:
        if w.lower() not in seen:
            seen.add(w.lower())
            out.append(w)
    return out[:MAX_KEYWORDS]


def search_places(prefs) -> list[str]:
    """Where to look: the places you chose (Remote isn't a place to search); Malaysia as a whole if you chose none."""
    places = [p for p in prefs.places if p.lower() != "remote"][:2]
    return places or ["Malaysia"]


# ---------------------------------------------------------------- running them

async def search_all(prefs) -> tuple[list[Candidate], list[SearchReport]]:
    """Search every switched-on source with every keyword. Returns (all candidates, one report per source)."""
    cands: list[Candidate] = []
    reports: list[SearchReport] = []
    words, places = keywords(prefs), search_places(prefs)
    if not words:
        return cands, reports
    for src in enabled():
        if resting(src.key):
            reports.append(SearchReport(src.key, src.name, False, 0, "resting after repeated problems; I'll try again tomorrow", True))
            continue
        found: dict[str, Candidate] = {}
        answered, last_reason = 0, ""
        for i, word in enumerate(words):
            if i:
                await base.pause()
            try:
                for c in await src.search(word, places, PER_KEYWORD, prefs.interns_only):
                    found.setdefault(c.url, c)
                answered += 1
            except SourceError as err:
                last_reason = str(err)
            except Exception as err:   # a bug or a surprise in one source must never stop the others
                logger.warning("Source %s crashed: %s", src.name, err)
                last_reason = "it answered in a way I couldn't read"
        if not answered:
            _record(src.key, False, f"didn't answer ({last_reason})")
            reports.append(SearchReport(src.key, src.name, False, 0, last_reason))
        elif not found:
            _record(src.key, False, "no results (the site may have changed, or is blocking this server)")
            reports.append(SearchReport(src.key, src.name, False, 0, "no results today"))
        else:
            _record(src.key, True, f"ok, {len(found)} jobs")
            cands += list(found.values())
            reports.append(SearchReport(src.key, src.name, True, len(found)))
    return cands, reports
