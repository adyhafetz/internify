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
from bot.extraction import extract_tech_tags
from service.admin import (
    _load_skeleton_raw,
    _load_facts_raw,
    update_fact,
    delete_fact,
    UpdateFactRequest,
)

logger = logging.getLogger(__name__)

WAITING_UPDATE_FACT = 1


@restricted
async def listentries_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    skeleton = _load_skeleton_raw()
    exps = skeleton.get("experience_entries", [])
    projs = skeleton.get("project_entries", [])
    leads = skeleton.get("leadership", [])

    lines = ["📋 *All Resume Entries:*\n"]

    lines.append("*Work Experiences:*")
    if exps:
        for e in exps:
            lines.append(f"  • `{e['id']}`: {e['title']} @ {e['company']}")
    else:
        lines.append("  _None_")

    lines.append("\n*Projects:*")
    if projs:
        for p in projs:
            lines.append(f"  • `{p['id']}`: {p['name']}")
    else:
        lines.append("  _None_")

    lines.append("\n*Leadership:*")
    if leads:
        for l in leads:
            lines.append(f"  • {l['title']} @ {l['org']}")
    else:
        lines.append("  _None_")

    lines.append("\n_Use `/listfacts <entry_id>` to view the specific bullets for an entry._")
    await update.effective_message.reply_text("\n".join(lines), parse_mode="Markdown")


@restricted
async def listfacts_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.effective_message.reply_text(
            "Usage: `/listfacts <entry_id>`\n"
            "_Run `/listentries` to see valid entry IDs (e.g. `exp_sample_tech` or `proj_sample_app`)._",
            parse_mode="Markdown",
        )
        return

    entry_id = context.args[0].strip()
    facts = _load_facts_raw()
    matching = [f for f in facts if f["entry_id"] == entry_id]

    if not matching:
        await update.effective_message.reply_text(
            f"No fact bullets found for entry `{entry_id}`. Run `/listentries` to check IDs.",
            parse_mode="Markdown",
        )
        return

    lines = [f"📝 *Fact Bullets for* `{entry_id}`:\n"]
    for f in matching:
        fid = f["id"]
        txt = f["text"]
        tags = ", ".join(f.get("tech_tags", [])) or "None"
        lines.append(f"• *ID:* `{fid}`\n  {txt}\n  _Tags: {tags}_\n")

    lines.append("_To edit: `/updatefact <fact_id> <new text>`_\n_To delete: `/deletefact <fact_id>`_")
    await update.effective_message.reply_text("\n".join(lines), parse_mode="Markdown")


@restricted
async def update_fact_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) >= 2:
        fact_id = context.args[0].strip()
        new_text = " ".join(context.args[1:]).strip()
        return await _process_and_update_fact(update, fact_id, new_text)

    await update.effective_message.reply_text(
        "📝 *Please enter the fact ID followed by the new bullet text:*\n\n"
        "_Example: `fact_01 Developed backend APIs using FastAPI and reduced response time by 40%`_\n\n"
        "(Or send /cancel to abort)",
        parse_mode="Markdown",
    )
    return WAITING_UPDATE_FACT


async def update_fact_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.effective_message.text.strip()
    parts = text.split(" ", 1)
    if len(parts) < 2:
        await update.effective_message.reply_text("Please provide both the fact ID and the new text (or /cancel).")
        return WAITING_UPDATE_FACT
    return await _process_and_update_fact(update, parts[0].strip(), parts[1].strip())


async def _process_and_update_fact(update: Update, fact_id: str, new_text: str):
    await update.effective_message.reply_text("⏳ *Re-indexing bullet & extracting tech tags...*", parse_mode="Markdown")
    try:
        tech_tags = extract_tech_tags(new_text)
        res = update_fact(fact_id, UpdateFactRequest(text=new_text, tech_tags=tech_tags))
        await update.effective_message.reply_text(
            f"✅ *Fact Bullet Updated!*\n\n"
            f"• *ID:* `{fact_id}`\n"
            f"• *Text:* {new_text}\n"
            f"• *Tags:* {', '.join(tech_tags) or 'None'}",
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.error("Failed to update fact %s: %s", fact_id, e)
        await update.effective_message.reply_text(f"❌ Error updating fact: {e}")
    return ConversationHandler.END


@restricted
async def delete_fact_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.effective_message.reply_text("Usage: `/deletefact <fact_id>`", parse_mode="Markdown")
        return

    fact_id = context.args[0].strip()
    try:
        delete_fact(fact_id)
        await update.effective_message.reply_text(f"✅ Deleted fact bullet `{fact_id}`.", parse_mode="Markdown")
    except Exception as e:
        await update.effective_message.reply_text(f"❌ Error deleting fact: {e}")


def get_facts_conversation_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CommandHandler("updatefact", update_fact_start),
        ],
        states={
            WAITING_UPDATE_FACT: [MessageHandler(filters.TEXT & ~filters.COMMAND, update_fact_received)],
        },
        fallbacks=[CommandHandler("cancel", cancel_command)],
        allow_reentry=True,
    )

