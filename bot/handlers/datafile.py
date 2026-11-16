"""Importing a data file (📦 My data in /profile): you send the .zip that Export gave you, check what is in it, and confirm.
Nothing is changed until you tap the button, and importing replaces what you have now (a safety copy is saved first)."""
from __future__ import annotations

import asyncio
import logging
import time
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot import data_export
from bot.handlers.common import restricted

logger = logging.getLogger(__name__)

PENDING_KEY = "data_import"
PENDING_SECONDS = 30 * 60


def _h(text: object) -> str:
    return escape(str(text), quote=False)


def preview_text(pv: data_export.Preview) -> str:
    lines = ["📦 <b>An Internify data file</b>" + (f" from {_h(pv.when)}" if pv.when else ""), ""]
    lines.append(f"👤 {_h(pv.name) or 'unnamed profile'}")
    lines.append(f"• {pv.facts} bullet points in {pv.entries} jobs and projects")
    lines.append(f"• {pv.applications} application{'s' if pv.applications != 1 else ''} in the tracker, {pv.pages} job page{'s' if pv.pages != 1 else ''}, {pv.seen} jobs already seen")
    if pv.settings:
        lines.append("• your job-search settings")
    lines += ["", "⚠️ <b>Importing replaces</b> your saved experience, tracker, job pages and job-search settings with this file's. "
              "I save a safety copy of what you have now first, and keys, logins and your chat stay as they are."]
    return "\n".join(lines)


@restricted
async def data_zip_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """A .zip file was sent: check it and show what is in it."""
    msg = update.effective_message
    doc = msg.document
    if doc.file_size and doc.file_size > data_export.MAX_ZIP_BYTES:
        await msg.reply_text("That file is bigger than 20 MB, so it isn't an Internify data file.")
        return
    status = await msg.reply_text("📦 Checking the file…")
    try:
        tg_file = await doc.get_file()
        data = bytes(await tg_file.download_as_bytearray())
        pv, content = await asyncio.to_thread(data_export.inspect, data)
    except data_export.DataFileError as err:
        await status.edit_text(f"❌ {err}")
        return
    except Exception as err:
        logger.error("Data file check failed: %s", err)
        await status.edit_text(f"❌ I couldn't read that file.\nReason: {err}")
        return
    context.user_data[PENDING_KEY] = {"content": content, "at": time.time()}
    await status.edit_text(preview_text(pv), parse_mode="HTML", reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("♻️ Replace what I have with this", callback_data="dim:go")],
        [InlineKeyboardButton("Cancel", callback_data="dim:cancel")]]))


@restricted
async def data_import_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action = (query.data or "").split(":", 1)[-1]
    pending = context.user_data.pop(PENDING_KEY, None)   # popped first, so a double tap can't import twice
    if action == "cancel":
        await query.edit_message_text("👍 Okay, nothing was changed.")
        return
    if action != "go" or not pending or time.time() - pending["at"] > PENDING_SECONDS:
        await query.edit_message_text("That import has expired. Send me the data file again.")
        return
    await query.edit_message_text("⏳ Saving a safety copy, then importing…")
    try:
        report = await asyncio.to_thread(data_export.restore, pending["content"])
    except Exception as err:
        logger.error("Data import failed: %s", err)
        await query.edit_message_text(f"❌ I couldn't import it. Nothing you had was removed before the safety copy was made.\nReason: {err}")
        return
    c = report["counts"]
    await query.edit_message_text(
        "✅ <b>Imported!</b>\n\n"
        f"• {report['facts']} bullet points · {c.get('applications', 0)} applications · {c.get('watch_sources', 0)} job pages\n"
        f"• 💾 Your earlier data is saved as <code>{_h(report['safety_copy'])}</code>\n\n"
        "Check it with /profile and /applied. If you use Google Sheets, your sheet is unchanged: set it up again if this is a new copy.",
        parse_mode="HTML")
