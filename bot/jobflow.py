"""Tailor one resume for one job link: read the page, understand it, log it, write and check the resume, file it.

Shared by two callers that talk to the user differently:
  * the Telegram handler (you sent a link): shows live progress, may ask you questions, replies in the chat;
  * the morning scan (the Matcher picked the job): runs quietly and sends one message per resume.
Each passes a "reporter" that knows how to tell the user things; the steps themselves are the same."""
from __future__ import annotations

import asyncio
import html
import logging
import os
from dataclasses import dataclass
from typing import Optional

from bot import config
from bot.ask import SkillQuestioner, ask_for_job_text
from bot.extraction import normalize_job_posting
from bot.services.db import is_duplicate_url, update_application as db_update_application, upsert_application
from bot.services.drive import upload_to_drive
from bot.services.scraper import MIN_PAGE_CHARS, Page, scrape_job_url
from bot.services.sheets import append_application, find_row_by_url, update_application_status
from service import events
from service.main import run_pipeline
from service.models import ProcessRequest

logger = logging.getLogger(__name__)


def score_emoji(score: float) -> str:
    """One set of colour bands everywhere: the same cut-offs as the Excellent/Good/Fair/Weak labels."""
    return "🟢" if score >= 85 else "🟡" if score >= 70 else "🟠" if score >= 55 else "🔴"


def html_escape(text: object) -> str:
    """Telegram HTML mode: safe for any company or job title (Markdown breaks on * and _ in names)."""
    return html.escape(str(text), quote=False)


@dataclass
class JobOutcome:
    ok: bool
    company: str = ""
    role: str = ""
    score: float = 0.0
    status: str = ""
    pdf_path: Optional[str] = None
    filename: str = ""
    drive_link: str = ""
    sheet_row: Optional[int] = None
    comment: str = ""
    error: str = ""


def fallback_source(page: Page) -> str:
    """Plain-language description of where the posting came from when it was not the page the user sent."""
    return {"reader": "a page-rendering service", "archive": page.note,
            "search": f"the same posting ({page.note})"}.get(page.method, page.label)


def _record_failure(db_id: int, sheet_row: Optional[int], comment: str, score: float = 0.0, resume_status: str = "Failed") -> None:
    """Mark an application as failed in both trackers."""
    db_update_application(db_id, status="Failed", score=score, resume_status=resume_status, comment=comment)
    if sheet_row:
        update_application_status(sheet_row, resume_status=resume_status, comment=comment)


def build_caption(outcome: JobOutcome, intro: str = "") -> str:
    caption = (
        f"🎯 <b>Your resume for {html_escape(outcome.company)} is ready!</b>\n\n" if not intro else intro
    ) + (
        f"• Role: {html_escape(outcome.role)}\n"
        f"• Job match: {score_emoji(outcome.score)} <b>{outcome.score:.0f}%</b> ({html_escape(outcome.status)})\n"
    )
    if outcome.drive_link:
        caption += f'• ☁️ <a href="{html_escape(outcome.drive_link)}">Open in Google Drive</a>\n'
    else:
        caption += "• 💾 Saved on this computer only (Google Drive isn't connected)\n"
    if not outcome.sheet_row:
        caption += "• 📋 Not added to your Google Sheet (not connected)\n"
    return caption + f"\n💡 <b>How it could be better:</b>\n<i>{html_escape(outcome.comment[:400])}</i>"


