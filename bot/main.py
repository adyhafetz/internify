from __future__ import annotations
import logging
import sys
from telegram import Update, BotCommand
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from bot import config
from bot.handlers.common import start_command, help_command, cancel_command
from bot.handlers.profile_hub import profile_command, xp_callback, xp_text_handler
from bot.services.db import init_db
from bot.handlers.resume import master_resume_command
from bot.handlers.job import get_job_url_handler, history_command
from telegram.request import HTTPXRequest
from bot.telegram_safe import SafeBot
from bot.ask import ask_callback, ask_text_handler
from bot.handlers.importresume import importresume_command, resume_document_handler, import_callback

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception while handling an update:", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                f"⚠️ *An unexpected error occurred:*\n`{context.error}`",
                parse_mode="Markdown",
            )
        except Exception:
            pass


async def post_init(application) -> None:
    # The slash menu. Everything on your resume is behind /profile (including adding things).
    commands = [
        BotCommand("profile", "See and edit everything on your resume"),
        BotCommand("importresume", "Fill it in from your current resume (PDF or Word)"),
        BotCommand("history", "Jobs you've applied to"),
        BotCommand("resume", "Download your general resume (PDF)"),
        BotCommand("help", "Everything I can do"),
        BotCommand("cancel", "Stop the current step"),
    ]
    try:
        await application.bot.set_my_commands(commands)
        # Shown on the empty chat screen, before the user has typed anything: tells them the main action.
        await application.bot.set_my_short_description("Send me a job link and I'll write a 1-page resume from your real experience.")
        await application.bot.set_my_description(
            "Send me a job link and I'll write a 1-page resume tailored to that job, using only your real experience, "
            "and send you the PDF.\n\nFirst, tap Start and send me your current resume file so I know your experience.")
        logger.info("Successfully registered the bot's command menu and description with Telegram.")
    except Exception as e:
        logger.warning("Could not set the bot's menu or description automatically: %s", e)
    from bot import scanner
    when = scanner.schedule(application)
    logger.info("Daily job check: %s", when or "off")


def create_bot_app():
    if not config.TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is not set in environment or .env file!")
        sys.exit(1)

    # SafeBot converts the Markdown-style messages to safe HTML, so names or bullet points containing _ * [ can
    # never make Telegram reject a message ("Can't parse entities").
    bot = SafeBot(
        token=config.TELEGRAM_BOT_TOKEN,
        request=HTTPXRequest(connect_timeout=30.0, read_timeout=30.0, write_timeout=60.0, pool_timeout=30.0),
    )
    app = ApplicationBuilder().bot(bot).post_init(post_init).build()

    # Standalone commands
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("history", history_command))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CommandHandler("resume", master_resume_command))

    # Everything on your resume, by tapping: one command, no shortcuts.
    app.add_handler(CommandHandler("profile", profile_command))
    app.add_handler(CallbackQueryHandler(xp_callback, pattern=r"^xp:"))

    # Import an existing resume: /importresume, then send the file; buttons confirm before anything is saved
    app.add_handler(CommandHandler("importresume", importresume_command))
    app.add_handler(MessageHandler(filters.Document.ALL, resume_document_handler, block=False))
    app.add_handler(CallbackQueryHandler(import_callback, pattern=r"^imp:"))

    # Answers to the Archivist's questions (buttons, and typed replies, which must be seen before other text handlers)
    app.add_handler(CallbackQueryHandler(ask_callback, pattern=r"^ask:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ask_text_handler), group=-2)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, xp_text_handler), group=-1)

    # Job URL Scrape & Tailor Ingestion Handler
    app.add_handler(get_job_url_handler())

    # Global Error Handler
    app.add_error_handler(error_handler)

    return app


def main():
    if not config.ALLOWED_USER_IDS:
        logger.warning("TELEGRAM_ALLOWED_USER_IDS is empty: anyone who finds your bot can use it and spend your OpenAI credit. "
                       "Add your Telegram user ID to .env.")
    logger.info("Initializing database...")
    init_db()
    if config.DASHBOARD_ENABLED:
        from bot.dashboard.server import start_dashboard
        start_dashboard(config.DASHBOARD_HOST, config.DASHBOARD_PORT)
    # Say plainly whether Google Drive/Sheets are connected (in the background; never blocks or stops the bot).
    import threading
    from bot.services.google_check import check_google_setup
    threading.Thread(target=check_google_setup, name="google-check", daemon=True).start()
    logger.info("Starting Internify Telegram Bot runner...")
    app = create_bot_app()
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

