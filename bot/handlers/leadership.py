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
from bot.extraction import extract_leadership
from service.admin import (
    _load_skeleton_raw,
    add_leadership,
    delete_leadership,
)

logger = logging.getLogger(__name__)

WAITING_ADD_LEAD = 1


@restricted
async def add_leadership_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        raw_text = " ".join(context.args).strip()
        return await _process_and_save_leadership(update, raw_text)

    await update.effective_message.reply_text(
        "🏅 *Please describe your leadership or extracurricular role:*\n\n"
        "Include: Title, Organization or Society name, Dates, Location, and key activities or impact.\n"
        "_Example: Vice President at Computer Science Society from Aug 2023 to May 2024. Organized hackathons and tech talks for 200+ active student members._\n\n"
        "(Or send /cancel to abort)",
        parse_mode="Markdown",
    )
    return WAITING_ADD_LEAD


async def add_leadership_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw_text = update.effective_message.text.strip()
    return await _process_and_save_leadership(update, raw_text)


async def _process_and_save_leadership(update: Update, raw_text: str):
    await update.effective_message.reply_text("⏳ *Extracting leadership role...*", parse_mode="Markdown")
    try:
        req = extract_leadership(raw_text)
        add_leadership(req)

        bullet_previews = "\n".join(f"  • {b}" for b in req.bullets)

        await update.effective_message.reply_text(
            f"✅ *Leadership Experience Added!*\n\n"
            f"• *Title:* {req.title}\n"
            f"• *Organization:* {req.org}\n"
            f"• *Dates:* {req.dates} ({req.location})\n\n"
            f"*Bullets:*\n{bullet_previews}",
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.error("Failed to add leadership: %s", e)
        await update.effective_message.reply_text(f"❌ Failed to parse/add leadership: {e}")
    return ConversationHandler.END


@restricted
async def delete_leadership_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        title = " ".join(context.args).strip()
        try:
            delete_leadership(title)
            await update.effective_message.reply_text(f"✅ Deleted leadership entry *{title}*.", parse_mode="Markdown")
        except Exception as e:
            await update.effective_message.reply_text(f"❌ Error: {e}")
        return

    skeleton = _load_skeleton_raw()
    leads = skeleton.get("leadership", [])
    if not leads:
        await update.effective_message.reply_text("No leadership entries found.")
        return

    keyboard = [
        [InlineKeyboardButton(f"🗑️ {l['title']} ({l['org']})", callback_data=f"del_lead:{l['title']}")]
        for l in leads
    ]
    await update.effective_message.reply_text(
        "Select a leadership entry to delete:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def delete_leadership_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith("del_lead:"):
        title = data.split(":", 1)[1]
        try:
            delete_leadership(title)
            await query.edit_message_text(f"✅ Deleted leadership entry *{title}*.", parse_mode="Markdown")
        except Exception as e:
            await query.edit_message_text(f"❌ Error deleting *{title}*: {e}")


def get_leadership_conversation_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CommandHandler("addleadership", add_leadership_start),
        ],
        states={
            WAITING_ADD_LEAD: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_leadership_received)],
        },
        fallbacks=[CommandHandler("cancel", cancel_command)],
        allow_reentry=True,
    )

