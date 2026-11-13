"""The daily job check: Discoverer -> Matcher -> a resume for each of the best few -> a short digest in Telegram.

Runs once a day at SCAN_TIME, or when you tap "Scan now". It never asks you questions (you may be asleep): jobs that
need a tool you haven't saved experience for just say so in the message, and you can send that link yourself to be asked."""
from __future__ import annotations

import asyncio
import time
import logging
import os
from dataclasses import dataclass
from typing import Optional

from bot import config
from bot.jobflow import JobOutcome, tailor_job
from bot.services import watchlist
from bot.services.db import is_duplicate_url
from bot.services.discovery import discover
from bot.services.matcher import match
from service.importer import profile_is_sample
from service import events

logger = logging.getLogger(__name__)

_running = False   # one scan at a time


@dataclass
class ScanSummary:
    sources: int = 0
    found: int = 0
    kept: int = 0
    tailored: int = 0
    failed: int = 0
    duplicates: int = 0
    searched: bool = False
    filtered: int = 0          # left out by your filters (not an internship, another place, closed, already done)
    note: str = ""


class ScanReporter:
    """How the scan talks to you: quietly, one message per resume."""

    def __init__(self, bot, chat_id: int):
        self.bot, self.chat_id = bot, chat_id

    async def notice(self, text: str) -> None:
        pass   # the scan doesn't chatter

    async def fail(self, text: str) -> None:
        events.emit_run("job_failed", error=text)
        await self.bot.send_message(self.chat_id, "⚠️ I couldn't tailor one of today's matches.\n" + text.lstrip("❌ "))

    async def finished(self, outcome: JobOutcome) -> None:
        events.emit_run("job_finished", score=outcome.score, status=outcome.status, pdf=os.path.basename(outcome.pdf_path or ""),
                        drive_link=outcome.drive_link, company=outcome.company, role=outcome.role)

    async def deliver(self, outcome: JobOutcome, caption: str, keyboard=None) -> None:
        try:
            with open(outcome.pdf_path, "rb") as pdf_file:
                await self.bot.send_document(self.chat_id, document=pdf_file, filename=outcome.filename, caption=caption,
                                             parse_mode="HTML", write_timeout=60, read_timeout=60, reply_markup=keyboard)
        except Exception as err:
            logger.error("Could not send a scan resume: %s", err)
            await self.bot.send_message(self.chat_id, f"🎯 A resume for {outcome.company} is ready, but sending it timed out.\nReason: {err}")


async def run_scan(bot, chat_id: int, trigger: str = "schedule") -> Optional[ScanSummary]:
    """Returns None if a scan is already running."""
    global _running
    if _running:
        return None
    _running = True
    loop = asyncio.get_running_loop()
    summary = ScanSummary()
    try:
        events.start_run(url="", mode="scan", trigger=trigger)
        try:
            await _scan(bot, chat_id, loop, summary)
        except Exception as err:   # nothing in a scan may take the bot down
            logger.exception("Scan failed")
            events.emit_run("failed", error=f"The scan stopped: {err}")
            await bot.send_message(chat_id, f"⚠️ The daily job check stopped.\nReason: {err}")
            return summary
        events.emit_run("finished", scan=True, found=summary.found, shortlisted=summary.kept, tailored=summary.tailored,
                        failed=summary.failed, note=summary.note)
        watchlist.set_setting("scan_last", time.strftime("%a %d %b %H:%M") + " · " + (summary.note[:80] if summary.note else
                              f"{summary.found} new jobs, {summary.tailored} resumes written"))
        await bot.send_message(chat_id, _digest(summary), parse_mode="HTML")
        return summary
    finally:
        _running = False


def _digest(s: ScanSummary) -> str:
    if s.note:
        return f"☀️ <b>Daily job check</b>\n{s.note}"
    lines = ["☀️ <b>Daily job check done</b>",
             f"• Looked at {s.sources} job page{'s' if s.sources != 1 else ''}"
             + (" and searched the web" if s.searched else "") + f", and found {s.found} new job{'s' if s.found != 1 else ''} that fit your filters",
             f"• {s.kept} matched well enough, {s.tailored} resume{'s' if s.tailored != 1 else ''} written"]
    if s.filtered:
        lines.append(f"• {s.filtered} left out by your filters (not an internship, another place, closed or already done)")
    if s.duplicates:
        lines.append(f"• {s.duplicates} skipped: the same job as one you already have a resume for")
    if s.failed:
        lines.append(f"• {s.failed} couldn't be tailored (see the messages above)")
    return "\n".join(lines)


