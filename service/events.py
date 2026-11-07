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
}

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


def describe(ev: dict[str, Any]) -> Optional[str]:
    """One plain-language line for an event, with its numbers. Shared by Telegram and the dashboard,
    so the wording lives in one place. Returns None for events that say nothing (hand-offs, run markers)."""
    a, k, d, m = ev.get("agent"), ev.get("kind"), ev.get("data") or {}, ev.get("message") or ""
    if k in (HANDOFF, RUN):
        return None
    if k == ASK:
        return f"Asking you about {', '.join(d.get('skills', []))}… (check Telegram)"

    def n(word: str, count: int) -> str:
        return f"{count} {word}{'' if count == 1 else 's'}"

    if a == "scout":
        if k == START: return "Opening the job page…"
        if k == PROGRESS:
            return "That didn't work, trying another way in…" if ("switching" in m.lower() or "failed" in m.lower()) else "Fetching the page…"
        if k == DONE: return f"Read the whole posting ({int(d.get('chars', 0)):,} characters)"
        if k == FAIL: return "Couldn't read this page. It may be blocking automated visitors"
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
        if k == FAIL: return "Your saved experience is empty. Add some with /addexperience first"
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


def start_run(url: str = "", run_id: Optional[str] = None) -> str:
    """Begin a run in the current context. Events emitted afterwards belong to it.
    Threads started with ``asyncio.to_thread`` inherit the context automatically."""
    rid = run_id or new_run_id()
    _current_run.set(rid)
    emit_run("started", url=url)
    return rid


def current_run_id() -> Optional[str]:
    return _current_run.get()


def use_run(run_id: Optional[str]) -> None:
    _current_run.set(run_id)


def emit_run(state: str, **data: Any) -> None:
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
