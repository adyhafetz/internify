"""Live progress message in Telegram, driven by the same events the dashboard shows."""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any, Optional

from service import events

logger = logging.getLogger(__name__)

MIN_EDIT_INTERVAL = 1.2  # seconds; Telegram throttles rapid message edits

_ICON = {"idle": "⚪", "working": "⏳", "done": "✅", "fail": "❌"}


class TelegramProgress:
    """Edits one Telegram message to show each agent's state as events arrive."""

    def __init__(self, message, run_id: str, loop: asyncio.AbstractEventLoop, header: str = "") -> None:
        self.message = message
        self.run_id = run_id
        self.loop = loop
        self.header = header
        self._state: dict[str, dict[str, str]] = {
            a: {"state": "idle", "text": ""} for a in events.AGENTS
        }
        self._scores: list[float] = []
        self._lock = threading.Lock()
        self._flush_pending = False
        self._flush_lock: Optional[asyncio.Lock] = None
        self._last_text = ""
        self._last_edit = 0.0
        self._closed = False
        self._unsub = events.bus.subscribe(self._on_event)

    # ----- event intake (any thread) -----
    def _on_event(self, ev: dict[str, Any]) -> None:
        if ev.get("run_id") != self.run_id or self._closed:
            return
        agent, kind = ev.get("agent"), ev.get("kind")
        if agent not in self._state:
            return
        with self._lock:
            st = self._state[agent]
            if kind == events.START:
                st["state"] = "working"
                st["text"] = ev["message"]
            elif kind == events.PROGRESS:
                st["text"] = ev["message"]
            elif kind == events.DONE:
                st["state"] = "done"
                st["text"] = ev["message"]
                if agent == "critic" and "score" in ev.get("data", {}):
                    self._scores.append(float(ev["data"]["score"]))
            elif kind == events.FAIL:
                st["state"] = "fail"
                st["text"] = ev["message"]
            elif kind == events.HANDOFF:
                target = ev.get("to")
                if target in self._state and self._state[target]["state"] == "idle":
                    self._state[target]["text"] = f"Waiting for {events.AGENTS[agent]['name']}"
            if self._flush_pending:
                return
            self._flush_pending = True
        try:
            asyncio.run_coroutine_threadsafe(self._flush(), self.loop)
        except Exception:
            self._flush_pending = False

    # ----- rendering -----
    def render(self) -> str:
        lines = [self.header or "🤖 Internify agents at work", ""]
        with self._lock:
            for key, meta in events.AGENTS.items():
                st = self._state[key]
                line = f"{_ICON[st['state']]} {meta['emoji']} {meta['name']}"
                if st["text"]:
                    line += f" — {st['text'][:90]}"
                lines.append(line)
            if self._scores:
                lines.append("")
                lines.append("📈 ATS score: " + " → ".join(f"{s:.0f}" for s in self._scores))
        return "\n".join(lines)

    async def _flush(self) -> None:
        if self._flush_lock is None:
            self._flush_lock = asyncio.Lock()
        async with self._flush_lock:
            wait = MIN_EDIT_INTERVAL - (time.monotonic() - self._last_edit)
            if wait > 0:
                await asyncio.sleep(wait)
            with self._lock:
                self._flush_pending = False
            if self._closed:
                return
            text = self.render()
            if text == self._last_text:
                return
            try:
                await self.message.edit_text(text)
                self._last_text = text
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
