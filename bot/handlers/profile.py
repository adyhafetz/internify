from __future__ import annotations
import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
)
from bot.handlers.common import restricted
from service.admin import _load_skeleton_raw, update_header, UpdateHeaderRequest

(
    WAITING_NAME,
    WAITING_PHONE,
    WAITING_EMAIL,
    WAITING_LINKEDIN,
    WAITING_GITHUB,
) = range(5)


@restricted
async def myprofile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    skeleton = _load_skeleton_raw()
    header = skeleton.get("header", {})
    name = header.get("name", "Not set")
    phone = header.get("phone", "Not set")
    email = header.get("email", "Not set")
    li_disp = header.get("linkedin_display", "Not set")
    li_url = header.get("linkedin_url", "")
    gh_disp = header.get("github_display", "Not set")
    gh_url = header.get("github_url", "")

    msg = (
        "👤 *Your Current Profile:*\n\n"
        f"• *Name:* {name}\n"
        f"• *Phone:* `{phone}`\n"
        f"• *Email:* `{email}`\n"
        f"• *LinkedIn:* [{li_disp}]({li_url})\n"
        f"• *GitHub:* [{gh_disp}]({gh_url})\n\n"
        "_Click a button below or use /setname, /setphone, etc. to update:_"
    )

    keyboard = [
        [
            InlineKeyboardButton("✏️ Name", callback_data="edit_name"),
            InlineKeyboardButton("📞 Phone", callback_data="edit_phone"),
            InlineKeyboardButton("✉️ Email", callback_data="edit_email"),
        ],
        [
            InlineKeyboardButton("🔗 LinkedIn", callback_data="edit_linkedin"),
            InlineKeyboardButton("🐙 GitHub", callback_data="edit_github"),
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(msg, parse_mode="Markdown", reply_markup=reply_markup)
    else:
        await update.effective_message.reply_text(msg, parse_mode="Markdown", reply_markup=reply_markup)


def _format_linkedin_display(url: str) -> str:
    cleaned = re.sub(r"^https?://(www\.)?", "", url).rstrip("/")
    return cleaned or url


def _format_github_display(url: str) -> str:
    cleaned = re.sub(r"^https?://(www\.)?", "", url).rstrip("/")
    return cleaned or url


# ---------- /setname ----------

@restricted
async def set_name_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        name = " ".join(context.args).strip()
        update_header(UpdateHeaderRequest(name=name))
        await update.effective_message.reply_text(f"✅ Name updated to: *{name}*", parse_mode="Markdown")
        return ConversationHandler.END

    await update.effective_message.reply_text(
        "✍️ *Please enter your full name* (or send /cancel):",
        parse_mode="Markdown",
    )
    return WAITING_NAME


async def set_name_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.effective_message.text.strip()
    if not name:
        await update.effective_message.reply_text("Name cannot be empty. Please enter your name:")
        return WAITING_NAME

    update_header(UpdateHeaderRequest(name=name))
    await update.effective_message.reply_text(f"✅ Name successfully saved as: *{name}*", parse_mode="Markdown")
    return ConversationHandler.END


# ---------- /setphone ----------

@restricted
async def set_phone_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        phone = " ".join(context.args).strip()
        update_header(UpdateHeaderRequest(phone=phone))
        await update.effective_message.reply_text(f"✅ Phone updated to: `{phone}`", parse_mode="Markdown")
        return ConversationHandler.END

    await update.effective_message.reply_text(
        "📞 *Please enter your phone number* (e.g. `+1 555-019-2834`, or /cancel):",
        parse_mode="Markdown",
    )
    return WAITING_PHONE


async def set_phone_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.effective_message.text.strip()
    update_header(UpdateHeaderRequest(phone=phone))
    await update.effective_message.reply_text(f"✅ Phone successfully saved as: `{phone}`", parse_mode="Markdown")
    return ConversationHandler.END


# ---------- /setemail ----------

@restricted
async def set_email_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        email = context.args[0].strip()
        update_header(UpdateHeaderRequest(email=email))
        await update.effective_message.reply_text(f"✅ Email updated to: `{email}`", parse_mode="Markdown")
        return ConversationHandler.END

    await update.effective_message.reply_text(
        "✉️ *Please enter your email address* (or send /cancel):",
        parse_mode="Markdown",
    )
    return WAITING_EMAIL


async def set_email_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    email = update.effective_message.text.strip()
    update_header(UpdateHeaderRequest(email=email))
    await update.effective_message.reply_text(f"✅ Email successfully saved as: `{email}`", parse_mode="Markdown")
    return ConversationHandler.END


# ---------- /setlinkedin ----------

@restricted
async def set_linkedin_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        url = context.args[0].strip()
        disp = _format_linkedin_display(url)
        update_header(UpdateHeaderRequest(linkedin_url=url))
        await update.effective_message.reply_text(f"✅ LinkedIn updated to: [{disp}]({url})", parse_mode="Markdown")
        return ConversationHandler.END

    await update.effective_message.reply_text(
        "🔗 *Please enter your LinkedIn profile URL* (or send /cancel):",
        parse_mode="Markdown",
    )
    return WAITING_LINKEDIN


async def set_linkedin_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.effective_message.text.strip()
    disp = _format_linkedin_display(url)
    update_header(UpdateHeaderRequest(linkedin_url=url))
    await update.effective_message.reply_text(f"✅ LinkedIn updated to: [{disp}]({url})", parse_mode="Markdown")
    return ConversationHandler.END


# ---------- /setgithub ----------

@restricted
async def set_github_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        url = context.args[0].strip()
        disp = _format_github_display(url)
        update_header(UpdateHeaderRequest(github_url=url))
        await update.effective_message.reply_text(f"✅ GitHub updated to: [{disp}]({url})", parse_mode="Markdown")
        return ConversationHandler.END

    await update.effective_message.reply_text(
        "🐙 *Please enter your GitHub profile URL* (or send /cancel):",
        parse_mode="Markdown",
    )
    return WAITING_GITHUB


async def set_github_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.effective_message.text.strip()
    disp = _format_github_display(url)
    update_header(UpdateHeaderRequest(github_url=url))
    await update.effective_message.reply_text(f"✅ GitHub updated to: [{disp}]({url})", parse_mode="Markdown")
    return ConversationHandler.END


# ---------- Callback query handlers for /myprofile buttons ----------

async def profile_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "edit_name":
        await query.message.reply_text("✍️ *Please enter your full name* (or send /cancel):", parse_mode="Markdown")
        return WAITING_NAME
    elif data == "edit_phone":
        await query.message.reply_text("📞 *Please enter your phone number* (or send /cancel):", parse_mode="Markdown")
        return WAITING_PHONE
    elif data == "edit_email":
        await query.message.reply_text("✉️ *Please enter your email address* (or send /cancel):", parse_mode="Markdown")
        return WAITING_EMAIL
    elif data == "edit_linkedin":
        await query.message.reply_text("🔗 *Please enter your LinkedIn profile URL* (or send /cancel):", parse_mode="Markdown")
        return WAITING_LINKEDIN
    elif data == "edit_github":
        await query.message.reply_text("🐙 *Please enter your GitHub profile URL* (or send /cancel):", parse_mode="Markdown")
        return WAITING_GITHUB


def get_profile_conversation_handler() -> ConversationHandler:
    from bot.handlers.common import cancel_command

    return ConversationHandler(
        entry_points=[
            CommandHandler("setname", set_name_start),
            CommandHandler("setphone", set_phone_start),
            CommandHandler("setemail", set_email_start),
            CommandHandler("setlinkedin", set_linkedin_start),
            CommandHandler("setgithub", set_github_start),
            CallbackQueryHandler(profile_button_callback, pattern=r"^edit_"),
        ],
        states={
            WAITING_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_name_save)],
            WAITING_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_phone_save)],
            WAITING_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_email_save)],
            WAITING_LINKEDIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_linkedin_save)],
            WAITING_GITHUB: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_github_save)],
        },
        fallbacks=[CommandHandler("cancel", cancel_command)],
        allow_reentry=True,
    )

