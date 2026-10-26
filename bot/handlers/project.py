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
from bot.extraction import extract_project
from service.admin import (
    _load_skeleton_raw,
    add_project,
    delete_project,
)

logger = logging.getLogger(__name__)

WAITING_ADD_PROJ = 1


@restricted
async def add_project_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        raw_text = " ".join(context.args).strip()
        return await _process_and_save_project(update, raw_text)

    await update.effective_message.reply_text(
        "🚀 *Please describe your project:*\n\n"
        "Include: Project Name, Tech Stack, Completion Date, and your achievements or features built.\n"
        "_Example: Built TaskFlow, a real-time task manager using React, Node.js, and PostgreSQL in Dec 2024. Integrated Docker and implemented WebSocket notifications for 500+ users._\n\n"
        "(Or send /cancel to abort)",
        parse_mode="Markdown",
    )
    return WAITING_ADD_PROJ


async def add_project_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw_text = update.effective_message.text.strip()
    return await _process_and_save_project(update, raw_text)


async def _process_and_save_project(update: Update, raw_text: str):
    await update.effective_message.reply_text("⏳ *Extracting project details & indexing into Fact Store...*", parse_mode="Markdown")
    try:
        req = extract_project(raw_text)
        res = add_project(req)

        bullet_previews = "\n".join(f"  • {b.text}" for b in req.bullets)
        skills_summary = ", ".join(f"{cat}: {', '.join(items)}" for cat, items in (res.skills_added or {}).items()) or "None"

        await update.effective_message.reply_text(
            f"✅ *Project Added!*\n\n"
            f"• *ID:* `{res.id}`\n"
            f"• *Project:* {req.name} ({req.date})\n"
            f"• *Tech Stack:* {req.tech_stack}\n\n"
            f"*Generated Fact Bullets:*\n{bullet_previews}\n\n"
            f"*New Skills Indexed:* {skills_summary}",
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.error("Failed to add project: %s", e)
        await update.effective_message.reply_text(f"❌ Failed to parse/add project: {e}")
    return ConversationHandler.END


@restricted
async def delete_project_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        proj_id = context.args[0].strip()
        try:
            res = delete_project(proj_id)
            await update.effective_message.reply_text(
                f"✅ Deleted project `{proj_id}` and purged {res.facts_deleted} associated fact bullets.",
                parse_mode="Markdown",
            )
        except Exception as e:
            await update.effective_message.reply_text(f"❌ Error: {e}")
        return

    skeleton = _load_skeleton_raw()
    projs = skeleton.get("project_entries", [])
    if not projs:
        await update.effective_message.reply_text("No project entries found.")
        return

    keyboard = [
        [InlineKeyboardButton(f"🗑️ {p['name']} ({p['id']})", callback_data=f"del_proj:{p['id']}")]
        for p in projs
    ]
    await update.effective_message.reply_text(
        "Select a project entry to delete:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def delete_project_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith("del_proj:"):
        proj_id = data.split(":", 1)[1]
        try:
            res = delete_project(proj_id)
            await query.edit_message_text(
                f"✅ Deleted project `{proj_id}` and purged {res.facts_deleted} associated fact bullets.",
                parse_mode="Markdown",
            )
        except Exception as e:
            await query.edit_message_text(f"❌ Error deleting `{proj_id}`: {e}")


def get_project_conversation_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CommandHandler("addproject", add_project_start),
        ],
        states={
            WAITING_ADD_PROJ: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_project_received)],
        },
        fallbacks=[CommandHandler("cancel", cancel_command)],
        allow_reentry=True,
    )

