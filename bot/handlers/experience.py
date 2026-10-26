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
from bot.extraction import extract_experience
from service.admin import (
    _load_skeleton_raw,
    add_experience,
    delete_experience,
)

logger = logging.getLogger(__name__)

WAITING_ADD_EXP = 1


@restricted
async def add_experience_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        raw_text = " ".join(context.args).strip()
        return await _process_and_save_experience(update, raw_text)

    await update.effective_message.reply_text(
        "💼 *Please describe your work experience or internship:*\n\n"
        "Include: Role Title, Company name, Location, Dates, and your accomplishments or responsibilities.\n"
        "_Example: Software Engineering Intern at Tech Corp in Kuala Lumpur from Jun 2024 to Aug 2024. Developed REST APIs with FastAPI and reduced latency by 30%._\n\n"
        "(Or send /cancel to abort)",
        parse_mode="Markdown",
    )
    return WAITING_ADD_EXP


async def add_experience_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw_text = update.effective_message.text.strip()
    return await _process_and_save_experience(update, raw_text)


async def _process_and_save_experience(update: Update, raw_text: str):
    await update.effective_message.reply_text("⏳ *Extracting experience & indexing bullets into Fact Store...*", parse_mode="Markdown")
    try:
        req = extract_experience(raw_text)
        res = add_experience(req)

        bullet_previews = "\n".join(f"  • {b.text}" for b in req.bullets)
        skills_summary = ", ".join(f"{cat}: {', '.join(items)}" for cat, items in (res.skills_added or {}).items()) or "None"

        await update.effective_message.reply_text(
            f"✅ *Work Experience Added!*\n\n"
            f"• *ID:* `{res.id}`\n"
            f"• *Role:* {req.title} at {req.company}\n"
            f"• *Dates:* {req.dates} ({req.location})\n\n"
            f"*Generated Fact Bullets:*\n{bullet_previews}\n\n"
            f"*New Skills Indexed:* {skills_summary}",
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.error("Failed to add experience: %s", e)
        await update.effective_message.reply_text(f"❌ Failed to parse/add experience: {e}")
    return ConversationHandler.END


@restricted
async def delete_experience_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        exp_id = context.args[0].strip()
        try:
            res = delete_experience(exp_id)
            await update.effective_message.reply_text(
                f"✅ Deleted experience `{exp_id}` and purged {res.facts_deleted} associated fact bullets.",
                parse_mode="Markdown",
            )
        except Exception as e:
            await update.effective_message.reply_text(f"❌ Error: {e}")
        return

    skeleton = _load_skeleton_raw()
    exps = skeleton.get("experience_entries", [])
    if not exps:
        await update.effective_message.reply_text("No experience entries found.")
        return

    keyboard = [
        [InlineKeyboardButton(f"🗑️ {e['title']} @ {e['company']}", callback_data=f"del_exp:{e['id']}")]
        for e in exps
    ]
    await update.effective_message.reply_text(
        "Select a work experience entry to delete:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def delete_experience_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith("del_exp:"):
        exp_id = data.split(":", 1)[1]
        try:
            res = delete_experience(exp_id)
            await query.edit_message_text(
                f"✅ Deleted experience `{exp_id}` and purged {res.facts_deleted} associated fact bullets.",
                parse_mode="Markdown",
            )
        except Exception as e:
            await query.edit_message_text(f"❌ Error deleting `{exp_id}`: {e}")


def get_experience_conversation_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CommandHandler("addexperience", add_experience_start),
        ],
        states={
            WAITING_ADD_EXP: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_experience_received)],
        },
        fallbacks=[CommandHandler("cancel", cancel_command)],
        allow_reentry=True,
    )

