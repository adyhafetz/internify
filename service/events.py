"""Lightweight in-process event bus for the agent pipeline.

Pipeline code calls ``emit(agent, kind, message, ...)``. Whoever is listening
(the Telegram progress message, the web dashboard, a trace file) receives the
same events. Outside of a run, or when nobody listens, ``emit`` is a cheap no-op,
so the service keeps working exactly as before.
"""
from __future__ import annotations

import contextvars
import json
import os
import threading
import time
import uuid
from typing import Any, Callable, Optional

# ---------- The six visible agents ----------

AGENTS: dict[str, dict[str, str]] = {
    "scout":     {"name": "Scout",     "role": "Reads the job page",             "emoji": "🔭"},
    "analyst":   {"name": "Analyst",   "role": "Extracts requirements",          "emoji": "🧠"},
    "archivist": {"name": "Archivist", "role": "Retrieves your verified facts",  "emoji": "📚"},
    "scribe":    {"name": "Scribe",    "role": "Writes tailored bullets",        "emoji": "✍️"},
    "critic":    {"name": "Critic",    "role": "Checks claims and scores fit",   "emoji": "🛡️"},
    "publisher": {"name": "Publisher", "role": "Builds PDF, files it, logs it",  "emoji": "📎"},
    # The morning scan: these two work before the six above, on a schedule
    "discoverer": {"name": "Discoverer", "role": "Finds new jobs for you",       "emoji": "🔎"},
    "matcher":    {"name": "Matcher",    "role": "Picks the best fits",          "emoji": "🎯"},
}

# The six agents that work on one job, in order (the Telegram progress line counts these). The two scan agents
# (Discoverer, Matcher) work before them, once a morning.
PIPELINE = ["scout", "analyst", "archivist", "scribe", "critic", "publisher"]

# Event kinds
START = "start"        # agent begins work
PROGRESS = "progress"  # agent reports something while working
DONE = "done"          # agent finished its step
FAIL = "fail"          # agent hit an error
HANDOFF = "handoff"    # agent passes something to another agent (``to`` is set)
ASK = "ask"            # agent needs input from the user (reserved for later)
RUN = "run"            # run-level lifecycle: data["state"] in {"started","finished","failed"}

MAX_RUNS_IN_MEMORY = 20

_current_run: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("internify_run", default=None)


class EventBus:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runs: dict[str, list[dict[str, Any]]] = {}
        self._order: list[str] = []
        self._subs: list[Callable[[dict[str, Any]], None]] = []

    # --- subscription ---
    def subscribe(self, callback: Callable[[dict[str, Any]], None]) -> Callable[[], None]:
        with self._lock:
            self._subs.append(callback)

        def unsubscribe() -> None:
            with self._lock:
                if callback in self._subs:
                    self._subs.remove(callback)

        return unsubscribe

    # --- publishing ---
    def publish(self, run_id: str, event: dict[str, Any]) -> None:
        with self._lock:
            events = self._runs.setdefault(run_id, [])
            if run_id not in self._order:
                self._order.append(run_id)
                while len(self._order) > MAX_RUNS_IN_MEMORY:
                    self._runs.pop(self._order.pop(0), None)
            event["seq"] = len(events)
            say = describe(event)
            if say:
                event["say"] = say
            events.append(event)
            subs = list(self._subs)
        for cb in subs:
            try:
                cb(event)
            except Exception:
                pass  # a broken listener must never break the pipeline

    # --- reading ---
    def get_run(self, run_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._runs.get(run_id, []))

    def list_runs(self) -> list[dict[str, Any]]:
        with self._lock:
            out = []
            for rid in reversed(self._order):
                evs = self._runs.get(rid, [])
                head = next((e for e in evs if e["kind"] == RUN), None)
                out.append({
                    "run_id": rid,
                    "events": len(evs),
                    "started_at": evs[0]["ts"] if evs else None,
                    "url": (head or {}).get("data", {}).get("url"),
                })
            return out

    def latest_run_id(self) -> Optional[str]:
        with self._lock:
            return self._order[-1] if self._order else None


bus = EventBus()

# How the Scout got the posting when it was not by simply opening the page (wording for each route)
_GOT = {"scrapingant": "Got the posting through a scraping service", "reader": "Got the posting through a page-rendering service",
        "archive": "Got a saved copy of the posting from the web archive", "search": "Found the same posting on another site",
        "pasted": "Using the text you pasted"}
