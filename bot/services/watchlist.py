"""Where the daily job check looks, what it has already seen, and its settings.

A source is a career page or a job board. Job boards on Greenhouse or Lever are recognised from the link and read
through their public job-listing APIs (structured data, no scraping). Anything else is a "page": the Discoverer reads
it and picks out the job links. Boards are also found automatically from the jobs you have already sent (Greenhouse, Lever, Ashby, Workable, SmartRecruiters, Jobvite and Workday)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

from bot.services import db as _db_module
from bot.services.db import application_links

_ready = False


def _get_connection():
    """A connection with the scan's tables guaranteed to exist (created on first use, so startup order never matters)."""
    global _ready
    if not _ready:
        _db_module.init_db()
        _ready = True
    return _db_module._get_connection()


GREENHOUSE_HOSTS = ("boards.greenhouse.io", "job-boards.greenhouse.io", "boards.eu.greenhouse.io", "job-boards.eu.greenhouse.io")
LEVER_HOSTS = ("jobs.lever.co", "jobs.eu.lever.co")
ASHBY_HOSTS = ("jobs.ashbyhq.com",)
SMARTRECRUITERS_HOSTS = ("careers.smartrecruiters.com", "jobs.smartrecruiters.com")
WORKABLE_HOSTS = ("apply.workable.com",)
# Hiring systems with a public job-listing API: their boards are read as clean data, never scraped.
BOARD_HOSTS = {"greenhouse": GREENHOUSE_HOSTS, "lever": LEVER_HOSTS, "ashby": ASHBY_HOSTS,
               "smartrecruiters": SMARTRECRUITERS_HOSTS, "workable": WORKABLE_HOSTS}
BOARD_URL = {"greenhouse": "https://boards.greenhouse.io/{}", "lever": "https://jobs.lever.co/{}",
             "ashby": "https://jobs.ashbyhq.com/{}", "smartrecruiters": "https://careers.smartrecruiters.com/{}",
             "workable": "https://apply.workable.com/{}"}
BOARD_KINDS = tuple(BOARD_HOSTS)
AUTO_HISTORY, AUTO_SEARCH = 1, 2   # how an automatic source was found: from your jobs, or by the web search


@dataclass(frozen=True)
class Source:
    id: int
    url: str
    kind: str      # greenhouse | lever | ashby | smartrecruiters | workable | page
    ident: str
    label: str
    auto: int      # 0 = you added it, 1 = from the jobs you sent, 2 = found by the web search
    last_status: str = ""
    last_checked: str = ""   # UTC date and time of the last visit, "" if never


class SourceError(ValueError):
    """Something the user can fix. The message is plain language."""


def _label(ident: str) -> str:
    return ident.replace("-", " ").replace("_", " ").title()


def classify(url: str) -> tuple[str, str, str]:
    """(kind, ident, label) for a link. Boards on Greenhouse, Lever, Ashby, SmartRecruiters and Workable are recognised
    (read through their job-listing APIs); anything else is a page."""
    u = urlparse(url)
    host = (u.netloc or "").lower()
    first = next((seg for seg in u.path.split("/") if seg), "")
    if host in GREENHOUSE_HOSTS and first == "embed":   # boards.greenhouse.io/embed/job_board?for=acme
        m = re.search(r"(?:^|&)for=([\w-]+)", u.query)
        if m:
            return "greenhouse", m.group(1), _label(m.group(1))
    for kind, hosts in BOARD_HOSTS.items():
        if host in hosts and first and first not in ("embed", "api", "j"):
            return kind, first, _label(first)
    return "page", "", host.removeprefix("www.") or url


_ONE_SEGMENT_BOARDS = {"jobs.jobvite.com": "Jobvite"}
_LOCALE = re.compile(r"^[a-z]{2}([-_][A-Za-z]{2})?$")


def board_root(url: str) -> Optional[tuple[str, str]]:
    """(listing page, name) for the company board behind a job link on hiring systems without an API we read (Jobvite,
    Workday), or None. The listing page is read by the Discoverer like any career page."""
    u = urlparse(url)
    host = (u.netloc or "").lower()
    segs = [x for x in u.path.split("/") if x]
    if host in _ONE_SEGMENT_BOARDS and segs:
        return f"https://{host}/{segs[0]}", segs[0].replace("-", " ").replace("_", " ").title()
    if host.endswith(".myworkdayjobs.com"):
        site = [x for x in segs if not _LOCALE.match(x)]
        if site:
            return f"https://{host}/{site[0]}", host.split(".")[0].replace("-", " ").title()
    return None


def _clean(url: str) -> str:
    url = (url or "").strip()
    if url and not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    u = urlparse(url)
    if not u.netloc or "." not in u.netloc:
        raise SourceError("That doesn't look like a web address. Send a career page or job-board link, for example https://boards.greenhouse.io/acme")
    return url.rstrip("/")


