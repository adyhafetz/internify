"""Tailor one resume for one job link: read the page, understand it, log it, write and check the resume, file it.

Shared by two callers that talk to the user differently:
  * the Telegram handler (you sent a link): shows live progress, may ask you questions, replies in the chat;
  * the daily job check (the Matcher picked the job): runs quietly and sends one message per resume.
Each passes a "reporter" that knows how to tell the user things; the steps themselves are the same."""
from __future__ import annotations

import asyncio
import html
import logging
import os
from dataclasses import dataclass, field
from typing import Optional

from bot import config
from bot.ask import SkillQuestioner, ask_choice, ask_for_job_text
from bot.extraction import normalize_job_posting
from bot.services.db import (SHEET_STATUSES, USER_STATUSES, find_similar, get_application, is_duplicate_url,
                             update_application as db_update_application, upsert_application)
from bot.services.drive import upload_to_drive
from bot.services.scraper import MIN_PAGE_CHARS, Page, scrape_job_url
from bot.services.sheets import append_application, find_row_by_url, update_application_status
from service import events
from service.importer import profile_is_sample
from service.pipeline import run_pipeline
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
    fit_note: str = ""
    github_note: str = ""
    error: str = ""
    app_id: Optional[int] = None
    coverage: float = 0.0
    covered: list = field(default_factory=list)
    missing: list = field(default_factory=list)
    gaps: list = field(default_factory=list)          # tools to offer "add these and rebuild" for
    summary: dict = field(default_factory=dict)
    deadline: str = ""
    skipped: bool = False                              # not written on purpose (the same job was done already)


def fallback_source(page: Page) -> str:
    """Plain-language description of where the posting came from when it was not the page the user sent."""
    return {"reader": "a page-rendering service", "archive": page.note,
            "search": f"the same posting ({page.note})"}.get(page.method, page.label)


def _record_failure(db_id: int, sheet_row: Optional[int], comment: str, score: float = 0.0, resume_status: str = "Failed") -> None:
    """Mark an application as failed in both trackers."""
    db_update_application(db_id, status="Failed", score=score, resume_status=resume_status, comment=comment)
    if sheet_row:
        update_application_status(sheet_row, resume_status=resume_status, comment=comment)


def _names(items: list, limit: int = 4) -> str:
    shown = ", ".join(str(i) for i in items[:limit])
    return shown + (f" and {len(items) - limit} more" if len(items) > limit else "")


def build_caption(outcome: JobOutcome, intro: str = "") -> str:
    caption = (
        f"🎯 <b>Your resume for {html_escape(outcome.company)} is ready!</b>\n\n" if not intro else intro
    ) + (
        f"• Role: {html_escape(outcome.role)}\n"
        f"• Job match: {score_emoji(outcome.score)} <b>{outcome.score:.0f}%</b> ({html_escape(outcome.status)})\n"
    )
    total = len(outcome.covered) + len(outcome.missing)
    if total:
        caption += f"• Shows <b>{len(outcome.covered)} of {total}</b> of the job's requirements"
        caption += (f". Not on it: {html_escape(_names(outcome.missing))}\n" if outcome.missing else "\n")
    sm = outcome.summary or {}
    if sm.get("bullets"):
        places = len(sm.get("picked", []))
        caption += (f"• 📝 {sm['bullets']} bullet points from {places} place{'s' if places != 1 else ''}"
                    + (f", {sm['reworded']} reworded for this job" if sm.get("reworded") else ""))
        out = [e["name"] for e in sm.get("left_out", [])] + sm.get("leadership_out", [])
        caption += (f". Left out: {html_escape(_names(out, 2))}\n" if out else "\n")
    if outcome.deadline:
        caption += f"• ⏰ Applications close {html_escape(outcome.deadline)}\n"
    if outcome.github_note:
        caption += f"• 🐙 {html_escape(outcome.github_note)}\n"
    if outcome.fit_note:
        caption += f"• ✂️ {html_escape(outcome.fit_note)}\n"
    if outcome.drive_link:
        caption += f'• ☁️ <a href="{html_escape(outcome.drive_link)}">Open in Google Drive</a>\n'
    else:
        caption += "• 💾 Saved on this computer only (Google Drive isn't connected)\n"
    if not outcome.sheet_row:
        caption += "• 📋 Not added to your Google Sheet (not connected)\n"
    if outcome.gaps:
        caption += f"• 🧩 The job also wants {html_escape(_names(outcome.gaps))}. Have real experience? Tap 🧩 below.\n"
    room = min(300, 980 - len(caption) - 40)   # a file caption may be at most 1024 characters
    if room > 60 and outcome.comment:
        tip = outcome.comment if len(outcome.comment) <= room else outcome.comment[:room - 1].rsplit(" ", 1)[0] + "…"
        caption += f"\n💡 <b>How it could be better:</b>\n<i>{html_escape(tip)}</i>"
    return caption


