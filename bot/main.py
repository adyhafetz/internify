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
from bot.handlers.profile import (
    myprofile_command,
    get_profile_conversation_handler,
    profile_button_callback,
)
from bot.handlers.education import (
    listeducation_command,
    get_education_conversation_handler,
    delete_education_command,
    delete_education_callback,
    add_course_command,
    delete_course_command,
)
from bot.handlers.experience import (
    get_experience_conversation_handler,
    delete_experience_command,
    delete_experience_callback,
)
from bot.handlers.project import (
    get_project_conversation_handler,
    delete_project_command,
    delete_project_callback,
)
from bot.handlers.leadership import (
    get_leadership_conversation_handler,
    delete_leadership_command,
    delete_leadership_callback,
)
from bot.handlers.skills import (
    listskills_command,
    get_skills_conversation_handler,
    delete_skill_command,
)
from bot.handlers.facts import (
    listentries_command,
    listfacts_command,
    get_facts_conversation_handler,
    delete_fact_command,
)
from bot.services.db import init_db
from bot.handlers.resume import master_resume_command
from bot.handlers.job import get_job_url_handler, history_command
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
    commands = [
        BotCommand("start", "What I can do"),
        BotCommand("importresume", "Fill in your experience from your resume file"),
        BotCommand("history", "Jobs you've applied to"),
        BotCommand("resume", "Your general resume (PDF)"),
        BotCommand("myprofile", "Your contact details"),
        BotCommand("setname", "Update your full name"),
        BotCommand("setphone", "Update your phone number"),
        BotCommand("setemail", "Update your email address"),
        BotCommand("setlinkedin", "Update your LinkedIn URL"),
        BotCommand("setgithub", "Update your GitHub URL"),
        BotCommand("listeducation", "See your degrees"),
        BotCommand("addeducation", "Add a degree"),
        BotCommand("deleteeducation", "Remove a degree"),
        BotCommand("addcourse", "Add a course to a degree"),
        BotCommand("deletecourse", "Remove a course"),
        BotCommand("addexperience", "Add a job or internship"),
        BotCommand("deleteexperience", "Remove a job or internship"),
        BotCommand("addproject", "Add project"),
        BotCommand("deleteproject", "Delete project"),
        BotCommand("addleadership", "Add a leadership role or activity"),
        BotCommand("deleteleadership", "Remove a leadership role"),
        BotCommand("listskills", "See your skills"),
        BotCommand("addskill", "Add skills"),
        BotCommand("deleteskill", "Delete a skill"),
        BotCommand("listentries", "Your saved jobs, projects and IDs"),
        BotCommand("listfacts", "See the bullet points under an entry"),
        BotCommand("updatefact", "Edit a bullet point"),
        BotCommand("deletefact", "Remove a bullet point"),
        BotCommand("cancel", "Stop the current question"),
    ]
    try:
        await application.bot.set_my_commands(commands)
        logger.info("Successfully registered bot command menu with Telegram.")
    except Exception as e:
        logger.warning("Could not set bot commands automatically: %s", e)


def create_bot_app():
    if not config.TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is not set in environment or .env file!")
        sys.exit(1)

    app = (
        ApplicationBuilder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .connect_timeout(30.0)
        .read_timeout(30.0)
        .write_timeout(60.0)
        .pool_timeout(30.0)
        .post_init(post_init)
        .build()
    )

    # Conversation Handlers (FSM: /setname, /addexperience, etc.)
    app.add_handler(get_profile_conversation_handler())
    app.add_handler(get_education_conversation_handler())
    app.add_handler(get_experience_conversation_handler())
    app.add_handler(get_project_conversation_handler())
    app.add_handler(get_leadership_conversation_handler())
    app.add_handler(get_skills_conversation_handler())
    app.add_handler(get_facts_conversation_handler())

    # Standalone Commands
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("history", history_command))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CommandHandler("myprofile", myprofile_command))
    app.add_handler(CommandHandler("listeducation", listeducation_command))
    app.add_handler(CommandHandler("deleteeducation", delete_education_command))
    app.add_handler(CommandHandler("addcourse", add_course_command))
    app.add_handler(CommandHandler("deletecourse", delete_course_command))
    app.add_handler(CommandHandler("deleteexperience", delete_experience_command))
    app.add_handler(CommandHandler("deleteproject", delete_project_command))
    app.add_handler(CommandHandler("deleteleadership", delete_leadership_command))
    app.add_handler(CommandHandler("listskills", listskills_command))
    app.add_handler(CommandHandler("deleteskill", delete_skill_command))
    app.add_handler(CommandHandler("listentries", listentries_command))
    app.add_handler(CommandHandler("listfacts", listfacts_command))
    app.add_handler(CommandHandler("deletefact", delete_fact_command))
    app.add_handler(CommandHandler("resume", master_resume_command))

    # Inline Keyboard Button Callbacks (Delete confirmations)
    app.add_handler(CallbackQueryHandler(delete_education_callback, pattern=r"^del_edu:"))
    app.add_handler(CallbackQueryHandler(delete_experience_callback, pattern=r"^del_exp:"))
    app.add_handler(CallbackQueryHandler(delete_project_callback, pattern=r"^del_proj:"))
    app.add_handler(CallbackQueryHandler(delete_leadership_callback, pattern=r"^del_lead:"))

    # Import an existing resume: /importresume, then send the file; buttons confirm before anything is saved
    app.add_handler(CommandHandler("importresume", importresume_command))
    app.add_handler(MessageHandler(filters.Document.ALL, resume_document_handler, block=False))
    app.add_handler(CallbackQueryHandler(import_callback, pattern=r"^imp:"))

    # Answers to the Archivist's questions (buttons, and typed replies, which must be seen before other text handlers)
    app.add_handler(CallbackQueryHandler(ask_callback, pattern=r"^ask:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ask_text_handler), group=-1)

    # Job URL Scrape & Tailor Ingestion Handler
    app.add_handler(get_job_url_handler())

    # Global Error Handler
    app.add_error_handler(error_handler)

    return app


def main():
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