def _row(r) -> Source:
    return Source(r["id"], r["url"], r["kind"], r["ident"], r["label"], int(r["auto"] or 0), r["last_status"] or "", r["last_checked"] or "")


def add_source(url: str, auto: int = 0, label: str = "") -> Source:
    url = _clean(url)
    kind, ident, found_label = classify(url)
    label = label or found_label
    auto = int(auto)
    if kind in BOARD_KINDS:
        url = BOARD_URL[kind].format(ident)
    with _get_connection() as conn:
        existing = conn.execute("SELECT * FROM watch_sources WHERE url = ?", (url,)).fetchone()
        if existing is None and auto and url in _removed():
            raise SourceError("You removed this one before, so it isn't added again automatically.")
        if existing:
            if existing["auto"] and not auto:   # you added it yourself: it's yours now, not an automatic one
                conn.execute("UPDATE watch_sources SET auto = 0 WHERE id = ?", (existing["id"],)); conn.commit()
            return _row(conn.execute("SELECT * FROM watch_sources WHERE id = ?", (existing["id"],)).fetchone())
        cur = conn.execute("INSERT INTO watch_sources (url, kind, ident, label, auto) VALUES (?, ?, ?, ?, ?)",
                           (url, kind, ident, label, auto))
        conn.commit()
        return _row(conn.execute("SELECT * FROM watch_sources WHERE id = ?", (cur.lastrowid,)).fetchone())


def list_sources() -> list[Source]:
    with _get_connection() as conn:
        return [_row(r) for r in conn.execute("SELECT * FROM watch_sources ORDER BY auto, id").fetchall()]


def _removed() -> list[str]:
    import json
    try:
        return json.loads(get_setting("sources_removed", "[]"))
    except ValueError:
        return []


def remove_source(source_id: int) -> bool:
    """Stop checking a page. One that was found automatically is remembered, so it isn't added back tomorrow."""
    import json
    with _get_connection() as conn:
        row = conn.execute("SELECT url, auto FROM watch_sources WHERE id = ?", (source_id,)).fetchone()
        cur = conn.execute("DELETE FROM watch_sources WHERE id = ?", (source_id,)); conn.commit()
    if row is not None and row["auto"]:
        set_setting("sources_removed", json.dumps((_removed() + [row["url"]])[-300:]))
    return cur.rowcount > 0


def retarget(source_id: int, kind: str, ident: str) -> None:
    """A career page turned out to be a board on a hiring system with an API: read it that way from now on."""
    with _get_connection() as conn:
        conn.execute("UPDATE watch_sources SET kind = ?, ident = ? WHERE id = ?", (kind, ident, source_id))
        conn.commit()


def count_auto(auto: int) -> int:
    with _get_connection() as conn:
        return conn.execute("SELECT COUNT(*) FROM watch_sources WHERE auto = ?", (auto,)).fetchone()[0]


def mark_checked(source_id: int, status: str) -> None:
    with _get_connection() as conn:
        conn.execute("UPDATE watch_sources SET last_checked = CURRENT_TIMESTAMP, last_status = ? WHERE id = ?", (status[:200], source_id))
        conn.commit()


def sources_from_history() -> int:
    """Add the job boards behind the jobs you have already sent, so the scan starts with companies you like.
    Returns how many were new."""
    before = {s.url for s in list_sources()}
    found: dict[str, str] = {}   # page -> name ("" = work it out from the link)
    for link in application_links():
        kind, ident, _ = classify(link)
        if kind in BOARD_KINDS:
            found[link] = ""
        elif (root := board_root(link)) is not None:
            found[root[0]] = root[1]
    added = 0
    for link, name in sorted(found.items()):
        try:
            src = add_source(link, auto=AUTO_HISTORY, label=name)
            added += src.url not in before
            before.add(src.url)
        except SourceError:
            continue
    return added


# ---------------------------------------------------------------- jobs already seen

WAIT_DAYS = 14   # a job not read yet stays in line for the next checks this long


def seen(url: str) -> bool:
    """Already dealt with (read, tailored, filtered, ...). A job still waiting in line to be read is not."""
    with _get_connection() as conn:
        return conn.execute("SELECT 1 FROM seen_jobs WHERE url = ? AND status != 'waiting'", (url,)).fetchone() is not None


def known(url: str) -> bool:
    """Seen in any earlier check, including one still waiting to be read."""
    with _get_connection() as conn:
        return conn.execute("SELECT 1 FROM seen_jobs WHERE url = ?", (url,)).fetchone() is not None