async def tailor_job(job_url: str, reporter, *, bot, chat_id: int, loop: asyncio.AbstractEventLoop,
                     interactive: bool, intro: str = "", ask_now: bool = False) -> JobOutcome:
    """Run the whole flow for one link. ``reporter`` has: ``notice(text)``, ``fail(text)``, ``finished(outcome)``
    and ``deliver(outcome, caption_html, keyboard)``. ``interactive`` lets the Scout ask you to paste a page it can't
    read and check with you before redoing a job you already have a resume for; the daily job check passes False
    because nobody is awake to answer. Missing tools are not asked about during the run (``ask_now`` turns that back
    on): the resume comes first, and a 🧩 button offers to add them and rebuild."""

    async def fail(text: str) -> JobOutcome:
        await reporter.fail(text)
        return JobOutcome(ok=False, error=text)

    # 0. A fresh install holds a made-up sample profile. A resume built from it would be fiction, so stop here.
    if profile_is_sample():
        return await fail("📄 Your saved experience is still the sample profile, so I can't write a real resume yet.\n"
                          "Send me your current resume first (PDF or Word file, or use /importresume), then send the job link again.")

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

    # 3b. The same job through another link (LinkedIn, JobStreet and the company's own page)? Don't do it twice
    #     without asking.
    if not already_tracked:
        twin = find_similar(job.company, job.job_title, exclude_link=job_url)
        if twin is not None:
            when = str(twin.get("created_at") or "")[:10]
            text = (f"ℹ️ You already have a resume for <b>{html_escape(twin['job_title'])}</b> at "
                    f"<b>{html_escape(twin['company'])}</b> from {html_escape(when)} ({html_escape(twin['status'])}), "
                    "through another link. It looks like the same job.")
            events.emit("publisher", events.PROGRESS, "Looks like a job you already have a resume for",
                        duplicate=True, twin=twin["job_link"])
            if not interactive:
                await reporter.notice(text)
                return JobOutcome(ok=False, skipped=True, company=job.company, role=job.job_title,
                                  error=f"Same job as one from {when}")
            choice = await ask_choice(bot, chat_id, loop, text + "\n\nMake another one anyway?",
                                      [("✍️ Yes, make a new one", "go"), ("✋ No, stop here", "stop")])
            if choice == "stop":
                await reporter.fail("👍 Okay, I stopped. Your earlier resume is in /applied.")
                return JobOutcome(ok=False, skipped=True, company=job.company, role=job.job_title, error="stopped")

    # 4. Log to the trackers. A link that is already tracked is re-tailored: its rows are reused, never duplicated.
    try:
        db_id = upsert_application(
            company=job.company, job_title=job.job_title, location=job.location,
            job_description=job.job_description, job_requirements=job.job_requirements,
            job_link=job_url, status="Processing",
        )
        if job.deadline:
            db_update_application(db_id, deadline=job.deadline)
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

    fields = dict(company=job.company, job_title=job.job_title, location=job.location,
                  job_description=job.job_description, job_requirements=job.job_requirements, job_link=job_url)
    return await _write_and_deliver(fields, db_id, sheet_row, reporter, bot=bot, chat_id=chat_id, loop=loop,
                                    ask_now=ask_now and interactive, intro=intro, deadline=job.deadline or "")


async def rebuild(app_id: int, reporter, *, bot, chat_id: int, loop: asyncio.AbstractEventLoop,
                  keep_entries: Optional[list[str]] = None, ask_now: bool = False) -> JobOutcome:
    """Write the resume for a job in your tracker again, from the job details already saved (no page visit):
    keeping an entry you asked for, or after asking about the tools the job wants."""
    app = get_application(app_id)
    if app is None:
        await reporter.fail("❌ I can't find that job in your tracker any more.")
        return JobOutcome(ok=False, error="not found")
    if profile_is_sample():
        await reporter.fail("📄 Your saved experience is still the sample profile, so I can't write a real resume yet.")
        return JobOutcome(ok=False, error="sample profile")
    events.emit("analyst", events.DONE, f"Using the saved details of {app['job_title']} at {app['company']}",
                company=app["company"], role=app["job_title"], rebuild=True)
    sheet_row = await asyncio.to_thread(find_row_by_url, app["job_link"]) if app.get("job_link") else None
    fields = {k: app.get(k) or "" for k in ("company", "job_title", "location", "job_description", "job_requirements", "job_link")}
    return await _write_and_deliver(fields, app_id, sheet_row, reporter, bot=bot, chat_id=chat_id, loop=loop,
                                    ask_now=ask_now, keep_entries=keep_entries or [], deadline=app.get("deadline") or "")


