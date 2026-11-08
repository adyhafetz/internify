from __future__ import annotations
import functools
import logging
from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler
from bot import config

logger = logging.getLogger(__name__)


_remembered_chat = None


def _remember_chat(update: Update) -> None:
    """The scheduled scan has no message to reply to, so remember which chat is yours (once, when it changes)."""
    global _remembered_chat
    chat = getattr(update, "effective_chat", None)
    if chat is None or chat.id == _remembered_chat:
        return
    try:
        from bot.services import watchlist
        watchlist.set_setting("owner_chat_id", str(chat.id))
        _remembered_chat = chat.id
    except Exception as err:   # never let bookkeeping break a command
        logger.debug("Could not remember the chat: %s", err)


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
        _remember_chat(update)
        return await func(update, context, *args, **kwargs)
    return wrapped


@restricted
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.effective_user.first_name if update.effective_user else "there"
    await update.effective_message.reply_text(
        f"👋 Hi {name}! I'm Internify.\n\n"
        "Send me a job link and I'll write a 1-page resume for that job, using only your real experience, "
        "then send you the PDF.\n\n"
        "First, tell me about you:\n"
        "• Send me your current resume file (PDF or Word) and I'll fill in your experience\n"
        "• or use /profile to add it yourself\n\n"
        "Other things:\n"
        "• /profile – see and edit everything on your resume\n"
        "• /history – jobs you've applied to\n"
        "• /resume – your general resume, not tailored to any job\n"
        "• /help – everything I can do"
    )


@restricted
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "📚 What I can do\n\n"
        "Tailor a resume\n"
        "• Paste a job link – I write a resume for that job\n"
        "• /history – jobs you've applied to\n"
        "• /resume – your general resume (not tailored)\n\n"
        "Tell me about you\n"
        "• /importresume – send your current resume file (PDF or Word) and I'll fill everything in\n"
        "• /profile – see and edit everything on your resume: contact details, education, jobs, projects, "
        "leadership and skills. Tap anything to edit, delete or add a line, and use ➕ Add something for new entries "
        "(you describe it in your own words). No IDs to look up.\n\n"
        "Find jobs for me\n"
        "• Every day I check the job pages you choose, pick the ones that match you best and write a resume for each. "
        "Choose the pages in /profile → Job pages I check, and tap Check now to try it.\n\n"
        "• /cancel – stop whatever I'm asking you"
    )


@restricted
async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.effective_message.reply_text("🛑 Okay, cancelled.")
