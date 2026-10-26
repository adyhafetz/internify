from __future__ import annotations
import os
import logging
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler
from bot.handlers.common import restricted
from bot.services.drive import upload_to_drive
from service.admin import generate_master_resume

logger = logging.getLogger(__name__)


@restricted
async def master_resume_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status_msg = await update.effective_message.reply_text("⏳ *Compiling untailored master resume PDF with Tectonic...*", parse_mode="Markdown")
    try:
        res = generate_master_resume()
        pdf_path = res.get("pdf_path")
        filename = res.get("filename", "Master_Resume.pdf")

        if not pdf_path or not os.path.exists(pdf_path):
            await status_msg.edit_text("❌ *Error:* PDF was not generated.")
            return

        # Attempt Google Drive upload if configured
        drive_link = upload_to_drive(pdf_path, filename)

        caption = f"📄 *Master Resume Compiled!*\n• *Candidate:* {res.get('name')}"
        if drive_link:
            caption += f"\n• ☁️ [View in Google Drive]({drive_link})"

        with open(pdf_path, "rb") as pdf_file:
            await update.effective_message.reply_document(
                document=pdf_file,
                filename=filename,
                caption=caption,
                parse_mode="Markdown",
            )
        await status_msg.delete()

    except Exception as e:
        logger.error("Error generating master resume: %s", e)
        await status_msg.edit_text(f"❌ *LaTeX Compilation Failed:*\n`{e}`", parse_mode="Markdown")