def expire_waiting(days: int = WAIT_DAYS) -> int:
    """Jobs that waited in line this long without making the cut are let go (so the line can't grow forever)."""
    with _get_connection() as conn:
        cur = conn.execute("UPDATE seen_jobs SET status = 'low-fit', reason = 'waited too long without making the cut' "
                           "WHERE status = 'waiting' AND first_seen < datetime('now', ?)", (f"-{int(days)} days",))
        conn.commit()
        return cur.rowcount


def mark_seen(url: str, title: str = "", company: str = "", status: str = "new", fit: float = 0.0, reason: str = "") -> None:
    with _get_connection() as conn:
        conn.execute(
            """INSERT INTO seen_jobs (url, title, company, status, fit, reason) VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(url) DO UPDATE SET title = excluded.title, company = excluded.company, status = excluded.status,
                                              fit = excluded.fit, reason = excluded.reason""",
            (url, title[:200], company[:120], status, fit, reason[:300]))
        conn.commit()


# ---------------------------------------------------------------- settings

def get_setting(key: str, default: str = "") -> str:
    with _get_connection() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with _get_connection() as conn:
        conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
        conn.commit()


def scan_paused() -> bool:
    return get_setting("scan_paused", "0") == "1"


def owner_chat_id() -> Optional[int]:
    v = get_setting("owner_chat_id", "")
    return int(v) if v.lstrip("-").isdigit() else None


# ---------------------------------------------------------------- what you are looking for (the daily check's filters)

INTERN_WORDS = ("intern", "internship", "industrial training", "industrial attachment", "trainee", "placement",
                "co-op", "coop", "student", "latihan industri", "praktikal", "undergraduate")
PLACE_ALIASES = {
    "malaysia": ("malaysia", "kuala lumpur", "selangor", "penang", "pulau pinang", "johor", "cyberjaya", "petaling jaya",
                 "putrajaya", "shah alam", "subang", "melaka", "malacca", "ipoh", "perak", "kuching", "sarawak", "sabah",
                 "kota kinabalu", "negeri sembilan", "seremban", "kedah", "pahang", "terengganu", "kelantan", "puchong",
                 "bangsar", "mont kiara", "klang", "bayan lepas", "my-", ", my"),
    "kuala lumpur": ("kuala lumpur", "kl", "klcc", "bangsar", "mont kiara", "petaling jaya", "cyberjaya", "selangor"),
    "remote": ("remote", "anywhere", "work from home", "wfh", "distributed"),
    "singapore": ("singapore", ", sg"),
}


@dataclass
class Prefs:
    roles: list[str]          # what to search for, e.g. "software engineer intern"
    places: list[str]         # e.g. "Malaysia", "Remote"; empty = anywhere
    interns_only: bool        # skip full-time and senior roles
    web_search: bool          # also search the web for postings on common hiring systems


def _split(value: str) -> list[str]:
    return [p.strip() for p in re.split(r"[,;\n]", value or "") if p.strip()]


def get_prefs() -> Prefs:
    return Prefs(roles=_split(get_setting("scan_roles", ""))[:5],
                 places=_split(get_setting("scan_places", "Malaysia, Remote"))[:4],
                 interns_only=get_setting("scan_interns_only", "1") == "1",
                 web_search=get_setting("scan_web_search", "1") == "1")


def set_list(key: str, text: str) -> list[str]:
    items = _split(text)[:5]
    set_setting(key, ", ".join(items))
    return items


def toggle(key: str, default: str = "1") -> bool:
    on = get_setting(key, default) != "1"
    set_setting(key, "1" if on else "0")
    return on


def is_intern_title(text: str) -> bool:
    low = (text or "").lower()
    return any(re.search(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])", low) for w in INTERN_WORDS) or "internship" in low


SENIOR = re.compile(r"\b(senior|sr\.?|lead|principal|staff|head of|director|manager|vp|chief|architect)\b", re.I)


def title_ok(title: str, prefs: Prefs, snippet: str = "") -> tuple[bool, str]:
    """(keep?, why not). Checked before anything is read in full, so it must be cheap."""
    if not prefs.interns_only:
        return True, ""
    if is_intern_title(title):
        return True, ""
    if SENIOR.search(title or ""):
        return False, "not an internship"
    # titles like "Software Engineer (Internship programme)" sometimes only say it in the description
    return (True, "") if is_intern_title(snippet[:600]) else (False, "not an internship")


def place_ok(location: str, prefs: Prefs) -> bool:
    """True if the location matches one of your places, or if the location is unknown (it is checked again later)."""
    loc = (location or "").lower().strip()
    if not prefs.places or not loc:
        return True
    for place in prefs.places:
        p = place.lower().strip()
        for alias in PLACE_ALIASES.get(p, (p,)):
            if re.search(r"(?<![a-z])" + re.escape(alias.strip()) + r"(?![a-z])", loc) if alias.strip().isalpha() or " " in alias.strip() else alias in loc:
                return True
    return False
