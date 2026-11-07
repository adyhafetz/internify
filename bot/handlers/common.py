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
                    f"⛔ Sorry, this bot is private. Your user ID ({user.id}) isn't on its allowed list."
                )
            return ConversationHandler.END
        return await func(update, context, *args, **kwargs)
    return wrapped


@restricted
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.effective_user.first_name if update.effective_user else "there"
    await update.effective_message.reply_text(
        f"👋 Hi {name}! I'm Internify.\n\n"
        "Send me a job link and I'll write a 1-page resume for that job, using only your real experience, "
        "then send you the PDF.\n\n"
        "• Send me your current resume file (PDF or Word) and I'll fill in your experience for you\n"
        "• Paste a job link to get a tailored resume\n"
        "• /myprofile – your name and contact details\n"
        "• /history – jobs you've applied to\n"
        "• /resume – your general resume, not tailored to any job\n"
        "• /help – everything I can do\n\n"
        "Tip: you can type a command with its value, like /setname John Doe, or just /setname and I'll ask you."
    )


@restricted
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "📚 What I can do\n\n"
        "Tailor a resume\n"
        "• Paste a job link – I write a resume for that job\n"
        "• /history – jobs you've applied to\n"
        "• /resume – your general resume (not tailored)\n\n"
        "Your details\n"
        "• /myprofile – see your contact details\n"
        "• /setname, /setphone, /setemail, /setlinkedin, /setgithub – change them\n\n"
        "Your experience (what I choose from)\n"
        "• /importresume – send your current resume file (PDF/Word) and I'll fill this in for you\n"
        "• /addexperience – add a job or internship\n"
        "• /addproject – add a project\n"
        "• /addleadership – add a leadership role or activity\n"
        "• /addeducation – add a degree\n"
        "• /addskill – add skills\n"
        "Or describe each one in your own words and I'll turn it into resume bullet points.\n\n"
        "See and edit what's saved\n"
        "• /experience – open your degrees, jobs, projects and leadership roles, then tap to edit, add or delete "
        "any line. No IDs to look up.\n"
        "• /listskills, /addskill, /deleteskill [skill] – your skills\n\n"
        "• /cancel – stop whatever I'm asking you"
    )


@restricted
async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.effective_message.reply_text("🛑 Okay, cancelled.")
    return ConversationHandler.END

