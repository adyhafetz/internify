"""/experience: browse and edit everything you've saved by tapping. No IDs to look up or type.

    /experience                    -> your entries as buttons (education, jobs, projects, leadership)
    tap an entry                   -> its numbered bullet points, each with ✏️ edit and 🗑 delete buttons
    ✏️ / ➕                         -> "send me the text" (your next message is the answer)
    🗑                             -> shows what will go, asks to confirm

The old typed commands (/listentries, /listfacts <name>) open these same screens."""
from __future__ import annotations

import asyncio
import logging
import time
from html import escape
from math import ceil
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import ApplicationHandlerStop, ContextTypes

from bot.extraction import extract_tech_tags
from bot.handlers.common import restricted
from service import editor
from service.editor import EditError, EDU, EXP, LEAD, PROJ

logger = logging.getLogger(__name__)

ITEMS_PER_PAGE = 6
MAX_SHOWN_CHARS = 350
WAIT_KEY = "xp_wait"
WAIT_SECONDS = 15 * 60  # an unanswered "send me the text" expires, so a later message isn't taken as the answer
ICON = {EDU: "🎓", EXP: "💼", PROJ: "🛠", LEAD: "🏅"}
Screen = tuple[str, InlineKeyboardMarkup]


def _h(text: object) -> str:
    return escape(str(text), quote=False)


def _short(text: str, limit: int = MAX_SHOWN_CHARS) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _btn(label: str, *parts: object) -> InlineKeyboardButton:
    return InlineKeyboardButton(label, callback_data="xp:" + ":".join(str(p) for p in parts))


# ---------------------------------------------------------------- screens

def list_screen(entries: Optional[list] = None, heading: str = "", notice: str = "") -> Screen:
    entries = editor.list_entries() if entries is None else entries
    lines = [notice, ""] if notice else []
    if not entries:
        lines.append("📋 <b>Nothing saved yet.</b>\n\nSend me your resume file (/importresume), or add things one by one with "
                     "/addexperience, /addproject, /addleadership and /addeducation.")
        return "\n".join(lines), InlineKeyboardMarkup([])
    lines += [f"📋 <b>{_h(heading or 'Your experience')}</b>", "Tap one to see it and edit it."]
    rows = [[_btn(_short(f"{ICON[e.kind]} {e.label} ({e.count})", 60), "o", e.kind, e.ref, 0)] for e in entries]
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def _page_of(view: editor.EntryView, token: Optional[str]) -> int:
    idx = next((i for i, it in enumerate(view.items) if it.token == token), 0)
    return idx // ITEMS_PER_PAGE


def entry_screen(kind: str, ref: str, page: int = 0, notice: str = "") -> Optional[Screen]:
    view = editor.get_entry(kind, ref)
    if view is None:
        return None
    n = len(view.items)
    pages = max(1, ceil(n / ITEMS_PER_PAGE))
    page = max(0, min(page, pages - 1))
    heading = "Honors" if kind == EDU else "Bullet points"

    lines = [notice, ""] if notice else []
    lines.append(f"{ICON[kind]} <b>{_h(view.title)}</b>")
    if view.subtitle:
        lines.append(f"<i>{_h(view.subtitle)}</i>")
    if view.courses is not None:
        lines += ["", "<b>Courses:</b> " + (_h(", ".join(view.courses)) if view.courses else "<i>none yet</i>")]
    lines += ["", f"<b>{heading}</b>" + (f" (page {page + 1} of {pages})" if pages > 1 else "")]
    if not n:
        lines.append("<i>None yet. Tap ➕ to add one.</i>")

    start = page * ITEMS_PER_PAGE
    shown = view.items[start:start + ITEMS_PER_PAGE]
    for number, item in enumerate(shown, start=start + 1):
        lines.append(f"{number}. {_h(_short(item.text))}")

    rows: list[list[InlineKeyboardButton]] = []
    pairs = []
    for number, item in enumerate(shown, start=start + 1):
        pairs += [_btn(f"✏️ {number}", "e", kind, ref, item.token), _btn(f"🗑 {number}", "d", kind, ref, item.token)]
    rows += [pairs[i:i + 4] for i in range(0, len(pairs), 4)]
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(_btn("◀ Previous", "o", kind, ref, page - 1))
        if page < pages - 1:
            nav.append(_btn("Next ▶", "o", kind, ref, page + 1))
        rows.append(nav)
    rows.append([_btn(f"➕ Add a {'honor' if kind == EDU else 'bullet point'}", "a", kind, ref)])
    if kind == EDU:
        rows.append([_btn("➕ Add a course", "ca", ref)] + ([_btn("➖ Remove a course", "cd", ref)] if view.courses else []))
    rows.append([_btn("⬅ All my experience", "l"), _btn("🗑 Delete this entry", "x", kind, ref)])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def _missing() -> Screen:
    return list_screen(notice="That entry no longer exists.")


