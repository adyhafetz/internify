from __future__ import annotations
import asyncio
import logging
import os
import re
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters

from bot.handlers.common import restricted
from bot.jobflow import html_escape as _html, score_emoji, tailor_job
from bot.services.db import get_recent_applications
from bot.progress import TelegramProgress
from service import events

logger = logging.getLogger(__name__)

URL_REGEX = re.compile(r"https?://[^\s<>\"']+")


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


class TelegramReporter:
    """Tells the user, in the chat they sent the link from, what is happening."""

    def __init__(self, message, status_msg, progress: TelegramProgress):
        self.message, self.status_msg, self.progress = message, status_msg, progress

    async def notice(self, text: str) -> None:
        await self.message.reply_text(text)

    async def fail(self, text: str) -> None:
        """Stop live updates, show the final error, and close the run."""
        events.emit_run("failed", error=text)
        await self.progress.close()
        await self.status_msg.edit_text(text)

    async def finished(self, outcome) -> None:
        events.emit_run("finished", score=outcome.score, status=outcome.status, pdf=os.path.basename(outcome.pdf_path),
                        drive_link=outcome.drive_link, company=outcome.company, role=outcome.role)

    async def deliver(self, outcome, caption: str) -> None:
        await self.progress.close()
        try:
            with open(outcome.pdf_path, "rb") as pdf_file:
                await self.message.reply_document(document=pdf_file, filename=outcome.filename, caption=caption,
                                                  parse_mode="HTML", write_timeout=60, read_timeout=60)
            await self.status_msg.delete()
        except Exception as err:
            logger.error("Failed to deliver tailored PDF to Telegram: %s", err)
            text = f"🎯 Your resume is ready, but sending it here timed out.\nReason: {err}"
            if outcome.drive_link:
                text += f"\n\n☁️ You can still open it on Google Drive:\n{outcome.drive_link}"
            await self.status_msg.edit_text(text)


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
    loop = asyncio.get_running_loop()
    reporter = TelegramReporter(update.effective_message, status_msg, TelegramProgress(status_msg, run_id, loop))
    await tailor_job(job_url, reporter, bot=context.bot, chat_id=update.effective_chat.id, loop=loop, interactive=True)


def get_job_url_handler() -> MessageHandler:
    # block=False: the run takes a while and may wait for your answer to a question, and the bot must keep
    # receiving messages (your answers, /history, ...) in the meantime.
    return MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.Regex(r"https?://"),
        job_url_handler,
        block=False,
    )
