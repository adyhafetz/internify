from __future__ import annotations
import functools
import logging
from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler
from bot import config

logger = logging.getLogger(__name__)


def restricted(func):
    """Decorator to ensure only authorized Telegram users can interact with the bot."""
    @functools.wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user = update.effective_user
        if not user:
            return None
        if config.ALLOWED_USER_IDS and user.id not in config.ALLOWED_USER_IDS:
            logger.warning("Unauthorized access attempt by user %s (ID: %d)", user.username, user.id)
            if update.effective_message:
                await update.effective_message.reply_text(
                    "⛔ *Access Denied*\n"
                    f"Your user ID (`{user.id}`) is not authorized to control this Internify bot.",
                    parse_mode="Markdown",
                )
            return ConversationHandler.END
        return await func(update, context, *args, **kwargs)
    return wrapped


@restricted
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = (
        f"👋 *Welcome to Internify, {user.first_name if user else 'there'}!*\n\n"
        "I am your autonomous AI resume tailoring agent.\n\n"
        "✨ *Quick Actions:*\n"
        "• *Paste a Job URL*: Send any job link to tailor your resume and compile a 1-page PDF.\n"
        "• `/resume`: Generate your full master resume PDF.\n"
        "• `/myprofile`: View and edit your contact & header info.\n"
        "• `/help`: View all available commands.\n\n"
        "_Tip: For commands like `/setname`, you can either type `/setname John Doe` or just `/setname` to be prompted interactively!_"
    )
    await update.effective_message.reply_text(text, parse_mode="Markdown")


@restricted
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📚 *Internify Commands Cheat-Sheet*\n\n"
        "*Profile*\n"
        "• `/myprofile` – View contact information\n"
        "• `/setname [name]` – Update full name\n"
        "• `/setphone [phone]` – Update phone number\n"
        "• `/setemail [email]` – Update email\n"
        "• `/setlinkedin [url]` – Update LinkedIn link\n"
        "• `/setgithub [url]` – Update GitHub link\n\n"
        "*Education*\n"
        "• `/listeducation` – List degrees and IDs\n"
        "• `/addeducation [text]` – Add degree (interactive or text)\n"
        "• `/deleteeducation [id]` – Remove an education entry\n"
        "• `/addcourse [id] [course]` – Add relevant course to education\n"
        "• `/deletecourse [id] [course]` – Remove a course\n\n"
        "*Work Experience & Projects*\n"
        "• `/addexperience [text]` – Add internship/job (auto-extracts bullets)\n"
        "• `/deleteexperience [id]` – Remove experience entry\n"
        "• `/addproject [text]` – Add project (auto-extracts bullets)\n"
        "• `/deleteproject [id]` – Remove project\n"
        "• `/addleadership [text]` – Add leadership / extracurricular\n"
        "• `/deleteleadership [title]` – Remove leadership entry\n\n"
        "*Skills & Facts*\n"
        "• `/listskills` – View master skills by category\n"
        "• `/addskill [text]` – Add skills\n"
        "• `/deleteskill [skill]` – Remove skill\n"
        "• `/listentries` – View all entry IDs\n"
        "• `/listfacts [id]` – View fact bullets for an entry\n"
        "• `/updatefact [id] [text]` – Edit a bullet text\n"
        "• `/deletefact [id]` – Delete a fact bullet\n\n"
        "*Resumes & Applications*\n"
        "• `/resume` – Compile full untailored master resume PDF\n"
        "• *Paste URL* – Tailor resume against any job description"
    )
    await update.effective_message.reply_text(text, parse_mode="Markdown")


@restricted
async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.effective_message.reply_text("🛑 Current operation cancelled.", parse_mode="Markdown")
    return ConversationHandler.END

