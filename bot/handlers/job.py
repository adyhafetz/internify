from __future__ import annotations
import asyncio
import html
import os
import re
from typing import Optional
import logging
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters

from bot.handlers.common import restricted
from bot.services.scraper import scrape_job_url
from bot.services.db import (
    is_duplicate_url,
    upsert_application,
    update_application as db_update_application,
    get_recent_applications,
)
from bot.services.sheets import find_row_by_url, append_application, update_application_status
from bot.services.drive import upload_to_drive
from bot.extraction import normalize_job_posting
from service.models import ProcessRequest
from service.main import run_pipeline
from service import events
from bot.progress import TelegramProgress
from bot.ask import SkillQuestioner
from bot import config

logger = logging.getLogger(__name__)

URL_REGEX = re.compile(r"https?://[^\s<>\"']+")


MIN_PAGE_CHARS = 100  # a page with less text than this is a block page or an empty shell


def score_emoji(score: float) -> str:
    """One set of colour bands everywhere: the same cut-offs as the Excellent/Good/Fair/Weak labels."""
    return "🟢" if score >= 85 else "🟡" if score >= 70 else "🟠" if score >= 55 else "🔴"


def _html(text: object) -> str:
    """Telegram HTML mode: safe for any company or job title (Markdown breaks on * and _ in names)."""
    return html.escape(str(text), quote=False)