async def tailor_job(job_url: str, reporter, *, bot, chat_id: int, loop: asyncio.AbstractEventLoop,
                     interactive: bool, intro: str = "") -> JobOutcome:
    """Run the whole flow for one link. ``reporter`` has: ``notice(text)``, ``fail(text)``, ``finished(outcome)``
    and ``deliver(outcome, caption_html)``. ``interactive`` lets the Scout and the Archivist ask the user questions;
    the morning scan passes False because nobody is awake to answer."""

    async def fail(text: str) -> JobOutcome:
        await reporter.fail(text)
        return JobOutcome(ok=False, error=text)

    # 1. Already in the tracker? One Sheet lookup answers both "is it a repeat" and "which row".
    sheet_row = await asyncio.to_thread(find_row_by_url, job_url)
    already_tracked = is_duplicate_url(job_url) or sheet_row is not None
    events.emit("publisher", events.PROGRESS,
                "This URL is already in your tracker" if already_tracked else "New URL, not in tracker yet",
                duplicate=already_tracked)
    if already_tracked:
        await reporter.notice("ℹ️ This link is already in your tracker. I'll tailor a fresh resume and update the existing entry.")

    # 2. Scout reads the page. If it is blocked or empty, Scout tries other routes (each one is shown), and as a
    #    last resort (when someone can answer) asks you to paste the job description.
    events.emit("scout", events.START, "Opening the job page", url=job_url)
    try:
        page = await scrape_job_url(job_url)
        if page is None and interactive and config.SCOUT_FALLBACKS:
            pasted = await ask_for_job_text(bot, chat_id, loop, MIN_PAGE_CHARS)
            if pasted:
                page = Page(text=pasted, method="pasted", label="the text you pasted", source_url=job_url, via_fallback=True)
    except Exception as err:
        logger.error("Scraping error: %s", err)
        events.emit("scout", events.FAIL, f"Scraping failed: {err}")
        return await fail(f"❌ Couldn't read the job page.\nReason: {err}")
    if page is None:
        events.emit("scout", events.FAIL, "No route could read the page")
        return await fail("❌ Couldn't read the job page, even after trying other ways in.\n"
                          "It may be blocking automated visitors. Try a different link, or send the link again and paste the job text when I ask.")
    events.emit("scout", events.DONE, f"Read {len(page.text):,} characters from the page",
                chars=len(page.text), preview=page.text[:1500], url=job_url, method=page.method, label=page.label,
                via_fallback=page.via_fallback, note=page.note, source_url=page.source_url)
    events.emit("scout", events.HANDOFF, "Raw job posting text", to="analyst")
    if page.via_fallback and page.method != "pasted":
        await reporter.notice(f"ℹ️ I couldn't open that page directly, so I used {fallback_source(page)}. "
                              "Please check that the company and role look right.")

    # 3. Analyst turns the page into a clean job posting
    events.emit("analyst", events.START, "Normalizing the raw page into a clean job posting")
    try:
        job = await asyncio.to_thread(normalize_job_posting, page.text)
    except Exception as err:
        logger.error("Normalization error: %s", err)
        events.emit("analyst", events.FAIL, f"Could not parse job description: {err}")
        return await fail(f"❌ Couldn't understand the job posting.\nReason: {err}")
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
        return await fail(f"❌ Couldn't update your application tracker.\nReason: {err}")

    # 5. Archivist, Scribe and Critic do the tailoring, off the event loop so live updates keep flowing.
    try:
        ask = SkillQuestioner(bot, chat_id, loop) if interactive and config.ASK_ABOUT_MISSING_SKILLS else None
        result = await asyncio.to_thread(run_pipeline, ProcessRequest(
            company=job.company, job_title=job.job_title, location=job.location,
            job_description=job.job_description, job_requirements=job.job_requirements, job_link=job_url,
        ), ask)
    except Exception as err:
        logger.error("Resume pipeline error: %s", err)
        reason = getattr(err, "detail", err)  # HTTPException carries the readable text in .detail
        _record_failure(db_id, sheet_row, str(reason))
        return await fail(f"❌ Something went wrong while writing your resume.\nReason: {reason}")

    pdf_path = result.pdf_path
    if not pdf_path or not os.path.exists(pdf_path):
        _record_failure(db_id, sheet_row, result.comment, score=result.score, resume_status=result.resume_status)
        return await fail(f"❌ The resume was written, but I couldn't build the PDF.\nReason: {result.comment}")

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
                                resume_status=result.resume_status, comment=f"Match {result.score:.0f}%. {result.comment}")
    events.emit("publisher", events.DONE,
                "Resume filed and tracker updated" if sheet_row else "Saved to local tracker",
                sheet_row=sheet_row or None, drive_link=drive_link or "", db_id=db_id, pdf=os.path.basename(pdf_path))

    outcome = JobOutcome(ok=True, company=job.company, role=job.job_title, score=result.score, status=result.resume_status,
                         pdf_path=pdf_path, filename=filename, drive_link=drive_link or "", sheet_row=sheet_row,
                         comment=result.comment)
    await reporter.finished(outcome)
    # 7. Deliver the PDF
    await reporter.deliver(outcome, build_caption(outcome, intro))
    return outcome
