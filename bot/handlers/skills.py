from __future__ import annotations
import logging
from telegram import Update
from telegram.ext import (
    ContextTypes,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    filters,
)
from bot.handlers.common import restricted, cancel_command
from bot.extraction import extract_skills
from service.admin import (
    _load_skeleton_raw,
    add_skills,
    delete_skill,
    AddSkillsRequest,
)

logger = logging.getLogger(__name__)

WAITING_ADD_SKILL = 1


@restricted
async def listskills_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    skeleton = _load_skeleton_raw()
    skills = skeleton.get("skills", {})

    if not skills:
        await update.effective_message.reply_text("🛠️ No skills found in skeleton.", parse_mode="Markdown")
        return

    lines = ["🛠️ *Master Technical Skills:*\n"]
    for cat, items in skills.items():
        joined_items = ", ".join(items) if items else "_None_"
        lines.append(f"• *{cat}:*\n  {joined_items}\n")

    await update.effective_message.reply_text("\n".join(lines), parse_mode="Markdown")


@restricted
async def add_skill_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        raw_text = " ".join(context.args).strip()
        return await _process_and_save_skill(update, raw_text)

    await update.effective_message.reply_text(
        "🛠️ *Please enter the skills you want to add:*\n\n"
        "_Example: Add Docker, Kubernetes, Terraform to infra tools_\n"
        "_Or simply: Python, Golang, Rust_\n\n"
        "(Or send /cancel to abort)",
        parse_mode="Markdown",
    )
    return WAITING_ADD_SKILL


async def add_skill_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw_text = update.effective_message.text.strip()
    return await _process_and_save_skill(update, raw_text)


async def _process_and_save_skill(update: Update, raw_text: str):
    await update.effective_message.reply_text("⏳ *Categorizing skills...*", parse_mode="Markdown")
    try:
        extracted = extract_skills(raw_text)
        res = add_skills(AddSkillsRequest(category=extracted.category, items=extracted.items))
        await update.effective_message.reply_text(
            f"✅ *Skills Added!*\n"
            f"• *Category:* {extracted.category}\n"
            f"• *Items:* {', '.join(extracted.items)}",
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.error("Failed to add skill: %s", e)
        await update.effective_message.reply_text(f"❌ Failed to parse/add skill: {e}")
    return ConversationHandler.END


@restricted
async def delete_skill_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        item = " ".join(context.args).strip()
        try:
            res = delete_skill(item=item)
            await update.effective_message.reply_text(
                f"✅ Removed *{item}* from skills.",
                parse_mode="Markdown",
            )
        except Exception as e:
            await update.effective_message.reply_text(f"❌ Error: {e}")
        return

    await update.effective_message.reply_text(
        "Usage: `/deleteskill <skill name>`\n_Example: `/deleteskill Docker`_",
        parse_mode="Markdown",
    )


def get_skills_conversation_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CommandHandler("addskill", add_skill_start),
        ],
        states={
            WAITING_ADD_SKILL: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_skill_received)],
        },
        fallbacks=[CommandHandler("cancel", cancel_command)],
        allow_reentry=True,
    )