# ---------------------------------------------------------------- sending

async def _show(query, screen: Screen) -> None:
    text, markup = screen
    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)
    except BadRequest as err:
        if "not modified" not in str(err).lower():   # tapping the same screen again is fine
            raise


def _clear_wait(context) -> None:
    context.user_data.pop(WAIT_KEY, None)


# ---------------------------------------------------------------- commands

@restricted
async def experience_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/experience, and the old /listentries and /listfacts [name]."""
    _clear_wait(context)
    msg = update.effective_message
    query = " ".join(context.args or []).strip()
    if query:
        matches = editor.find_entries(query)
        if len(matches) == 1:
            screen = entry_screen(matches[0].kind, matches[0].ref)
        elif matches:
            screen = list_screen(matches, heading=f"Which one did you mean by “{query}”?")
        else:
            screen = list_screen(notice=f"I couldn't find anything called “{_h(query)}”. Here's everything you have:")
    else:
        screen = list_screen()
    await msg.reply_text(screen[0], parse_mode="HTML", reply_markup=screen[1])


# ---------------------------------------------------------------- buttons

@restricted
async def xp_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _clear_wait(context)   # any tap cancels a pending "send me the text"
    parts = (query.data or "").split(":")[1:]
    action, args = (parts[0] if parts else ""), parts[1:]
    try:
        screen = await _handle(query, context, action, args)
    except EditError as err:
        screen = entry_screen(args[0], args[1], notice=f"⚠️ {_h(err)}") if len(args) >= 2 and args[0] in editor.KINDS else None
        screen = screen or list_screen(notice=f"⚠️ {_h(err)}")
    if screen:
        await _show(query, screen)


async def _handle(query, context, action: str, args: list[str]) -> Optional[Screen]:
    if action == "l":
        return list_screen()

    if action == "o":
        kind, ref, page = args[0], args[1], int(args[2]) if len(args) > 2 and args[2].isdigit() else 0
        return entry_screen(kind, ref, page) or _missing()

    view = editor.get_entry(args[0], args[1]) if args and args[0] in editor.KINDS and len(args) > 1 else None

    if action in ("e", "d", "dy"):
        kind, ref, token = args[0], args[1], args[2]
        if view is None:
            return _missing()
        item = next((it for it in view.items if it.token == token), None)
        if item is None:
            return entry_screen(kind, ref, notice="⚠️ That line has already changed.")
        number = view.items.index(item) + 1
        page = _page_of(view, token)
        back = InlineKeyboardMarkup([[_btn("⬅ Back", "o", kind, ref, page)]])
        if action == "e":
            context.user_data[WAIT_KEY] = {"action": "edit", "kind": kind, "ref": ref, "token": token, "at": time.time()}
            return (f"✏️ <b>Editing {view.noun} {number}</b>\n<i>{_h(_short(item.text))}</i>\n\n"
                    "Send me the new text now. Write it the way it should appear on your resume.", back)
        if action == "d":
            return (f"🗑 <b>Delete this {view.noun}?</b>\n\n<i>{_h(_short(item.text))}</i>",
                    InlineKeyboardMarkup([[_btn("Yes, delete it", "dy", kind, ref, token), _btn("No, keep it", "o", kind, ref, page)]]))
        editor.delete_item(kind, ref, token)
        return entry_screen(kind, ref, page, notice=f"🗑 Deleted {view.noun} {number}.")

    if action == "a":
        if view is None:
            return _missing()
        context.user_data[WAIT_KEY] = {"action": "add", "kind": args[0], "ref": args[1], "at": time.time()}
        extra = "\nI'll work out the technology tags myself." if view.has_tags else ""
        return (f"➕ <b>New {view.noun} for {_h(view.title)}</b>\n\nSend me the text now. Write it the way it should appear "
                f"on your resume.{extra}", InlineKeyboardMarkup([[_btn("⬅ Back", "o", args[0], args[1], 0)]]))

    if action == "x":
        if view is None:
            return _missing()
        n = len(view.items)
        what = f" and its {n} {view.noun}{'s' if n != 1 else ''}" if n else ""
        return (f"🗑 <b>Delete “{_h(view.title)}”{what}?</b>\nThis can't be undone.",
                InlineKeyboardMarkup([[_btn("Yes, delete it", "xy", args[0], args[1]), _btn("No, keep it", "o", args[0], args[1], 0)]]))

    if action == "xy":
        title = view.title if view else "That entry"
        removed = editor.delete_entry(args[0], args[1])
        return list_screen(notice=f"🗑 Deleted “{_h(title)}”" + (f" and its {removed} line{'s' if removed != 1 else ''}." if removed else "."))

    if action == "ca":
        context.user_data[WAIT_KEY] = {"action": "course", "kind": EDU, "ref": args[0], "at": time.time()}
        return ("➕ <b>Add a course</b>\n\nSend me the course name, for example “Operating Systems”.",
                InlineKeyboardMarkup([[_btn("⬅ Back", "o", EDU, args[0], 0)]]))

    if action in ("cd", "cy"):
        ref = args[0]
        view = editor.get_entry(EDU, ref)
        if view is None:
            return _missing()
        if action == "cy":
            name = editor.remove_course(ref, int(args[1]))
            return entry_screen(EDU, ref, notice=f"➖ Removed the course “{_h(name)}”.")
        rows = [[_btn(_short(f"➖ {c}", 60), "cy", ref, i)] for i, c in enumerate(view.courses or [])]
        rows.append([_btn("⬅ Back", "o", EDU, ref, 0)])
        return "➖ <b>Which course should I remove?</b>", InlineKeyboardMarkup(rows)

    return list_screen()


# ---------------------------------------------------------------- the typed answer

def _tags_for(text: str) -> list[str]:
    try:
        return extract_tech_tags(text)
    except Exception as err:  # tags are a nicety; never lose the user's text because the AI call failed
        logger.warning("Could not extract tags: %s", err)
        return []


async def xp_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """If you tapped ✏️ or ➕ and I'm waiting for text, your next message is the answer."""
    wait = context.user_data.get(WAIT_KEY)
    text = (update.effective_message.text or "").strip() if update.effective_message else ""
    if not wait or not text:
        return   # not for us: other handlers deal with this message
    if time.time() - wait["at"] > WAIT_SECONDS:
        _clear_wait(context)
        return

    kind, ref, action = wait["kind"], wait["ref"], wait["action"]
    msg = update.effective_message
    try:
        view = editor.get_entry(kind, ref)
        if view is None:
            raise EditError("That entry no longer exists.")
        tags = await asyncio.to_thread(_tags_for, text) if view.has_tags and action in ("edit", "add") else None
        if action == "edit":
            editor.edit_item(kind, ref, wait["token"], text, tags)
            notice, token = f"✅ Updated.", wait["token"]
        elif action == "add":
            item = editor.add_item(kind, ref, text, tags)
            notice, token = f"✅ Added.", item.token
        else:
            added = editor.add_course(ref, text)
            notice, token = ("✅ Course added." if added else "ℹ️ That course was already there."), None
    except EditError as err:
        wait["at"] = time.time()   # keep waiting: the user can send a corrected text
        await msg.reply_text(f"⚠️ {_h(err)}", parse_mode="HTML")
        raise ApplicationHandlerStop
    except Exception as err:
        logger.error("Could not save the change: %s", err)
        _clear_wait(context)
        await msg.reply_text(f"❌ I couldn't save that.\nReason: {_h(err)}", parse_mode="HTML")
        raise ApplicationHandlerStop

    _clear_wait(context)
    fresh = editor.get_entry(kind, ref)
    screen = entry_screen(kind, ref, _page_of(fresh, token) if fresh and token else 0, notice=notice)
    await msg.reply_text(screen[0], parse_mode="HTML", reply_markup=screen[1])
    raise ApplicationHandlerStop