_READ = {"scrapingant": "Read the posting through a scraping service", "reader": "Read the posting through a page-rendering service",
         "archive": "Read a saved copy of the posting from the web archive", "search": "Read the same posting from another site",
         "pasted": "Read the text you pasted"}


def describe(ev: dict[str, Any]) -> Optional[str]:
    """One plain-language line for an event, with its numbers. Shared by Telegram and the dashboard,
    so the wording lives in one place. Returns None for events that say nothing (hand-offs, run markers)."""
    a, k, d, m = ev.get("agent"), ev.get("kind"), ev.get("data") or {}, ev.get("message") or ""
    if k in (HANDOFF, RUN):
        return None
    if k == ASK:
        if a == "scout":
            return "Asking you to paste the job text… (check Telegram)"
        return f"Asking you about {', '.join(d.get('skills', []))}… (check Telegram)"

    def n(word: str, count: int) -> str:
        return f"{count} {word}{'' if count == 1 else 's'}"

    if a == "scout":
        if k == PROGRESS and d.get("status"):
            label, i, total, status = str(d.get("label", "")), int(d.get("attempt", 1)), int(d.get("total", 1)), d["status"]
            if status == "trying": return "Opening the job page…" if i == 1 else f"Trying another way in: {label}… ({i} of {total})"
            if status == "failed":
                return f"{label[:1].upper()}{label[1:]} didn't work: {d.get('reason')}." + (" Trying another way in…" if i < total else "")
            if status == "ok": return None if i == 1 else _GOT.get(d.get("method"), f"Got the posting from {label}")
            if status == "skipped": return "Out of time, stopping the search"
        if k == START: return "Opening the job page…"
        if k == PROGRESS:
            return "That didn't work, trying another way in…" if ("switching" in m.lower() or "failed" in m.lower()) else "Fetching the page…"
        if k == DONE:
            chars = f"{int(d.get('chars', 0)):,}"
            if d.get("via_fallback"):
                return f"{_READ.get(d.get('method'), 'Read the posting from ' + str(d.get('label')))} ({chars} characters)"
            return f"Read the whole posting ({chars} characters)"
        if k == FAIL: return "Couldn't read this page by any route"
    elif a == "discoverer":
        if k == START: return f"Checking your {n('job source', int(d.get('sources', 0)))}…"
        if k == PROGRESS and d.get("status") == "trying": return f"Checking {d.get('label', 'a source')}…"
        if k == PROGRESS and d.get("status") == "ok":
            return f"{d.get('label', 'A source')}: {n('new job', int(d.get('found', 0)))}"
        if k == PROGRESS and d.get("status") == "failed":
            return f"{d.get('label', 'A source')} didn't answer: {d.get('reason', 'unknown reason')}"
        if k == DONE: return f"Found {n('new job', int(d.get('found', 0)))} across {n('source', int(d.get('sources', 0)))}"
        if k == FAIL: return d.get("why") or "Couldn't find any jobs"
    elif a == "matcher":
        if k == START: return f"Scoring {n('new job', int(d.get('total', 0)))} against your experience…"
        if k == PROGRESS and d.get("reading"): return f"Reading the {int(d['reading'])} best in detail…"
        if k == DONE:
            top = f" · best fit {round(float(d['top']) * 100)}%" if d.get("top") is not None else ""
            return f"Kept {int(d.get('kept', 0))} of {int(d.get('total', 0))}{top}"
        if k == FAIL: return d.get("why") or "Couldn't score the jobs"
    elif a == "analyst":
        if k == START: return "Working out exactly what the job wants…"
        if k == PROGRESS and d.get("company"): return f"This is a {d.get('role')} role at {d.get('company')}"
        if k == DONE:
            return f"The job wants {n('tool', len(d.get('core_tech_stack', [])))} and {n('skill', len(d.get('hard_requirements', [])))}"
        if k == FAIL: return "Couldn't understand this posting"
    elif a == "archivist":
        if k == PROGRESS and d.get("saved"): return f"Saved your {d.get('skill')} experience ✓"
        if k == PROGRESS and d.get("answer") == "yes": return f"You've used {d.get('skill')}. Let's add it"
        if k == PROGRESS and d.get("answer") == "no": return f"No problem, skipping {d.get('skill')}"
        if k == PROGRESS and d.get("answer") == "timeout": return f"No answer about {d.get('skill')}, moving on"
        if k == START:
            digits = "".join(ch if ch.isdigit() else " " for ch in m).split()
            return f"Searching your {n('saved experience', int(digits[0]))}…" if digits else "Searching your saved experiences…"
        if k == DONE:
            covered, total = len(d.get("covered", [])), len(d.get("covered", [])) + len(d.get("missing", []))
            new = sum(1 for f in d.get("facts", []) if f.get("new"))
            return f"Found {len(d.get('facts', []))} that fit{f' ({new} new from you)' if new else ''} · {covered} of {total} needs matched"
        if k == FAIL: return "Your saved experience is empty. Add some in /profile first"
    elif a == "scribe":
        if k == START:
            return "Rewriting after the Critic's feedback…" if int(d.get("iteration", 1)) > 1 else "Writing resume lines from your real experience…"
        if k == DONE: return f"Draft {d.get('iteration')} written · {n('line', len(d.get('bullets', [])))}"
        if k == FAIL: return "Couldn't write the resume"
    elif a == "critic":
        if k == START: return "Checking every line is true…"
        if k == DONE:
            bad, score = len(d.get("unsupported", [])), round(float(d.get("score", 0)))
            return f"Caught {n('invented claim', bad)} · match {score}%" if bad else f"Every line verified ✓ · match {score}%"
    elif a == "publisher":
        if d.get("duplicate") is True: return "This job is already in your tracker"
        if d.get("duplicate") is False: return "Checked: new job, not applied to before"
        if k == START: return "Building your 1-page PDF…"
        if d.get("repair_attempt"): return "Spotted a formatting error and fixed it by itself"
        if m == "PDF compiled": return "PDF built ✓"
        if m.startswith("Uploading"): return "Saving to Google Drive…"
        if m.startswith("Uploaded"): return "Saved to Google Drive ✓"
        if "not configured" in m: return "Google Drive not set up, kept a local copy"
        if k == DONE: return "Resume ready, saved and logged ✓"
        if k == FAIL: return "Couldn't build the PDF"
    return None