async def _write_and_deliver(fields: dict, db_id: int, sheet_row: Optional[int], reporter, *, bot, chat_id: int,
                             loop: asyncio.AbstractEventLoop, ask_now: bool, keep_entries: Optional[list[str]] = None,
                             intro: str = "", deadline: str = "") -> JobOutcome:
    async def fail(text: str) -> JobOutcome:
        await reporter.fail(text)
        return JobOutcome(ok=False, error=text, app_id=db_id)

    # 5. Archivist, Scribe and Critic do the tailoring, off the event loop so live updates keep flowing.
    try:
        ask = SkillQuestioner(bot, chat_id, loop) if ask_now and config.ASK_ABOUT_MISSING_SKILLS else None
        result = await asyncio.to_thread(run_pipeline, ProcessRequest(**fields, keep_entries=keep_entries or []), ask)
    except Exception as err:
        logger.error("Resume pipeline error: %s", err)
        reason = err   # PipelineError carries plain-language text
        _record_failure(db_id, sheet_row, str(reason))
        return await fail(f"❌ Something went wrong while writing your resume.\nReason: {reason}")

    pdf_path = result.pdf_path
    if not pdf_path or not os.path.exists(pdf_path):
        _record_failure(db_id, sheet_row, result.comment, score=result.score, resume_status=result.resume_status)
        return await fail(f"❌ The resume was written, but I couldn't build the PDF.\nReason: {result.comment}")

    # 6. Publisher files it
    events.emit("publisher", events.PROGRESS, "Uploading PDF to Google Drive")
    filename = f"{fields['company']}_{fields['job_title']}.pdf".replace(" ", "_").replace("/", "-")
    drive_link = await asyncio.to_thread(upload_to_drive, pdf_path, filename)
    events.emit("publisher", events.PROGRESS,
                "Uploaded to Drive" if drive_link else "Drive not configured, kept local copy",
                drive_link=drive_link or "")
    current = (get_application(db_id) or {}).get("status")
    status = current if current in USER_STATUSES else "Ready"   # rebuilding never undoes "Applied" or "Interview"
    gaps = [] if ask_now else list(result.gaps)
    db_update_application(db_id, status=status, score=result.score, resume_status=result.resume_status,
                          pdf_path=pdf_path, drive_link=drive_link or "", comment=result.comment,
                          facts_used=result.facts_used, details=result.summary, gaps=gaps)
    if sheet_row:
        await asyncio.to_thread(update_application_status, row_num=sheet_row, resume_link=drive_link or "",
                                resume_status=result.resume_status, deadline=deadline,
                                status=status if status in SHEET_STATUSES else "",   # "Ready" stays "New" in the Sheet
                                comment=f"Match {result.score:.0f}%, shows {len(result.covered)} of "
                                        f"{len(result.covered) + len(result.missing)} requirements. {result.comment}")
    events.emit("publisher", events.DONE,
                "Resume filed and tracker updated" if sheet_row else "Saved to local tracker",
                sheet_row=sheet_row or None, drive_link=drive_link or "", db_id=db_id, pdf=os.path.basename(pdf_path))

    outcome = JobOutcome(ok=True, company=fields["company"], role=fields["job_title"], score=result.score,
                         status=result.resume_status, pdf_path=pdf_path, filename=filename, drive_link=drive_link or "",
                         sheet_row=sheet_row, comment=result.comment, fit_note=result.fit_note,
                         github_note=result.github_note, app_id=db_id, coverage=result.coverage,
                         covered=result.covered, missing=result.missing, gaps=gaps, summary=result.summary,
                         deadline=deadline)
    await reporter.finished(outcome)
    # 7. Deliver the PDF, with the tracker buttons under it
    from bot.tracker import delivery_keyboard
    await reporter.deliver(outcome, build_caption(outcome, intro), delivery_keyboard(outcome))
    return outcome
