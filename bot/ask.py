"""The Archivist asks you, in Telegram, about tools a job wants that your saved experience doesn't mention.

Flow per tool:  "Do you have real experience with X?"  ->  Yes / No
                Yes -> "Where did you use it?" (your jobs and projects as buttons)
                    -> "What did you do with it?" (one or two sentences, typed)
Your answer is saved as a new bullet point under that job or project and the tool is added to your skills,
so it is used in this resume and every later one. No answer within the time limit means "skip"."""
from __future__ import annotations

import asyncio
import html
import itertools
import logging
import re
from dataclasses import dataclass
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationHandlerStop, ContextTypes
from telegram import Update

from bot import config
from bot.extraction import extract_skills, extract_tech_tags
from service import events
from service.admin import (
    AddSkillsRequest,
    NewFactBullet,
    _load_facts_raw,
    _load_skeleton_raw,
    add_fact_to_entry,
    add_skills,
)
from service.models import FactBullet, SkillAnswer

logger = logging.getLogger(__name__)


@dataclass
class _Waiter:
    stage: str  # "choice" (buttons) or "text" (typed answer)
    qid: int
    future: asyncio.Future
    min_chars: int = 1  # shortest typed answer that counts (a pasted job description is long)


_pending: dict[int, _Waiter] = {}   # chat id -> the question currently waiting for an answer
_locks: dict[int, asyncio.Lock] = {}  # one set of questions at a time per chat
_qids = itertools.count(1)


def resolve(chat_id: int, qid: int, value: str) -> bool:
    """Deliver an answer to the question waiting in this chat. False if it expired or is for another question."""
    waiter = _pending.get(chat_id)
    if waiter is None or waiter.qid != qid or waiter.future.done():
        return False
    waiter.future.set_result(value)
    return True


def _h(text: object) -> str:
    return html.escape(str(text), quote=False)


def _entry_choices() -> list[tuple[str, str, str]]:
    """The user's jobs and projects as (id, button label, plain name)."""
    sk = _load_skeleton_raw()
    out = [(e["id"], f"💼 {e['title']} · {e['company']}"[:60], f"{e['title']} at {e['company']}")
           for e in sk.get("experience_entries", [])]
    out += [(e["id"], f"🛠 {e['name']}"[:60], e["name"]) for e in sk.get("project_entries", [])]
    return out


def _save_answer(skill: str, entry_id: str, text: str) -> FactBullet:
    """Blocking (file + LLM work): store the user's words as a new bullet point and add the tool to their skills."""
    tags = [skill] + [t for t in extract_tech_tags(text) if t.lower() != skill.lower()]
    resp = add_fact_to_entry(entry_id, NewFactBullet(text=text, tech_tags=tags))
    fact = next(f for f in _load_facts_raw() if f["id"] == resp.fact_ids[0])
    _save_skill(skill)
    return FactBullet(**fact)


def _save_skill(skill: str) -> None:
    extracted = extract_skills(skill)
    add_skills(AddSkillsRequest(category=extracted.category, items=extracted.items or [skill]))


