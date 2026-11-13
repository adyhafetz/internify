"""/profile: everything on your resume in one place, by tapping. No IDs to look up or type, and no other commands.

    /profile                       -> contact details, education, jobs, projects, leadership, skills, and ➕ Add
    tap an entry                   -> its numbered bullet points, each with ✏️ edit and 🗑 delete buttons
    ✏️ / ➕                         -> "send me the text" (your next message is the answer)
    🗑                             -> shows what will go, asks to confirm"""
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

from bot.extraction import (
    extract_education,
    extract_experience,
    extract_leadership,
    extract_project,
    extract_tech_tags,
)
from bot import config
from bot.handlers.common import restricted
from bot.services import starter_pages, watchlist
from service import editor
from service.profile_data import (
    _load_skeleton_raw,
    add_education,
    add_experience,
    add_leadership,
    add_project,
)
from service.editor import EditError, EDU, EXP, LEAD, PROJ

logger = logging.getLogger(__name__)

ITEMS_PER_PAGE = 6
MAX_SHOWN_CHARS = 350
WAIT_KEY = "xp_wait"
WAIT_SECONDS = 15 * 60  # an unanswered "send me the text" expires, so a later message isn't taken as the answer
ICON = {EDU: "🎓", EXP: "💼", PROJ: "🛠", LEAD: "🏅"}
SKILLS_PER_PAGE = 15
Screen = tuple[str, InlineKeyboardMarkup]


def _h(text: object) -> str:
    return escape(str(text), quote=False)


def _short(text: str, limit: int = MAX_SHOWN_CHARS) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _btn(label: str, *parts: object) -> InlineKeyboardButton:
    return InlineKeyboardButton(label, callback_data="xp:" + ":".join(str(p) for p in parts))


# ---------------------------------------------------------------- screens