@restricted
async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows recently tracked job applications from the local SQLite database."""
    apps = get_recent_applications(limit=8)
    if not apps:
        await update.effective_message.reply_text("📭 No applications tracked yet.\nSend any job link to tailor your first resume!")
        return

    lines = ["📊 <b>Your recent applications</b>\n"]
    for app in apps:
        score = app.get("score") or 0.0
        drive = f' · <a href="{_html(app["drive_link"])}">Google Drive</a>' if app.get("drive_link") else ""
        lines.append(
            f"• <b>{_html(app['company'])}</b> – {_html(app['job_title'])}\n"
            f"  {score_emoji(score)} Match {score:.0f}% · {_html(app['status'])} · {str(app.get('created_at', ''))[:10]}{drive}\n"
        )
    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML", disable_web_page_preview=True)


def _record_failure(db_id: int, sheet_row: Optional[int], comment: str, score: float = 0.0, resume_status: str = "Failed") -> None:
    """Mark an application as failed in both trackers."""
    db_update_application(db_id, status="Failed", score=score, resume_status=resume_status, comment=comment)
    if sheet_row:
        update_application_status(sheet_row, resume_status=resume_status, comment=comment)


@restricted
async def job_url_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    urls = URL_REGEX.findall(update.effective_message.text or "")
    if not urls:
        return
    job_url = urls[0].rstrip(".,);")
    logger.info("Detected job URL: %s", job_url)

    status_msg = await update.effective_message.reply_text("🔎 Job link detected, waking up the agents…")

    # Every run gets an id; the same events feed this chat and the web dashboard.
    run_id = events.start_run(url=job_url)
    progress = TelegramProgress(status_msg, run_id, asyncio.get_running_loop())

    async def fail(text: str):
        """Stop live updates, show the final error, and close the run."""
        events.emit_run("failed", error=text)
        await progress.close()
        await status_msg.edit_text(text)

    # 1. Already in the tracker? One Sheet lookup answers both "is it a repeat" and "which row".
    sheet_row = await asyncio.to_thread(find_row_by_url, job_url)
    already_tracked = is_duplicate_url(job_url) or sheet_row is not None
    events.emit("publisher", events.PROGRESS,
                "This URL is already in your tracker" if already_tracked else "New URL, not in tracker yet",
                duplicate=already_tracked)
    if already_tracked:
        await update.effective_message.reply_text(
            "ℹ️ This link is already in your tracker. I'll tailor a fresh resume and update the existing entry."
        )

    # 2. Scout reads the page
    events.emit("scout", events.START, "Opening the job page", url=job_url)
    try:
        page_text = await scrape_job_url(job_url)
    except Exception as err:
        logger.error("Scraping error: %s", err)
        events.emit("scout", events.FAIL, f"Scraping failed: {err}")
        await fail(f"❌ Couldn't read the job page.\nReason: {err}")
        return
    if not page_text or len(page_text) < MIN_PAGE_CHARS:
        events.emit("scout", events.FAIL, "Page returned too little text (anti-bot wall or JavaScript)")
        await fail("❌ Couldn't read the job page.\nIt may be blocking automated visitors or need JavaScript. Try a different link.")
        return
    events.emit("scout", events.DONE, f"Read {len(page_text):,} characters from the page",
                chars=len(page_text), preview=page_text[:1500], url=job_url)
    events.emit("scout", events.HANDOFF, "Raw job posting text", to="analyst")

    # 3. Analyst turns the page into a clean job posting
    events.emit("analyst", events.START, "Normalizing the raw page into a clean job posting")
    try:
        job = await asyncio.to_thread(normalize_job_posting, page_text)
    except Exception as err:
        logger.error("Normalization error: %s", err)
        events.emit("analyst", events.FAIL, f"Could not parse job description: {err}")
        await fail(f"❌ Couldn't understand the job posting.\nReason: {err}")
        return
    events.emit("analyst", events.PROGRESS, f"Identified {job.job_title} at {job.company}",
                company=job.company, role=job.job_title, location=job.location,
                description=job.job_description, requirements=job.job_requirements)

    # 4. Log to the trackers. A link that is already tracked is re-tailored: its rows are reused, never duplicated.
    try:
        db_id = upsert_application(
            company=job.company, job_title=job.job_title, location=job.location,
            job_description=job.job_description, job_requirements=job.job_requirements,
            job_link=job_url, status="Processing",
        )
        if sheet_row is None:
            sheet_row = await asyncio.to_thread(
                append_application,
                company=job.company, job_title=job.job_title, location=job.location,
                job_description=job.job_description, job_requirements=job.job_requirements,
                job_link=job_url, status="New",
            )
    except Exception as err:
        logger.error("Tracker error: %s", err)
        events.emit("publisher", events.FAIL, f"Couldn't update the tracker: {err}")
        await fail(f"❌ Couldn't update your application tracker.\nReason: {err}")
        return

    # 5. Archivist, Scribe and Critic do the tailoring. Runs off the event loop so live updates keep flowing.
    try:
        # The Archivist may stop to ask you about a tool the job wants that you haven't saved experience for.
        ask = SkillQuestioner(context.bot, update.effective_chat.id, asyncio.get_running_loop()) \
            if config.ASK_ABOUT_MISSING_SKILLS else None
        result = await asyncio.to_thread(run_pipeline, ProcessRequest(
            company=job.company, job_title=job.job_title, location=job.location,
            job_description=job.job_description, job_requirements=job.job_requirements, job_link=job_url,
        ), ask)
    except Exception as err:
        logger.error("Resume pipeline error: %s", err)
        reason = getattr(err, "detail", err)  # HTTPException carries the readable text in .detail
        _record_failure(db_id, sheet_row, str(reason))
        await fail(f"❌ Something went wrong while writing your resume.\nReason: {reason}")
        return

    pdf_path = result.pdf_path
    if not pdf_path or not os.path.exists(pdf_path):
        _record_failure(db_id, sheet_row, result.comment, score=result.score, resume_status=result.resume_status)
        await fail(f"❌ The resume was written, but I couldn't build the PDF.\nReason: {result.comment}")
        return

    # 6. Publisher files it
    events.emit("publisher", events.PROGRESS, "Uploading PDF to Google Drive")
    filename = f"{job.company}_{job.job_title}.pdf".replace(" ", "_")
    drive_link = await asyncio.to_thread(upload_to_drive, pdf_path, filename)
    events.emit("publisher", events.PROGRESS,
                "Uploaded to Drive" if drive_link else "Drive not configured, kept local copy",
                drive_link=drive_link or "")

    db_update_application(db_id, status="Ready", score=result.score, resume_status=result.resume_status,
                          pdf_path=pdf_path, drive_link=drive_link or "", comment=result.comment)
    if sheet_row:
        await asyncio.to_thread(update_application_status, row_num=sheet_row, resume_link=drive_link or "",
                                resume_status=result.resume_status,
                                comment=f"Match {result.score:.0f}%. {result.comment}")
    events.emit("publisher", events.DONE,
                "Resume filed and tracker updated" if sheet_row else "Saved to local tracker",
                sheet_row=sheet_row or None, drive_link=drive_link or "", db_id=db_id, pdf=os.path.basename(pdf_path))
    events.emit_run("finished", score=result.score, status=result.resume_status, pdf=os.path.basename(pdf_path),
                    drive_link=drive_link or "", company=job.company, role=job.job_title)

    # 7. Deliver the PDF
    caption = (
        f"🎯 <b>Your resume for {_html(job.company)} is ready!</b>\n\n"
        f"• Role: {_html(job.job_title)}\n"
        f"• Job match: {score_emoji(result.score)} <b>{result.score:.0f}%</b> ({_html(result.resume_status)})\n"
    )
    if drive_link:
        caption += f'• ☁️ <a href="{_html(drive_link)}">Open in Google Drive</a>\n'
    else:
        caption += "• 💾 Saved on this computer only (Google Drive isn't connected)\n"
    if not sheet_row:
        caption += "• 📋 Not added to your Google Sheet (not connected)\n"
    caption += f"\n💡 <b>How it could be better:</b>\n<i>{_html(result.comment[:400])}</i>"

    await progress.close()
    try:
        with open(pdf_path, "rb") as pdf_file:
            await update.effective_message.reply_document(
                document=pdf_file, filename=filename, caption=caption, parse_mode="HTML",
                write_timeout=60, read_timeout=60,
            )
        await status_msg.delete()
    except Exception as err:
        logger.error("Failed to deliver tailored PDF to Telegram: %s", err)
        text = f"🎯 Your resume is ready, but sending it here timed out.\nReason: {err}"
        if drive_link:
            text += f"\n\n☁️ You can still open it on Google Drive:\n{drive_link}"
        await status_msg.edit_text(text)


def get_job_url_handler() -> MessageHandler:
    # block=False: the run takes a while and may wait for your answer to a question, and the bot must keep
    # receiving messages (your answers, /history, ...) in the meantime.
    return MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.Regex(r"https?://"),
        job_url_handler,
        block=False,
    )