async def _scan(bot, chat_id: int, loop, s: ScanSummary) -> None:
    if profile_is_sample():
        s.note = "I can't write resumes yet: your saved experience is still the sample profile. Send me your resume first (/importresume)."
        return
    await asyncio.to_thread(watchlist.sources_from_history)
    sources = watchlist.list_sources()
    prefs = watchlist.get_prefs()
    s.sources = len(sources)
    if not sources and not prefs.web_search:
        events.emit("discoverer", events.FAIL, "No job pages yet", why="No job pages yet. Add one in /profile.")
        s.note = ("I have nowhere to look yet. Open /profile, tap Job pages I check, and add a career page or a job-board link, "
                  "or switch on the web search there.")
        return

    # 1. Discoverer: find links (your filters are applied here, before anything is read in full)
    events.emit("discoverer", events.START, f"Checking {len(sources)} job pages" + (" and searching the web" if prefs.web_search else ""),
                sources=len(sources))
    candidates, results = await discover(sources, prefs)
    s.filtered = sum(r.skipped for r in results)
    s.searched = any(r.label == "Web search" for r in results)
    answered = [r for r in results if r.ok]
    if not answered:
        events.emit("discoverer", events.FAIL, "No job page answered", why="None of your job pages answered")
        s.note = "None of your job pages answered today. I'll try again tomorrow."
        return
    fresh = []
    for c in candidates:
        if watchlist.seen(c.url):
            continue
        if is_duplicate_url(c.url):   # you already applied to this one
            watchlist.mark_seen(c.url, c.title, c.company, "applied")
            continue
        fresh.append(c)
    s.found = len(fresh)
    events.emit("discoverer", events.DONE, f"Found {len(fresh)} new jobs", found=len(fresh), sources=len(sources))
    if not fresh:
        s.note = (f"Checked {len(sources)} job page{'s' if len(sources) != 1 else ''}"
                  + (" and searched the web" if s.searched else "") + ": nothing new that fits your filters since last time.")
        return
    events.emit("discoverer", events.HANDOFF, f"{len(fresh)} new jobs", to="matcher")

    # 2. Matcher: pick the best fits
    events.emit("matcher", events.START, f"Scoring {len(fresh)} jobs", total=len(fresh))
    result = await match(fresh, config.SCAN_MAX_TAILORED, config.SCAN_MIN_FIT, prefs)
    s.filtered += sum(1 for x in result.scored if x.rejected)
    s.kept = len(result.kept)
    events.emit("matcher", events.DONE, f"Kept {s.kept} of {len(fresh)}", kept=s.kept, total=len(fresh), top=result.top,
                picks=[{"role": k.role, "company": k.company, "fit": round(k.fit, 2), "why": k.why} for k in result.kept])
    kept_urls = {k.cand.url for k in result.kept}
    for item in result.scored:
        if item.cand.url not in kept_urls:
            watchlist.mark_seen(item.cand.url, item.role or item.cand.title, item.company,
                                "filtered" if item.rejected else "low-fit", item.fit, item.why)
    for c in fresh:
        if not watchlist.seen(c.url) and c.url not in kept_urls:
            watchlist.mark_seen(c.url, c.title, c.company, "low-fit")
    if not result.kept:
        best = f" The best match was {round(result.top * 100)}%." if result.top is not None else ""
        s.note = f"Found {len(fresh)} new job{'s' if len(fresh) != 1 else ''}, but none matched well enough to write a resume for.{best}"
        return
    events.emit("matcher", events.HANDOFF, f"{s.kept} picked", to="scout")

    # 3. The shortlisted jobs go through the same agents as a link you send yourself, one after another
    for i, pick in enumerate(result.kept, start=1):
        watchlist.mark_seen(pick.cand.url, pick.role, pick.company, "shortlisted", pick.fit, pick.why)
        events.emit_run("job_started", url=pick.cand.url, index=i, total=s.kept, company=pick.company, role=pick.role, fit=round(pick.fit, 2))
        intro = (f"🌅 <b>Daily match: {_h(pick.role)} at {_h(pick.company)}</b>\n\n"
                 f"• Match: <b>{round(pick.fit * 100)}%</b>. {_h(pick.why)}\n"
                 f"• <a href=\"{_h(pick.cand.url)}\">Open the job</a>\n")
        outcome = await tailor_job(pick.cand.url, ScanReporter(bot, chat_id), bot=bot, chat_id=chat_id, loop=loop,
                                   interactive=False, intro=intro)
        if outcome.ok:
            s.tailored += 1
            watchlist.mark_seen(pick.cand.url, pick.role, pick.company, "tailored", pick.fit, pick.why)
        elif outcome.skipped:   # the same job as one you already have a resume for
            s.duplicates += 1
            watchlist.mark_seen(pick.cand.url, pick.role, pick.company, "duplicate", pick.fit, outcome.error[:200])
        else:
            s.failed += 1
            watchlist.mark_seen(pick.cand.url, pick.role, pick.company, "failed", pick.fit, outcome.error[:200])


def _h(text: object) -> str:
    from bot.jobflow import html_escape
    return html_escape(text)


def start_scan_now(application, chat_id: int) -> bool:
    """Start a scan in the background (the "Scan now" button). False if one is already running."""
    if _running:
        return False
    application.create_task(run_scan(application.bot, chat_id, trigger="manual"))
    return True


def is_running() -> bool:
    return _running


async def daily_scan(context) -> None:
    """The scheduled job (PTB job queue)."""
    if watchlist.scan_paused():
        logger.info("Daily scan skipped: paused")
        return
    chat_id = watchlist.owner_chat_id()
    if chat_id is None:
        logger.info("Daily scan skipped: no chat to send results to yet (send the bot any message first)")
        return
    await run_scan(context.bot, chat_id, trigger="schedule")


def schedule(application) -> Optional[str]:
    """Register the daily scan. Returns a human description of when it runs, or None if scanning is off."""
    if not config.SCAN_ENABLED or getattr(application, "job_queue", None) is None:
        return None
    from datetime import time
    from zoneinfo import ZoneInfo
    try:
        hh, mm = (int(x) for x in config.SCAN_TIME.split(":"))
        when = time(hh, mm, tzinfo=ZoneInfo(config.SCAN_TIMEZONE))
    except Exception as err:
        logger.warning("SCAN_TIME/SCAN_TIMEZONE not understood (%s); the daily scan is off", err)
        return None
    application.job_queue.run_daily(daily_scan, time=when, name="daily-scan")
    return f"every day at {config.SCAN_TIME} ({config.SCAN_TIMEZONE})"