# ---------- Run context ----------

def new_run_id() -> str:
    return uuid.uuid4().hex[:10]


def start_run(url: str = "", run_id: Optional[str] = None, **data: Any) -> str:
    """Begin a run in the current context. Events emitted afterwards belong to it.
    Threads started with ``asyncio.to_thread`` inherit the context automatically.
    Extra data (for example mode="scan") travels with the "started" event."""
    rid = run_id or new_run_id()
    _current_run.set(rid)
    emit_run("started", url=url, **data)
    return rid


class muted:
    """``with events.muted():`` does background work (such as the Matcher reading a posting) without emitting events,
    so it doesn't show up as the Scout or Analyst working on the job on screen."""
    def __enter__(self):
        self._token = _current_run.set(None)
        return self

    def __exit__(self, *exc):
        _current_run.reset(self._token)
        return False


def current_run_id() -> Optional[str]:
    return _current_run.get()


def use_run(run_id: Optional[str]) -> None:
    _current_run.set(run_id)


def emit_run(state: str, **data: Any) -> None:
    """Run-level markers: started / finished / failed, and for a scan with several jobs, job_started / job_finished /
    job_failed (the run stays open while the next job begins)."""
    rid = _current_run.get()
    if rid is None:
        return
    bus.publish(rid, {
        "run_id": rid, "ts": time.time(), "agent": None, "to": None,
        "kind": RUN, "message": state, "data": {"state": state, **data},
    })
    if state in ("finished", "failed"):
        save_trace(rid)


def emit(agent: str, kind: str, message: str, to: Optional[str] = None, **data: Any) -> None:
    """Publish an agent event to the current run (no-op if no run is active)."""
    rid = _current_run.get()
    if rid is None:
        return
    bus.publish(rid, {
        "run_id": rid, "ts": time.time(), "agent": agent, "to": to,
        "kind": kind, "message": message, "data": data,
    })


# ---------- Trace files (replay / video backup) ----------

def traces_dir() -> str:
    return os.path.join(os.getenv("OUTPUT_DIR", "output"), "traces")


def save_trace(run_id: str) -> Optional[str]:
    try:
        os.makedirs(traces_dir(), exist_ok=True)
        path = os.path.join(traces_dir(), f"{run_id}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(bus.get_run(run_id), f, ensure_ascii=False)
        return path
    except Exception:
        return None


def load_trace(run_id: str) -> list[dict[str, Any]]:
    if not run_id.isalnum():
        return []
    path = os.path.join(traces_dir(), f"{run_id}.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []
