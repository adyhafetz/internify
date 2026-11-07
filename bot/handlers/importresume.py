"""Send the bot your current resume (PDF, Word or text) and it fills in your saved experience for you.

Flow: file -> read text -> AI structures it -> a preview of what was found -> you choose Replace / Add / Cancel.
Nothing is saved until you tap a button, and Replace keeps a backup. The typed commands (/addexperience, ...) stay
available for fixing or adding anything afterwards."""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import time
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.extraction import ParsedResume, extract_resume
from bot.handlers.common import restricted
from bot.resume_import import MAX_FILE_BYTES, ResumeFileError, read_resume_text
from service.importer import apply_resume, profile_is_sample

logger = logging.getLogger(__name__)

PENDING_KEY = "resume_import"
PENDING_SECONDS = 30 * 60   # a preview that nobody confirmed expires, so a stale tap can't overwrite anything


def _h(text: object) -> str:
    return escape(str(text), quote=False)


def _more(items: list[str], limit: int) -> list[str]:
    return items[:limit] + ([f"…and {len(items) - limit} more"] if len(items) > limit else [])


def build_preview(parsed: ParsedResume, sample_profile: bool) -> str:
    """What the AI found, in plain words, so the user can check it before anything is saved."""
    h = parsed.header
    contact = " · ".join(x for x in (h.name, h.email, h.phone) if x) or "not found"
    skill_total = sum(len(v) for v in parsed.skills.values())
    bullets = sum(len(e.bullets) for e in parsed.experience) + sum(len(p.bullets) for p in parsed.projects)

    lines = ["📄 <b>I read your resume. Here's what I found:</b>", "", f"👤 {_h(contact)}"]
    if parsed.education:
        lines += ["", f"🎓 <b>Education ({len(parsed.education)})</b>"]
        lines += [f"• {_h(e.degree_line)}, {_h(e.institution)}" for e in parsed.education[:4]]
    if parsed.experience:
        lines += ["", f"💼 <b>Experience ({len(parsed.experience)})</b>"]
        lines += _more([f"• {_h(e.title)}, {_h(e.company)} ({len(e.bullets)} bullet points)" for e in parsed.experience], 6)
    if parsed.projects:
        lines += ["", f"🛠 <b>Projects ({len(parsed.projects)})</b>"]
        lines += _more([f"• {_h(p.name)} ({len(p.bullets)} bullet points)" for p in parsed.projects], 6)
    if parsed.leadership:
        lines += ["", f"🏅 <b>Leadership and activities ({len(parsed.leadership)})</b>"]
        lines += _more([f"• {_h(e.title)}, {_h(e.org)}" for e in parsed.leadership], 4)
    if skill_total:
        lines += ["", f"🔧 <b>Skills:</b> {skill_total} in {len([v for v in parsed.skills.values() if v])} categories"]
    lines += ["", f"That's {bullets} bullet points I can choose from when tailoring."]
    if sample_profile:
        lines += ["", "ℹ️ Your saved profile is still the sample one (Jane Doe), so <b>Replace</b> is the right choice."]
    lines += ["", "<b>What should I do with it?</b>"]
    return "\n".join(lines)


def _buttons(sample_profile: bool) -> InlineKeyboardMarkup:
    replace = "♻️ Replace what's saved (recommended)" if sample_profile else "♻️ Replace what's saved"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(replace, callback_data="imp:replace")],
        [InlineKeyboardButton("➕ Add to what's saved", callback_data="imp:add")],
        [InlineKeyboardButton("Cancel", callback_data="imp:cancel")],
    ])


@restricted
async def importresume_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "📄 Send me your current resume as a file and I'll fill in your experience for you.\n\n"
        "• PDF, Word (.docx) or text files, up to 5 MB\n"
        "• I show you what I found first, and nothing is saved until you confirm\n"
        "• The file is read by the AI service you set up, and I don't keep it\n\n"
        "You can still add or change anything one by one afterwards (/addexperience, /addproject, /updatefact…)."
    )


@restricted
async def resume_document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    doc = msg.document
    name = doc.file_name or "resume"
    if doc.file_size and doc.file_size > MAX_FILE_BYTES:
        await msg.reply_text("That file is bigger than 5 MB. A resume should be much smaller. Check you sent the right file.")
        return

    status = await msg.reply_text("📄 Reading your resume…")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "resume" + os.path.splitext(name.lower())[1])
            tg_file = await doc.get_file()
            await tg_file.download_to_drive(custom_path=path)
            text = await asyncio.to_thread(read_resume_text, path, name)
        await status.edit_text("🧠 Finding your education, jobs, projects and skills…")
        parsed = await asyncio.to_thread(extract_resume, text)
    except ResumeFileError as err:
        await status.edit_text(f"❌ {err}")
        return
    except Exception as err:
        logger.error("Resume import failed: %s", err)
        await status.edit_text(f"❌ Something went wrong while reading your resume.\nReason: {err}")
        return

    if parsed.is_empty():
        await status.edit_text("❌ I couldn't find any resume sections in that file. Is it your resume? "
                               "Try another file, or add things one by one with /addexperience and /addproject.")
        return

    sample = await asyncio.to_thread(profile_is_sample)
    context.user_data[PENDING_KEY] = {"parsed": parsed, "at": time.time()}
    await status.edit_text(build_preview(parsed, sample), parse_mode="HTML", reply_markup=_buttons(sample))


def build_summary(report, mode: str) -> str:
    a = report.added
    parts = [f"{a['education']} education", f"{a['experience']} job{'s' if a['experience'] != 1 else ''}",
             f"{a['projects']} project{'s' if a['projects'] != 1 else ''}", f"{a['leadership']} leadership role{'s' if a['leadership'] != 1 else ''}"]
    lines = ["✅ <b>Imported!</b>", "", "• " + " · ".join(parts), f"• {a['bullets']} bullet points · {a['skills']} skills"]
    if report.skipped:
        lines.append(f"• Skipped {len(report.skipped)} already saved: " + _h(", ".join(report.skipped[:4])))
    lines += [f"⚠️ {_h(w)}" for w in report.warnings[:4]]
    if report.backup_dir:
        lines += ["", f"💾 Your previous profile is backed up in <code>{_h(report.backup_dir)}</code>."]
    lines += ["", "Check it with /listentries and /listfacts. Fix or add anything with /addexperience, /addproject, "
              "/updatefact and the other commands. Then send me a job link!"]
    return "\n".join(lines)


@restricted
async def import_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action = (query.data or "").split(":", 1)[-1]
    pending = context.user_data.pop(PENDING_KEY, None)   # popped first, so a double tap can't import twice

    if action == "cancel":
        await query.edit_message_text("👍 Okay, nothing was changed.")
        return
    if action not in ("replace", "add") or not pending or time.time() - pending["at"] > PENDING_SECONDS:
        await query.edit_message_text("That import has expired. Send me your resume file again.")
        return

    await query.edit_message_text("⏳ Saving your experience…")
    try:
        report = await asyncio.to_thread(apply_resume, pending["parsed"], action)
    except Exception as err:
        logger.error("Resume import apply failed: %s", err)
        await query.edit_message_text(f"❌ I couldn't save it.\nReason: {err}")
        return
    await query.edit_message_text(build_summary(report, action), parse_mode="HTML")
