from __future__ import annotations
import logging
import sys
from telegram import Update, BotCommand
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
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
        BotCommand("start", "Welcome & quick overview"),
        BotCommand("history", "View recent job applications"),
        BotCommand("resume", "Compile full master resume PDF"),
        BotCommand("myprofile", "View & edit contact info"),
        BotCommand("setname", "Update your full name"),
        BotCommand("setphone", "Update your phone number"),
        BotCommand("setemail", "Update your email address"),
        BotCommand("setlinkedin", "Update your LinkedIn URL"),
        BotCommand("setgithub", "Update your GitHub URL"),
        BotCommand("listeducation", "View education entries"),
        BotCommand("addeducation", "Add education entry"),
        BotCommand("deleteeducation", "Delete an education entry"),
        BotCommand("addcourse", "Add course to education"),
        BotCommand("deletecourse", "Remove a course"),
        BotCommand("addexperience", "Add work experience / internship"),
        BotCommand("deleteexperience", "Delete experience entry"),
        BotCommand("addproject", "Add project"),
        BotCommand("deleteproject", "Delete project"),
        BotCommand("addleadership", "Add leadership / activity"),
        BotCommand("deleteleadership", "Delete leadership entry"),
        BotCommand("listskills", "View skills by category"),
        BotCommand("addskill", "Add new skills"),
        BotCommand("deleteskill", "Delete a skill"),
        BotCommand("listentries", "View all resume entry IDs"),
        BotCommand("listfacts", "View fact bullets for an entry"),
        BotCommand("updatefact", "Edit a specific bullet"),
        BotCommand("deletefact", "Delete a fact bullet"),
        BotCommand("cancel", "Abort current prompt"),
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

    # Job URL Scrape & Tailor Ingestion Handler
    app.add_handler(get_job_url_handler())

    # Global Error Handler
    app.add_error_handler(error_handler)

    return app


def main():
    logger.info("Initializing database...")
    init_db()
    logger.info("Starting Internify Telegram Bot runner...")
    app = create_bot_app()
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