def list_screen(notice: str = "") -> Screen:
    """The hub: contact details, every entry, skills and ➕ Add."""
    entries = editor.list_entries()
    lines = [notice, ""] if notice else []
    lines.append("📋 <b>Your profile</b>")
    lines.append("Everything on your resume. Tap one to see it and edit it." if entries else
                 "Nothing saved yet. Send me your resume file (/importresume) or tap ➕ Add something.")
    rows = [[_btn("👤 Contact details", "c")]]
    rows += [[_btn(_short(f"{ICON[e.kind]} {e.label} ({e.count})", 60), "o", e.kind, e.ref, 0)] for e in entries]
    rows.append([_btn(f"🔧 Skills ({sum(n for _, _, n in editor.list_skill_categories())})", "s")])
    rows.append([_btn(f"🔭 Job pages I check ({len(watchlist.list_sources())})", "w")])
    if my_links():
        rows.append([_btn("🔗 My links", "lk")])
    rows.append([_btn("🧹 Tidy up", "ty")])
    rows.append([_btn("➕ Add something", "n")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def my_links() -> list[tuple[str, str]]:
    """(button text, address) for what is set up: the dashboard, the tracker sheet and the resume folder.
    Telegram only accepts public https addresses on buttons, so a localhost address is left out."""
    links: list[tuple[str, str]] = []
    if config.PUBLIC_URL.startswith("https://"):
        links.append(("📊 Live dashboard", f"{config.PUBLIC_URL}/dashboard"))
    sheet = config.GOOGLE_SHEET_ID.strip()
    if sheet:
        links.append(("📑 Application tracker (Google Sheet)", f"https://docs.google.com/spreadsheets/d/{sheet}/edit"))
    folder = config.GOOGLE_DRIVE_FOLDER_ID.strip().rstrip("/").rsplit("/", 1)[-1]
    if folder:
        links.append(("📁 Resume folder (Google Drive)", f"https://drive.google.com/drive/folders/{folder}"))
    return links


def links_screen() -> Screen:
    links = my_links()
    if not links:
        return ("🔗 <b>My links</b>\n\nNothing to link to yet. The dashboard link needs your website address, and the sheet and folder "
                "come from connecting Google. Run the setup page again to add them."), InlineKeyboardMarkup([[_btn("⬅ Back to profile", "l")]])
    rows = [[InlineKeyboardButton(text, url=url)] for text, url in links]
    rows.append([_btn("⬅ Back to profile", "l")])
    return "🔗 <b>My links</b>\nTap one to open it.", InlineKeyboardMarkup(rows)


def contact_screen(notice: str = "") -> Screen:
    lines = [notice, ""] if notice else []
    lines += ["👤 <b>Contact details</b>", "These appear at the top of your resume.", ""]
    for _, label, value in editor.get_contact():
        lines.append(f"<b>{label}:</b> " + (_h(value) if value else "<i>not set</i>"))
    buttons = [_btn(f"✏️ {label}", "ce", key) for key, label, _ in editor.get_contact()]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([_btn("⬅ Back to profile", "l")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def skills_screen(notice: str = "") -> Screen:
    lines = [notice, ""] if notice else []
    lines += ["🔧 <b>Skills</b>", "Tap a category to see, add or remove skills."]
    rows = [[_btn(f"{name} ({count})", "sc", i, 0)] for i, name, count in editor.list_skill_categories()]
    rows.append([_btn("⬅ Back to profile", "l")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def skill_category_screen(index: int, page: int = 0, notice: str = "") -> Screen:
    category, skills = editor.get_skills(index)
    pages = max(1, ceil(len(skills) / SKILLS_PER_PAGE))
    page = max(0, min(page, pages - 1))
    lines = [notice, ""] if notice else []
    lines.append(f"🔧 <b>{_h(category)}</b>" + (f" (page {page + 1} of {pages})" if pages > 1 else ""))
    lines.append(_h(", ".join(s.name for s in skills)) if skills else "<i>No skills here yet. Tap ➕ to add some.</i>")
    if skills:
        lines.append("\nTap a skill to remove it.")
    shown = skills[page * SKILLS_PER_PAGE:(page + 1) * SKILLS_PER_PAGE]
    chips = [_btn(_short(f"✕ {s.name}", 28), "sd", index, s.token) for s in shown]
    rows = [chips[i:i + 3] for i in range(0, len(chips), 3)]
    if pages > 1:
        nav = ([_btn("◀ Previous", "sc", index, page - 1)] if page > 0 else []) + \
              ([_btn("Next ▶", "sc", index, page + 1)] if page < pages - 1 else [])
        rows.append(nav)
    rows.append([_btn(f"➕ Add to {category}", "sa", index)])
    rows.append([_btn("⬅ All skills", "s")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def _schedule_text() -> str:
    if not config.SCAN_ENABLED:
        return "The daily check is switched off in the settings."
    if watchlist.scan_paused():
        return "The daily check is <b>paused</b>."
    return f"Every day at <b>{_h(config.SCAN_TIME)}</b> ({_h(config.SCAN_TIMEZONE)})"


def _source_status(src) -> str:
    """Plain-language result of the last visit."""
    if not src.last_checked:
        return "not checked yet"
    day = src.last_checked[:10]
    st = src.last_status or ""
    if st.startswith("ok:"):
        return f"checked {day}: {st[3:].strip()} found"
    if st.startswith("failed:"):
        return f"checked {day}: didn't answer ({st[7:].strip()})"
    return f"checked {day}"


def starters_screen(notice: str = "") -> Screen:
    """Well-known Malaysian job pages, one tap each."""
    have = {src.url.rstrip("/") for src in watchlist.list_sources()}
    lines = [notice, ""] if notice else []
    lines += ["🇲🇾 <b>Popular in Malaysia</b>", "Tap one to add it to the pages I check each day. Pages that list many employers "
              "give me the most to choose from.", ""]
    rows = []
    for i, (name, url, note) in enumerate(starter_pages.MALAYSIA):
        done = url.rstrip("/") in have
        lines.append(f"• <b>{_h(name)}</b>: {_h(note)}" + (" ✓" if done else ""))
        rows.append([_btn(("✓ " if done else "➕ ") + _short(name, 34), "wm", i)])
    rows.append([_btn("⬅ Back", "w")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def watchlist_screen(notice: str = "") -> Screen:
    from bot.scanner import is_running
    watchlist.sources_from_history()   # pick up job boards behind the jobs you've already sent
    sources = watchlist.list_sources()
    lines = [notice, ""] if notice else []
    lines += ["🔭 <b>Job pages I check</b>",
              f"{_schedule_text()}, I look at these for new jobs, keep the best matches (up to {config.SCAN_MAX_TAILORED}) and write a resume for each.", ""]
    names = {"greenhouse": "job board", "lever": "job board", "page": "career page"}
    if sources:
        for src in sources:
            extra = " · from your jobs" if src.auto else ""
            lines.append(f'• <a href="{_h(src.url)}">{_h(src.label)}</a> ({names[src.kind]}{extra})\n   {_h(_source_status(src))}')
    else:
        lines.append("<i>Nothing here yet. Add a career page or a job-board link, for example https://boards.greenhouse.io/acme "
                     "or https://jobs.lever.co/acme. Boards on Greenhouse or Lever work best.</i>")
    if sources:
        lines += ["", "Tap ✕ to stop checking one."]
    chips = [_btn(_short(f"✕ {src.label}", 28), "wd", src.id) for src in sources]
    rows = [chips[i:i + 2] for i in range(0, len(chips), 2)]
    rows.append([_btn("➕ Add a link", "wa"), _btn("🇲🇾 Popular in Malaysia", "wm")])
    rows.append([_btn("⏳ Checking now…" if is_running() else "▶ Check now", "wn"),
                 _btn("▶ Resume daily check" if watchlist.scan_paused() else "⏸ Pause daily check", "wp")])
    rows.append([_btn("⬅ Back to profile", "l")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def add_menu_screen() -> Screen:
    text = ("➕ <b>What would you like to add?</b>\n"
            "You describe it in your own words and I'll turn it into resume bullet points.")
    rows = [[_btn("💼 A job or internship", "nn", EXP)], [_btn("🛠 A project", "nn", PROJ)],
            [_btn("🏅 A leadership role or activity", "nn", LEAD)], [_btn("🎓 A degree", "nn", EDU)],
            [_btn("🔧 Skills", "s")], [_btn("⬅ Back to profile", "l")]]
    return text, InlineKeyboardMarkup(rows)


# What to say when asking for a new entry (in plain words, with an example to copy the shape from)
NEW_PROMPTS = {
    EXP: ("💼 <b>Describe your job or internship</b>", "your role, company, location, dates and what you did",
          "Software Engineering Intern at Tech Corp in Kuala Lumpur, Jun to Aug 2024. Built REST APIs with FastAPI and cut latency by 30%."),
    PROJ: ("🛠 <b>Describe your project</b>", "its name, the technologies, the date and what it does",
           "TaskFlow, a kanban app built with React and Node.js in Dec 2024. Used by 40 students, deployed with Docker."),
    LEAD: ("🏅 <b>Describe your role or activity</b>", "your title, the organisation, location, dates and what you did",
           "Vice President of the Computer Science Society at Universiti Malaya, Aug 2023 to May 2024. Organised 5 hackathons for 200 members."),
    EDU: ("🎓 <b>Describe your degree</b>", "the university, degree, dates, location, GPA and relevant courses",
          "Bachelor of Computer Science at Universiti Malaya, 2022 to 2026, CGPA 3.8. Courses: Data Structures, AI, Database Systems."),
}


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


def _wait(context, **info) -> None:
    context.user_data[WAIT_KEY] = {**info, "at": time.time()}


# ---------------------------------------------------------------- creating entries from a description

def create_from_description(kind: str, text: str) -> tuple[str, str, str]:
    """Blocking (AI + saving). Returns (kind, ref, notice) for the entry that was created."""
    if kind == EXP:
        res = add_experience(extract_experience(text)); ref = res.entry_id
    elif kind == PROJ:
        res = add_project(extract_project(text)); ref = res.entry_id
    elif kind == EDU:
        res = add_education(extract_education(text)); ref = res.entry_id
    elif kind == LEAD:
        res = add_leadership(extract_leadership(text)); ref = str(len(_load_skeleton_raw()["leadership"]) - 1)
    else:
        raise EditError("I don't know how to add that.")
    notice = "✅ Added. Here's what I understood. Tap ✏️ to fix any line."
    if res.skills_added:
        notice += "\n🔧 New skills: " + _h(", ".join(s for items in res.skills_added.values() for s in items))
    return kind, ref, notice


@restricted
async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/profile: the one place to see and edit everything."""
    _clear_wait(context)
    text, markup = list_screen()
    await update.effective_message.reply_text(text, parse_mode="HTML", reply_markup=markup)


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
        notice = f"⚠️ {_h(err)}"
        if action.startswith("s") and args[:1] and args[0].isdigit():
            screen = skill_category_screen(int(args[0]), notice=notice)
        elif action.startswith("c"):
            screen = contact_screen(notice=notice)
        elif action.startswith("w"):
            screen = watchlist_screen(notice=notice)
        elif len(args) >= 2 and args[0] in editor.KINDS:
            screen = entry_screen(args[0], args[1], notice=notice)
        else:
            screen = None
        screen = screen or list_screen(notice=notice)
    if screen:
        await _show(query, screen)


def _prompt(context, text: str, back: tuple, **wait) -> Screen:
    _wait(context, **wait)
    return text, InlineKeyboardMarkup([[_btn("⬅ Back", *back)]])


async def _handle(query, context, action: str, args: list[str]) -> Optional[Screen]:
    if action == "l":
        return list_screen()
    if action == "c":
        return contact_screen()
    if action == "s":
        return skills_screen()
    if action == "n":
        return add_menu_screen()
    if action == "w":
        return watchlist_screen()
    if action == "lk":
        return links_screen()
    if action in ("ty", "tv", "ta", "tx", "tA", "tX", "tz", "tZ", "tg"):
        from bot import tidy
        return await tidy.handle(query, action, args)
    if action == "wa":
        return _prompt(context, "➕ <b>Add a link to watch</b>\n\nSend a career page or a job-board link, for example "
                       "https://boards.greenhouse.io/acme or https://jobs.lever.co/acme.", ("w",), action="source")
    if action == "wm":
        if not args:
            return starters_screen()
        name, url, _ = starter_pages.MALAYSIA[int(args[0])]
        try:
            watchlist.add_source(url, label=name)
        except watchlist.SourceError as err:
            return starters_screen(notice=f"❌ {_h(err)}")
        return starters_screen(notice=f"✅ Added {_h(name)}.")
    if action == "wd":
        gone = watchlist.remove_source(int(args[0]))
        return watchlist_screen(notice="✕ Removed." if gone else "That one was already gone.")
    if action == "wp":
        watchlist.set_setting("scan_paused", "0" if watchlist.scan_paused() else "1")
        return watchlist_screen(notice="⏸ The daily check is paused." if watchlist.scan_paused() else "▶ The daily check is on again.")
    if action == "wn":
        from bot.scanner import start_scan_now
        started = start_scan_now(context.application, query.message.chat_id)
        return watchlist_screen(notice="▶ Check started. Watch it on the dashboard; resumes arrive here as they're ready."
                                if started else "A check is already running.")

    if action == "ce":
        label = dict(editor.CONTACT_FIELDS).get(args[0])
        if label is None:
            return contact_screen()
        hint = "" if args[0] in editor._REQUIRED else "\nSend - to remove it."
        return _prompt(context, f"✏️ <b>New {label}</b>\n\nSend it now.{hint}", ("c",), action="contact", field=args[0])

    if action == "sc":
        return skill_category_screen(int(args[0]), int(args[1]) if len(args) > 1 and args[1].isdigit() else 0)
    if action == "sa":
        category, _ = editor.get_skills(int(args[0]))
        return _prompt(context, f"➕ <b>Add to {_h(category)}</b>\n\nSend the skills separated by commas, for example: "
                       "Python, Go, Rust", ("sc", args[0], 0), action="skills", index=int(args[0]))
    if action == "sd":
        name = editor.remove_skill(int(args[0]), args[1])
        return skill_category_screen(int(args[0]), notice=f"✕ Removed {_h(name)}.")

    if action == "nn":
        kind = args[0]
        if kind not in NEW_PROMPTS:
            return add_menu_screen()
        title, what, example = NEW_PROMPTS[kind]
        return _prompt(context, f"{title}\n\nSend me {what}.\n<i>For example: {_h(example)}</i>", ("n",), action="new", kind=kind)

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
        if action == "e":
            return _prompt(context, f"✏️ <b>Editing {view.noun} {number}</b>\n<i>{_h(_short(item.text))}</i>\n\n"
                           "Send me the new text now. Write it the way it should appear on your resume.",
                           ("o", kind, ref, page), action="edit", kind=kind, ref=ref, token=token)
        if action == "d":
            return (f"🗑 <b>Delete this {view.noun}?</b>\n\n<i>{_h(_short(item.text))}</i>",
                    InlineKeyboardMarkup([[_btn("Yes, delete it", "dy", kind, ref, token), _btn("No, keep it", "o", kind, ref, page)]]))
        editor.delete_item(kind, ref, token)
        return entry_screen(kind, ref, page, notice=f"🗑 Deleted {view.noun} {number}.")

    if action == "a":
        if view is None:
            return _missing()
        extra = "\nI'll work out the technology tags myself." if view.has_tags else ""
        return _prompt(context, f"➕ <b>New {view.noun} for {_h(view.title)}</b>\n\nSend me the text now. Write it the way it "
                       f"should appear on your resume.{extra}", ("o", args[0], args[1], 0), action="add", kind=args[0], ref=args[1])

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
        return _prompt(context, "➕ <b>Add a course</b>\n\nSend me the course name, for example “Operating Systems”.",
                       ("o", EDU, args[0], 0), action="course", kind=EDU, ref=args[0])

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


async def _apply_answer(msg, wait: dict, text: str) -> Screen:
    """Do what the user was asked for with their text. Returns the screen to show next."""
    action = wait["action"]
    if action == "contact":
        editor.set_contact(wait["field"], text)
        return contact_screen(notice=f"✅ {dict(editor.CONTACT_FIELDS)[wait['field']]} updated.")
    if action == "skills":
        added, already = editor.add_skills_text(wait["index"], text)
        notice = ("✅ Added: " + _h(", ".join(added)) if added else "") + \
                 (("\n" if added else "") + "ℹ️ Already there: " + _h(", ".join(already)) if already else "")
        return skill_category_screen(wait["index"], notice=notice)
    if action == "source":
        try:
            src = watchlist.add_source(text)
        except watchlist.SourceError as err:
            raise EditError(str(err))
        return watchlist_screen(notice=f"✅ Added {_h(src.label)}.")
    if action == "new":
        await msg.reply_text("⏳ Reading that and saving it…")
        kind, ref, notice = await asyncio.to_thread(create_from_description, wait["kind"], text)
        return entry_screen(kind, ref, notice=notice) or list_screen(notice=notice)

    kind, ref = wait["kind"], wait["ref"]
    view = editor.get_entry(kind, ref)
    if view is None:
        raise EditError("That entry no longer exists.")
    tags = await asyncio.to_thread(_tags_for, text) if view.has_tags and action in ("edit", "add") else None
    if action == "edit":
        editor.edit_item(kind, ref, wait["token"], text, tags)
        token, notice = wait["token"], "✅ Updated."
    elif action == "add":
        token, notice = editor.add_item(kind, ref, text, tags).token, "✅ Added."
    else:
        added = editor.add_course(ref, text)
        token, notice = None, "✅ Course added." if added else "ℹ️ That course was already there."
    fresh = editor.get_entry(kind, ref)
    return entry_screen(kind, ref, _page_of(fresh, token) if fresh and token else 0, notice=notice)


async def xp_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """If you tapped ✏️ or ➕ and I'm waiting for text, your next message is the answer."""
    wait = context.user_data.get(WAIT_KEY)
    text = (update.effective_message.text or "").strip() if update.effective_message else ""
    if not wait or not text:
        return   # not for us: other handlers deal with this message
    if time.time() - wait["at"] > WAIT_SECONDS:
        _clear_wait(context)
        return

    msg = update.effective_message
    try:
        screen = await _apply_answer(msg, wait, text)
    except EditError as err:
        wait["at"] = time.time()   # keep waiting: the user can send a corrected text
        await msg.reply_text(f"⚠️ {_h(err)}", parse_mode="HTML")
        raise ApplicationHandlerStop
    except Exception as err:
        logger.error("Could not save the change: %s", err)
        wait["at"] = time.time()   # the AI or the disk hiccuped: the same text (or a new one) can be sent again
        await msg.reply_text(f"❌ I couldn't save that.\nReason: {_h(err)}\nSend it again, or /cancel.", parse_mode="HTML")
        raise ApplicationHandlerStop

    _clear_wait(context)
    await msg.reply_text(screen[0], parse_mode="HTML", reply_markup=screen[1])
    raise ApplicationHandlerStop
