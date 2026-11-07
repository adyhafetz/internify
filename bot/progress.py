"""Live progress in Telegram: one message that shows only the step happening right now."""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any, Optional

from service import events

logger = logging.getLogger(__name__)

MIN_EDIT_INTERVAL = 1.2  # seconds; Telegram throttles rapid message edits
ORDER = events.PIPELINE  # scout, analyst, archivist, scribe, critic, publisher


class TelegramProgress:
    """Edits one Telegram message so it always shows the current step, e.g.
        ⏳ Step 4 of 6 · ✍️ Scribe
        Writing resume lines from your real experience…
    The wording comes from ``events.describe`` so it matches the dashboard exactly."""

    def __init__(self, message, run_id: str, loop: asyncio.AbstractEventLoop) -> None:
        self.message = message
        self.run_id = run_id
        self.loop = loop
        self._text = "🔎 Job link detected, waking up the agents…"
        self._scores: list[float] = []
        self._lock = threading.Lock()
        self._flush_pending = False
        self._flush_lock: Optional[asyncio.Lock] = None
        self._last_sent = ""
        self._last_edit = 0.0
        self._closed = False
        self._unsub = events.bus.subscribe(self._on_event)

    # ----- event intake (any thread) -----
    def _on_event(self, ev: dict[str, Any]) -> None:
        if ev.get("run_id") != self.run_id or self._closed:
            return
        agent, say, data = ev.get("agent"), ev.get("say"), ev.get("data") or {}
        if agent not in ORDER or not say or data.get("duplicate") is not None:
            return  # hand-offs, run markers and the tracker note don't change the current step
        meta = events.AGENTS[agent]
        icon = {events.FAIL: "❌", events.DONE: "✅", events.ASK: "❓"}.get(ev["kind"], "⏳")
        with self._lock:
            if agent == "critic" and ev["kind"] == events.DONE and "score" in data:
                self._scores.append(float(data["score"]))
            text = f"{icon} Step {ORDER.index(agent) + 1} of {len(ORDER)} · {meta['emoji']} {meta['name']}\n{say}"
            if self._scores:
                text += "\n\n📈 Job match: " + " → ".join(f"{s:.0f}%" for s in self._scores)
            self._text = text
            if self._flush_pending:
                return
            self._flush_pending = True
        try:
            asyncio.run_coroutine_threadsafe(self._flush(), self.loop)
        except Exception:
            self._flush_pending = False

    # ----- sending -----
    async def _flush(self) -> None:
        if self._flush_lock is None:
            self._flush_lock = asyncio.Lock()
        async with self._flush_lock:
            wait = MIN_EDIT_INTERVAL - (time.monotonic() - self._last_edit)
            if wait > 0:
                await asyncio.sleep(wait)
            with self._lock:
                self._flush_pending = False
                text = self._text
            if self._closed or text == self._last_sent:
                return
            try:
                await self.message.edit_text(text)
                self._last_sent = text
            except Exception as e:  # "message is not modified", rate limits, network
                logger.debug("Progress edit skipped: %s", e)
            self._last_edit = time.monotonic()

    async def close(self) -> None:
        """Stop listening and wait for any in-flight edit, so callers can safely overwrite the message."""
        self._closed = True
        self._unsub()
        if self._flush_lock is not None:
            async with self._flush_lock:
                pass
