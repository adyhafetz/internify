from __future__ import annotations
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
)
from bot.handlers.common import restricted, cancel_command
from bot.extraction import extract_education
from service.admin import (
    _load_skeleton_raw,
    add_education,
    delete_education,
    add_course,
    delete_course,
)

logger = logging.getLogger(__name__)

WAITING_ADD_EDU, WAITING_ADD_COURSE, WAITING_DELETE_COURSE = range(3)


@restricted
async def listeducation_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    skeleton = _load_skeleton_raw()
    education = skeleton.get("education", [])

    if not education:
        await update.effective_message.reply_text("🎓 *No education entries found.* Use `/addeducation` to add one.", parse_mode="Markdown")
        return

    msg_lines = ["🎓 *Education Entries:*\n"]
    keyboard = []

    for edu in education:
        eid = edu["id"]
        inst = edu["institution"]
        deg = edu["degree_line"]
        dates = edu["dates"]
        loc = edu["location"]
        courses = ", ".join(edu.get("courses", [])) or "None"
        honors = "; ".join(edu.get("honor_bullets", [])) or "None"

        msg_lines.append(
            f"• *ID:* `{eid}`\n"
            f"  *Institution:* {inst}\n"
            f"  *Degree:* {deg}\n"
            f"  *Dates:* {dates} | {loc}\n"
            f"  *Courses:* {courses}\n"
            f"  *Honors:* {honors}\n"
        )
        keyboard.append([InlineKeyboardButton(f"🗑️ Delete {inst[:20]}", callback_data=f"del_edu:{eid}")])

    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    await update.effective_message.reply_text("\n".join(msg_lines), parse_mode="Markdown", reply_markup=reply_markup)


# ---------- /addeducation ----------

@restricted
async def add_education_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        raw_text = " ".join(context.args).strip()
        return await _process_and_save_education(update, raw_text)

    await update.effective_message.reply_text(
        "🎓 *Please describe your education details*\n\n"
        "Include your University name, Degree, Graduation dates, Location, Courses, and GPA/Honors.\n"
        "_Example: Bachelor of Computer Science at University of Malaya, 2022-2026, GPA 3.8/4.0. Relevant courses: Data Structures, AI, Database Systems._\n\n"
        "(Or send /cancel to abort)",
        parse_mode="Markdown",
    )
    return WAITING_ADD_EDU


async def add_education_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw_text = update.effective_message.text.strip()
    return await _process_and_save_education(update, raw_text)


async def _process_and_save_education(update: Update, raw_text: str):
    await update.effective_message.reply_text("⏳ *Extracting education details with AI...*", parse_mode="Markdown")
    try:
        req = extract_education(raw_text)
        res = add_education(req)
        await update.effective_message.reply_text(
            f"✅ *Education Added!*\n"
            f"• *ID:* `{res.id}`\n"
            f"• *Institution:* {req.institution}\n"
            f"• *Degree:* {req.degree_line}\n"
            f"• *Dates:* {req.dates}\n"
            f"• *Courses:* {', '.join(req.courses) or 'None'}",
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.error("Failed to add education: %s", e)
        await update.effective_message.reply_text(f"❌ Failed to parse/add education: {e}")
    return ConversationHandler.END


# ---------- /deleteeducation ----------

@restricted
async def delete_education_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        edu_id = context.args[0].strip()
        try:
            delete_education(edu_id)
            await update.effective_message.reply_text(f"✅ Deleted education entry `{edu_id}`.", parse_mode="Markdown")
        except Exception as e:
            await update.effective_message.reply_text(f"❌ Error deleting `{edu_id}`: {e}")
        return

    # No arg: show inline list
    skeleton = _load_skeleton_raw()
    education = skeleton.get("education", [])
    if not education:
        await update.effective_message.reply_text("No education entries to delete.")
        return

    keyboard = [
        [InlineKeyboardButton(f"🗑️ {e['institution']} ({e['id']})", callback_data=f"del_edu:{e['id']}")]
        for e in education
    ]
    await update.effective_message.reply_text(
        "Select an education entry to delete:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def delete_education_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith("del_edu:"):
        edu_id = data.split(":", 1)[1]
        try:
            delete_education(edu_id)
            await query.edit_message_text(f"✅ Deleted education entry `{edu_id}`.", parse_mode="Markdown")
        except Exception as e:
            await query.edit_message_text(f"❌ Error deleting `{edu_id}`: {e}")


# ---------- /addcourse & /deletecourse ----------

@restricted
async def add_course_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) >= 2:
        edu_id = context.args[0].strip()
        course_name = " ".join(context.args[1:]).strip()
        try:
            add_course(edu_id, course_name)
            await update.effective_message.reply_text(
                f"✅ Added *{course_name}* to `{edu_id}`.",
                parse_mode="Markdown",
            )
        except Exception as e:
            await update.effective_message.reply_text(f"❌ Error: {e}")
        return

    await update.effective_message.reply_text(
        "Usage: `/addcourse <education_id> <course name>`\n"
        "_Run `/listeducation` to find your education IDs._",
        parse_mode="Markdown",
    )


@restricted
async def delete_course_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) >= 2:
        edu_id = context.args[0].strip()
        course_name = " ".join(context.args[1:]).strip()
        try:
            delete_course(edu_id, course_name)
            await update.effective_message.reply_text(
                f"✅ Removed *{course_name}* from `{edu_id}`.",
                parse_mode="Markdown",
            )
        except Exception as e:
            await update.effective_message.reply_text(f"❌ Error: {e}")
        return

    await update.effective_message.reply_text(
        "Usage: `/deletecourse <education_id> <course name>`\n"
        "_Run `/listeducation` to find your education IDs._",
        parse_mode="Markdown",
    )


def get_education_conversation_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CommandHandler("addeducation", add_education_start),
        ],
        states={
            WAITING_ADD_EDU: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_education_received)],
        },
        fallbacks=[CommandHandler("cancel", cancel_command)],
        allow_reentry=True,
    )

