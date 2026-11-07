from __future__ import annotations
import asyncio
import os
import re
import logging
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters

from bot.handlers.common import restricted
from bot.services.scraper import scrape_job_url
from bot.services.db import (
    is_duplicate_url,
    upsert_application as db_add_application,
    update_application as db_update_application,
    get_recent_applications,
)
from bot.services.sheets import is_duplicate_job, find_row_by_url, append_application, update_application_status
from bot.services.drive import upload_to_drive
from bot.extraction import normalize_job_posting
from service.models import ProcessRequest
from service.main import process
from service import events
from bot.progress import TelegramProgress

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

    status_msg = await update.effective_message.reply_text("🔎 Job link detected, waking up the agents...")

    # Every run gets an id; the same events feed this chat and the web dashboard.
    run_id = events.start_run(url=job_url)
    progress = TelegramProgress(status_msg, run_id, asyncio.get_running_loop())

    async def fail(text: str, **run_data):
        """Stop live updates, show the final error, and close the run."""
        events.emit_run("failed", error=text, **run_data)
        await progress.close()
        await status_msg.edit_text(text)

    # 1. Duplicate check via SQLite and Google Sheets
    is_dup_sqlite = is_duplicate_url(job_url)
    is_dup_sheet = is_duplicate_job(job_url)

    if is_dup_sqlite or is_dup_sheet:
        events.emit("publisher", events.PROGRESS, "This URL is already in your tracker", duplicate=True)
        await update.effective_message.reply_text(
            "ℹ️ This link is already in your tracker. I'll tailor a fresh resume and update the existing entry."
        )
    else:
        events.emit("publisher", events.PROGRESS, "New URL, not in tracker yet", duplicate=False)

    # 2. Scrape job posting
    events.emit("scout", events.START, "Opening the job page", url=job_url)
    try:
        scraped_text = await scrape_job_url(job_url)
        if not scraped_text or len(scraped_text) < 100:
            events.emit("scout", events.FAIL, "Page returned too little text (anti-bot wall or JavaScript)")
            await fail(
                "❌ Failed to extract job posting text.\n"
                "The page could be protected by heavy anti-bot walls or JavaScript."
            )
            return
    except Exception as scrape_err:
        logger.error("Scraping error: %s", scrape_err)
        events.emit("scout", events.FAIL, f"Scraping failed: {scrape_err}")
        await fail(f"❌ Scraping failed: {scrape_err}")
        return
    events.emit(
        "scout", events.DONE, f"Read {len(scraped_text):,} characters from the page",
        chars=len(scraped_text), preview=scraped_text[:1500], url=job_url,
    )
    events.emit("scout", events.HANDOFF, "Raw job posting text", to="analyst")

    # 3. Extract structured job details
    events.emit("analyst", events.START, "Normalizing the raw page into a clean job posting")
    try:
        job = await asyncio.to_thread(normalize_job_posting, scraped_text)
    except Exception as norm_err:
        logger.error("Normalization error: %s", norm_err)
        events.emit("analyst", events.FAIL, f"Could not parse job description: {norm_err}")
        await fail(f"❌ Failed to parse job description: {norm_err}")
        return
    events.emit(
        "analyst", events.PROGRESS, f"Identified {job.job_title} at {job.company}",
        company=job.company, role=job.job_title, location=job.location,
        description=job.job_description, requirements=job.job_requirements,
    )

    # 4. Log initial row to SQLite (Always) & Google Sheets (Optional Cloud).
    #    A link that is already tracked is re-tailored: its existing rows are reused, never duplicated.
    try:
        db_app_id = db_add_application(
            company=job.company,
            job_title=job.job_title,
            location=job.location,
            job_description=job.job_description,
            job_requirements=job.job_requirements,
            job_link=job_url,
            status="Processing",
        )
        sheet_row_num = await asyncio.to_thread(find_row_by_url, job_url) if is_dup_sheet else None
        if sheet_row_num is None:
            sheet_row_num = await asyncio.to_thread(
                append_application,
                company=job.company,
                job_title=job.job_title,
                location=job.location,
                job_description=job.job_description,
                job_requirements=job.job_requirements,
                job_link=job_url,
                status="New",
            )
    except Exception as track_err:
        logger.error("Tracker error: %s", track_err)
        events.emit("publisher", events.FAIL, f"Couldn't update the tracker: {track_err}")
        await fail(f"❌ Couldn't update the application tracker: {track_err}")
        return

    # 5. Run agentic loop (Retrieval + Generator + Critic) off the event loop so live updates keep flowing
    try:
        proc_req = ProcessRequest(
            company=job.company,
            job_title=job.job_title,
            location=job.location,
            job_description=job.job_description,
            job_requirements=job.job_requirements,
            job_link=job_url,
        )
        proc_res = await asyncio.to_thread(process, proc_req)
    except Exception as proc_err:
        logger.error("Resume pipeline error: %s", proc_err)
        db_update_application(db_app_id, status="Failed", comment=str(proc_err))
        if sheet_row_num:
            update_application_status(sheet_row_num, resume_status="Failed", comment=str(proc_err))
        await fail(f"❌ Resume pipeline failed: {proc_err}")
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
        await fail(
            f"❌ LaTeX Compilation Failed\n\n"
            f"• Status: {proc_res.resume_status}\n"
            f"• Notes: {proc_res.comment}"
        )
        return

    # 7. Upload to Google Drive (Optional Cloud)
    events.emit("publisher", events.PROGRESS, "Uploading PDF to Google Drive")
    dest_filename = f"{job.company}_{job.job_title}.pdf".replace(" ", "_")
    drive_link = await asyncio.to_thread(upload_to_drive, pdf_path, dest_filename)
    events.emit(
        "publisher", events.PROGRESS,
        "Uploaded to Drive" if drive_link else "Drive not configured, kept local copy",
        drive_link=drive_link or "",
    )

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
        await asyncio.to_thread(
            update_application_status,
            row_num=sheet_row_num,
            resume_link=drive_link or "",
            resume_status=proc_res.resume_status,
            comment=f"Score: {proc_res.score:.1f}. {proc_res.comment}",
        )
    events.emit(
        "publisher", events.DONE,
        "Resume filed and tracker updated" if sheet_row_num else "Saved to local tracker",
        sheet_row=sheet_row_num or None, drive_link=drive_link or "", db_id=db_app_id,
        pdf=os.path.basename(pdf_path),
    )
    events.emit_run(
        "finished", score=proc_res.score, status=proc_res.resume_status,
        pdf=os.path.basename(pdf_path), drive_link=drive_link or "",
        company=job.company, role=job.job_title,
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

    await progress.close()
    try:
        with open(pdf_path, "rb") as pdf_file:
            await update.effective_message.reply_document(
                document=pdf_file,
                filename=dest_filename,
                caption=caption,
                parse_mode="Markdown",
                write_timeout=60,
                read_timeout=60,
            )
        # Keep the finished agent timeline in the chat instead of deleting it.
        try:
            await status_msg.edit_text(progress.render())
        except Exception:
            pass
    except Exception as upload_err:
        logger.error("Failed to deliver tailored PDF to Telegram: %s", upload_err)
        msg_text = (
            f"🎯 Tailored Resume Ready, but delivery directly to Telegram encountered a network timeout:\n{upload_err}"
        )
        if drive_link:
            msg_text += f"\n\n☁️ You can access your compiled PDF on Google Drive:\n{drive_link}"
        await status_msg.edit_text(msg_text)


def get_job_url_handler() -> MessageHandler:
    return MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.Regex(r"https?://"),
        job_url_handler,
    )
