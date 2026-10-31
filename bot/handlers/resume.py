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
    
    # 1. LaTeX Compilation
    try:
        res = generate_master_resume()
        pdf_path = res.get("pdf_path")
        filename = res.get("filename", "Master_Resume.pdf")

        if not pdf_path or not os.path.exists(pdf_path):
            await status_msg.edit_text("❌ *LaTeX Compilation Error:* PDF was not generated.")
            return
    except Exception as comp_err:
        logger.error("Error compiling master resume with Tectonic: %s", comp_err)
        await status_msg.edit_text(f"❌ *LaTeX Compilation Failed:*\n`{comp_err}`", parse_mode="Markdown")
        return

    # 2. Upload to Google Drive (Optional Cloud)
    drive_link = None
    try:
        drive_link = upload_to_drive(pdf_path, filename)
    except Exception as drive_err:
        logger.warning("Google Drive upload failed: %s", drive_err)

    caption = f"📄 *Master Resume Compiled!*\n• *Candidate:* {res.get('name')}"
    if drive_link:
        caption += f"\n• ☁️ [View in Google Drive]({drive_link})"

    # 3. Deliver PDF to Telegram
    try:
        with open(pdf_path, "rb") as pdf_file:
            await update.effective_message.reply_document(
                document=pdf_file,
                filename=filename,
                caption=caption,
                parse_mode="Markdown",
                write_timeout=60,
                read_timeout=60,
            )
        await status_msg.delete()
    except Exception as upload_err:
        logger.error("Failed to send PDF over Telegram: %s", upload_err)
        msg_text = (
            f"⚠️ *PDF compiled successfully*, but delivery to Telegram failed due to network timeout:\n`{upload_err}`"
        )
        if drive_link:
            msg_text += f"\n\n☁️ *You can still access your compiled PDF on Google Drive:*\n[Open in Drive]({drive_link})"
        await status_msg.edit_text(msg_text, parse_mode="Markdown")

