from __future__ import annotations
import os
import re
import logging
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters

from bot.handlers.common import restricted
from bot.services.scraper import scrape_job_url
from bot.services.db import (
    is_duplicate_url,
    add_application as db_add_application,
    update_application as db_update_application,
    get_recent_applications,
)
from bot.services.sheets import is_duplicate_job, append_application, update_application_status
from bot.services.drive import upload_to_drive
from bot.extraction import normalize_job_posting
from service.models import ProcessRequest
from service.main import process

logger = logging.getLogger(__name__)

URL_REGEX = re.compile(r"https?://[^\s<>\"']+")


@restricted
async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays recently tracked job applications from the local SQLite database."""
    apps = get_recent_applications(limit=8)
    if not apps:
        await update.effective_message.reply_text(
            "📭 *No applications tracked yet.*\nSend any job link to tailor your first resume!",
            parse_mode="Markdown",
        )
        return

    lines = ["📊 *Recent Applications Tracker:*\n"]
    for app in apps:
        score_val = app.get("score") or 0.0
        score_emoji = "🟢" if score_val >= 80 else ("🟡" if score_val >= 60 else "⚪")
        date_str = str(app.get("created_at", ""))[:10]
        drive = f" [☁️ Drive]({app['drive_link']})" if app.get("drive_link") else ""

        lines.append(
            f"• *{app['company']}* – {app['job_title']}\n"
            f"  {score_emoji} Score: `{score_val:.1f}` | Status: `{app['status']}` ({date_str}){drive}\n"
        )

    await update.effective_message.reply_text("\n".join(lines), parse_mode="Markdown")


@restricted
async def job_url_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message_text = update.effective_message.text or ""
    urls = URL_REGEX.findall(message_text)
    if not urls:
        return

    job_url = urls[0].rstrip(".,);")
    logger.info("Detected job URL: %s", job_url)

    status_msg = await update.effective_message.reply_text(
        "🔎 *Detected Job Link!*\nChecking application tracker...",
        parse_mode="Markdown",
    )

    # 1. Duplicate check via SQLite and Google Sheets
    is_dup_sqlite = is_duplicate_url(job_url)
    is_dup_sheet = is_duplicate_job(job_url)

    if is_dup_sqlite or is_dup_sheet:
        await update.effective_message.reply_text(
            "⚠️ *Notice:* You already have an application logged for this URL in your Tracker.",
            parse_mode="Markdown",
        )

    # 2. Scrape job posting
    try:
        await status_msg.edit_text("⏳ `[1/4]` 🌐 *Scraping job posting content...*", parse_mode="Markdown")
        scraped_text = await scrape_job_url(job_url)
        if not scraped_text or len(scraped_text) < 100:
            await status_msg.edit_text(
                "❌ *Failed to extract job posting text.*\n"
                "The page could be protected by heavy anti-bot walls or JavaScript.",
                parse_mode="Markdown",
            )
            return
    except Exception as scrape_err:
        logger.error("Scraping error: %s", scrape_err)
        await status_msg.edit_text(f"❌ *Scraping failed:* {scrape_err}")
        return

    # 3. Extract structured job details
    try:
        await status_msg.edit_text("⏳ `[2/4]` 🧠 *Normalizing job details with AI...*", parse_mode="Markdown")
        job = normalize_job_posting(scraped_text)
    except Exception as norm_err:
        logger.error("Normalization error: %s", norm_err)
        await status_msg.edit_text(f"❌ *Failed to parse job description:* {norm_err}")
        return

    # 4. Log initial row to SQLite (Always) & Google Sheets (Optional Cloud)
    db_app_id = db_add_application(
        company=job.company,
        job_title=job.job_title,
        location=job.location,
        job_description=job.job_description,
        job_requirements=job.job_requirements,
        job_link=job_url,
        status="Processing",
    )

    sheet_row_num = append_application(
        company=job.company,
        job_title=job.job_title,
        location=job.location,
        job_description=job.job_description,
        job_requirements=job.job_requirements,
        job_link=job_url,
        status="New",
    )

    # 5. Run agentic loop (Retrieval + Generator + Critic)
    try:
        await status_msg.edit_text(
            f"⏳ `[3/4]` ✍️ *Tailoring resume for* **{job.company}** (`{job.job_title}`)...\n"
            "_Evaluating keyword match, selecting relevant coursework & running critic loop..._",
            parse_mode="Markdown",
        )
        proc_req = ProcessRequest(
            company=job.company,
            job_title=job.job_title,
            location=job.location,
            job_description=job.job_description,
            job_requirements=job.job_requirements,
            job_link=job_url,
        )
        proc_res = process(proc_req)
    except Exception as proc_err:
        logger.error("Resume pipeline error: %s", proc_err)
        db_update_application(db_app_id, status="Failed", comment=str(proc_err))
        if sheet_row_num:
            update_application_status(sheet_row_num, resume_status="Failed", comment=str(proc_err))
        await status_msg.edit_text(f"❌ *Resume pipeline failed:* {proc_err}")
        return

    # 6. Check compiled PDF
    pdf_path = proc_res.pdf_path
    if not pdf_path or not os.path.exists(pdf_path):
        db_update_application(
            db_app_id,
            status="Failed",
            score=proc_res.score,
            resume_status=proc_res.resume_status,
            comment=proc_res.comment,
        )
        if sheet_row_num:
            update_application_status(sheet_row_num, resume_status=proc_res.resume_status, comment=proc_res.comment)
        await status_msg.edit_text(
            f"❌ *LaTeX Compilation Failed*\n\n"
            f"• *Status:* {proc_res.resume_status}\n"
            f"• *Notes:* {proc_res.comment}",
            parse_mode="Markdown",
        )
        return

    # 7. Upload to Google Drive (Optional Cloud)
    await status_msg.edit_text("⏳ `[4/4]` 📄 *Packaging PDF and updating application tracker...*", parse_mode="Markdown")
    dest_filename = f"{job.company}_{job.job_title}.pdf".replace(" ", "_")
    drive_link = upload_to_drive(pdf_path, dest_filename)

    # 8. Update SQLite & Google Sheets
    db_update_application(
        db_app_id,
        status="Ready",
        score=proc_res.score,
        resume_status=proc_res.resume_status,
        pdf_path=pdf_path,
        drive_link=drive_link or "",
        comment=proc_res.comment,
    )

    if sheet_row_num:
        update_application_status(
            row_num=sheet_row_num,
            resume_link=drive_link or "",
            resume_status=proc_res.resume_status,
            comment=f"Score: {proc_res.score:.1f}. {proc_res.comment}",
        )

    # 9. Deliver PDF directly to Telegram chat
    score_emoji = "🟢" if proc_res.score >= 80 else ("🟡" if proc_res.score >= 65 else "🔴")
    caption = (
        f"🎯 *Tailored Resume Ready!*\n\n"
        f"• *Company:* {job.company}\n"
        f"• *Role:* {job.job_title}\n"
        f"• *ATS Match Score:* {score_emoji} *{proc_res.score:.1f}/100* ({proc_res.resume_status})\n"
    )
    if drive_link:
        caption += f"• ☁️ [Open in Google Drive]({drive_link})\n"
    caption += f"\n💡 *Critic Feedback:*\n_{proc_res.comment[:400]}_"

    with open(pdf_path, "rb") as pdf_file:
        await update.effective_message.reply_document(
            document=pdf_file,
            filename=dest_filename,
            caption=caption,
            parse_mode="Markdown",
        )

    await status_msg.delete()


def get_job_url_handler() -> MessageHandler:
    return MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.Regex(r"https?://"),
        job_url_handler,
    )