class SkillQuestioner:
    """Callable handed to the pipeline: ``questioner(["Kubernetes"]) -> [SkillAnswer]``.
    The pipeline runs in a worker thread; the questions run on the bot's event loop."""

    def __init__(self, bot, chat_id: int, loop: asyncio.AbstractEventLoop) -> None:
        self.bot, self.chat_id, self.loop = bot, chat_id, loop
        self._gave_up = False

    def __call__(self, skills: list[str]) -> list[SkillAnswer]:
        run_id = events.current_run_id()
        return asyncio.run_coroutine_threadsafe(self._ask_all(skills, run_id), self.loop).result()

    async def _ask_all(self, skills: list[str], run_id: Optional[str]) -> list[SkillAnswer]:
        events.use_run(run_id)
        lock = _locks.setdefault(self.chat_id, asyncio.Lock())
        answers: list[SkillAnswer] = []
        async with lock:
            for i, skill in enumerate(skills):
                if self._gave_up:  # no answer to the previous question: don't keep nagging
                    answers.append(SkillAnswer(skill=skill))
                    continue
                if i > 0:
                    # The pipeline announced the first question. Each later question needs its own event, or the
                    # dashboard shows no arrow to you for the second one.
                    events.emit("archivist", events.ASK, f"Asking you about {skill}", skills=[skill], index=i, total=len(skills))
                answers.append(await self._ask_one(skill))
        return answers

    # ----- one tool -----
    async def _ask_one(self, skill: str) -> SkillAnswer:
        s = _h(skill)
        choice = await self._choose(
            f"📚 This job asks for <b>{s}</b>, but I can't find it in your saved experience.\n\n"
            "Do you have real experience with it?",
            [("✅ Yes, I do", "yes"), ("❌ No", "no")],
        )
        if choice is None:
            self._gave_up = True
            events.emit("archivist", events.PROGRESS, "No answer", skill=skill, answer="timeout")
            return SkillAnswer(skill=skill)
        if choice != "yes":
            events.emit("archivist", events.PROGRESS, "Said no", skill=skill, answer="no")
            await self.bot.send_message(self.chat_id, "👍 Okay, I won't claim it on this resume.")
            return SkillAnswer(skill=skill)
        events.emit("archivist", events.PROGRESS, "Said yes", skill=skill, answer="yes")

        entries = _entry_choices()
        buttons = [(label, eid) for eid, label, _ in entries] + [("➕ Just add it to my skills", "skills")]
        where = await self._choose(f"Great! Where did you use <b>{s}</b>?", buttons)
        names = {eid: name for eid, _, name in entries}

        if where in (None, "skills"):
            return await self._skills_only(skill)

        text = await self._ask_text(
            f"In a sentence or two, what did you do with <b>{s}</b>"
            f" at <b>{_h(names.get(where, 'that place'))}</b>?\n"
            "<i>This goes on your resume, so keep it true.</i>"
        )
        if not text:
            return await self._skills_only(skill)
        try:
            fact = await asyncio.to_thread(_save_answer, skill, where, text)
        except Exception as e:
            logger.error("Could not save the answer about %s: %s", skill, e)
            await self.bot.send_message(self.chat_id, "⚠️ I couldn't save that, so I'll carry on without it.")
            return SkillAnswer(skill=skill)
        events.emit("archivist", events.PROGRESS, "Saved", skill=skill, saved=True,
                    where=names.get(where, ""), text=text)
        await self.bot.send_message(
            self.chat_id,
            f"✅ Saved! I'll use it in this resume, and it's now part of your saved experience for future jobs.",
        )
        return SkillAnswer(skill=skill, has=True, fact=fact)

    async def _skills_only(self, skill: str) -> SkillAnswer:
        try:
            await asyncio.to_thread(_save_skill, skill)
        except Exception as e:
            logger.error("Could not add skill %s: %s", skill, e)
            await self.bot.send_message(self.chat_id, "⚠️ I couldn't save that, so I'll carry on without it.")
            return SkillAnswer(skill=skill)
        events.emit("archivist", events.PROGRESS, "Saved", skill=skill, saved=True, where="", text="")
        await self.bot.send_message(self.chat_id, f"✅ Added <b>{_h(skill)}</b> to your skills.", parse_mode="HTML")
        return SkillAnswer(skill=skill, has=True)

    # ----- asking and waiting -----
    async def _choose(self, text: str, options: list[tuple[str, str]]) -> Optional[str]:
        """Send buttons, wait for a tap. Returns the tapped value, or None after the timeout."""
        waiter = _Waiter("choice", next(_qids), self.loop.create_future())
        _pending[self.chat_id] = waiter
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton(label, callback_data=f"ask:{waiter.qid}:{value}")] for label, value in options]
        )
        msg = await self.bot.send_message(self.chat_id, text, parse_mode="HTML", reply_markup=keyboard)
        value = await self._wait(waiter)
        label = next((lbl for lbl, v in options if v == value), None)
        await self._close(msg, text, f"➡️ {_h(label)}" if label else "⏱ No answer, skipping")
        return value

    async def _ask_text(self, text: str, min_chars: int = 1, timeout: Optional[float] = None) -> Optional[str]:
        """Ask for a typed answer (with a Skip button). Returns the text, or None if skipped or timed out."""
        waiter = _Waiter("text", next(_qids), self.loop.create_future(), min_chars)
        _pending[self.chat_id] = waiter
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Skip", callback_data=f"ask:{waiter.qid}:skip")]])
        msg = await self.bot.send_message(self.chat_id, text, parse_mode="HTML", reply_markup=keyboard)
        value = await self._wait(waiter, timeout)
        await self._close(msg, text, "⏱ No answer, skipping" if value is None else ("➡️ Skipped" if value == "skip" else "➡️ Got it"))
        return None if value in (None, "skip") else value

    async def _wait(self, waiter: _Waiter, timeout: Optional[float] = None) -> Optional[str]:
        try:
            return await asyncio.wait_for(waiter.future, timeout or config.ASK_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            return None
        finally:
            if _pending.get(self.chat_id) is waiter:
                del _pending[self.chat_id]

    async def _close(self, msg, text: str, outcome: str) -> None:
        try:  # remove the buttons and show what was chosen
            await msg.edit_text(f"{text}\n\n{outcome}", parse_mode="HTML")
        except Exception as e:
            logger.debug("Could not update question message: %s", e)


async def ask_for_job_text(bot, chat_id: int, loop: asyncio.AbstractEventLoop, min_chars: int) -> Optional[str]:
    """Last resort when no route could read the job page: ask the user to paste the job description."""
    events.emit("scout", events.ASK, "Asking you to paste the job description", paste=True)
    questioner = SkillQuestioner(bot, chat_id, loop)
    lock = _locks.setdefault(chat_id, asyncio.Lock())
    async with lock:
        text = await questioner._ask_text(
            "🔭 I couldn't read that page any way I tried. It may be blocking robots.\n\n"
            "<b>Can you paste the job description here?</b> Open the page, select the text, copy it and send it to me "
            "as a message. I'll use that instead.\n<i>Or tap Skip to give up on this link.</i>",
            min_chars=min_chars, timeout=config.ASK_TIMEOUT_SECONDS * 2,
        )
    if text:
        await bot.send_message(chat_id, "👍 Got it, I'll use the text you pasted.")
    return text


# ----- Telegram handlers (registered in bot/main.py) -----
async def ask_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A button on one of the Archivist's questions was tapped."""
    query = update.callback_query
    try:
        _, qid, value = (query.data or "").split(":", 2)
        ok = resolve(query.message.chat_id, int(qid), value)
    except ValueError:
        ok = False
    await query.answer("" if ok else "That question has already closed.")


async def ask_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """If the Archivist is waiting for a typed answer in this chat, this message is that answer."""
    waiter = _pending.get(update.effective_chat.id) if update.effective_chat else None
    text = (update.effective_message.text or "").strip() if update.effective_message else ""
    if waiter is None or waiter.stage != "text" or waiter.future.done() or not text:
        return  # not an answer: let the normal handlers deal with this message
    if len(text) < waiter.min_chars:
        if re.search(r"https?://", text):
            return  # a new job link, not the answer: let the job handler take it
        await update.effective_message.reply_text("That looks a bit short. Please paste the whole job description, or tap Skip.")
        raise ApplicationHandlerStop
    waiter.future.set_result(text)
    raise ApplicationHandlerStop
