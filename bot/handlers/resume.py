from __future__ import annotations
import html
import os
import logging
from telegram import Update
from telegram.ext import ContextTypes
from bot.handlers.common import restricted
from bot.services.drive import upload_to_drive
from service.profile_data import generate_master_resume

logger = logging.getLogger(__name__)


@restricted
async def master_resume_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status_msg = await update.effective_message.reply_text("⏳ Building your general resume…")
    
    # 1. LaTeX Compilation
    try:
        res = generate_master_resume()
        pdf_path = res.get("pdf_path")
        filename = res.get("filename", "Master_Resume.pdf")

        if not pdf_path or not os.path.exists(pdf_path):
            await status_msg.edit_text("❌ Couldn't build the PDF.")
            return
    except Exception as comp_err:
        logger.error("Error compiling master resume with Tectonic: %s", comp_err)
        await status_msg.edit_text(f"❌ Couldn't build the PDF.\nReason: {comp_err}")
        return

    # 2. Upload to Google Drive (Optional Cloud)
    drive_link = None
    try:
        drive_link = upload_to_drive(pdf_path, filename)
    except Exception as drive_err:
        logger.warning("Google Drive upload failed: %s", drive_err)

    caption = f"📄 <b>Your general resume is ready!</b>\n• Name: {html.escape(str(res.get('name')), quote=False)}"
    if drive_link:
        caption += f'\n• ☁️ <a href="{html.escape(drive_link)}">Open in Google Drive</a>'

    # 3. Deliver PDF to Telegram
    try:
        with open(pdf_path, "rb") as pdf_file:
            await update.effective_message.reply_document(
                document=pdf_file,
                filename=filename,
                caption=caption,
                parse_mode="HTML",
                write_timeout=60,
                read_timeout=60,
            )
        await status_msg.delete()
    except Exception as upload_err:
        logger.error("Failed to send PDF over Telegram: %s", upload_err)
        msg_text = f"⚠️ Your resume is ready, but sending it here timed out.\nReason: {upload_err}"
        if drive_link:
            msg_text += f"\n\n☁️ You can still open it on Google Drive:\n{drive_link}"
        await status_msg.edit_text(msg_text)
