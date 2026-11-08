"""Make every Telegram message safe to send, whatever text goes into it.

Most handlers write messages in Telegram's legacy Markdown (*bold*, _italic_, `code`) with names, IDs and your resume's
bullet points mixed in. A stray `_`, `*` or `[` in that data makes Telegram refuse the whole message
("Can't parse entities"), or silently eat underscores (exp_sample_tech -> expsampletech).

SafeBot converts those messages to HTML before sending: everything is escaped, and a marker only becomes bold/italic
when it is a clean pair around a word, so `snake_case_names`, `2*3*4` and unmatched characters stay as plain text."""
from __future__ import annotations

import html
import re
from typing import Any

from telegram.ext import ExtBot

_PLACEHOLDER = "\x00{}\x00"
_FENCE = re.compile(r"```(?:[a-zA-Z0-9_+-]*\n)?(.*?)```", re.DOTALL)
_CODE = re.compile(r"`([^`\n]+)`")
_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)")
_TOKEN = re.compile(r"\x00(\d+)\x00")
# A marker pair only counts if it hugs non-space text and is not glued to a word (so snake_case and 2*3*4 are left alone).
_BOLD = re.compile(r"(?<![\w*])\*(?=\S)((?:[^*\n<]|<i>[^<]*</i>)+?)(?<=\S)\*(?![\w*])")
_ITALIC = re.compile(r"(?<![\w_])_(?=\S)((?:[^_\n<]|(?<=\w)_(?=\w)|<b>[^<]*</b>)+?)(?<=\S)_(?![\w_])")


def markdown_to_html(text: str) -> str:
    """Telegram legacy-Markdown text -> Telegram-safe HTML. Never raises, never produces broken markup."""
    text = (text or "").replace("\x00", "")
    held: list[str] = []

    def hold(markup: str) -> str:
        held.append(markup)
        return _PLACEHOLDER.format(len(held) - 1)

    text = _FENCE.sub(lambda m: hold(f"<pre>{html.escape(m.group(1).strip(chr(10)), quote=False)}</pre>"), text)
    text = _CODE.sub(lambda m: hold(f"<code>{html.escape(m.group(1), quote=False)}</code>"), text)
    text = _LINK.sub(lambda m: hold(f'<a href="{html.escape(m.group(2), quote=True)}">{html.escape(m.group(1), quote=False)}</a>'), text)
    text = html.escape(text, quote=False)

    def style(tag: str):
        def apply(m: re.Match) -> str:
            inner = m.group(1)
            # Telegram does not reliably render code/links inside bold or italic, so keep those parts plain
            return inner if _TOKEN.search(inner) else f"<{tag}>{inner}</{tag}>"
        return apply

    text = _BOLD.sub(style("b"), text)
    text = _ITALIC.sub(style("i"), text)
    return _TOKEN.sub(lambda m: held[int(m.group(1))], text)


def _convert(kwargs: dict[str, Any], text_key: str) -> None:
    if str(kwargs.get("parse_mode", "")).lower() == "markdown" and isinstance(kwargs.get(text_key), str):
        kwargs[text_key] = markdown_to_html(kwargs[text_key])
        kwargs["parse_mode"] = "HTML"


class SafeBot(ExtBot):
    """The bot used everywhere. Messages written for Markdown are converted to safe HTML on the way out."""

    async def send_message(self, chat_id, text, *args, **kwargs):  # type: ignore[override]
        if str(kwargs.get("parse_mode", "")).lower() == "markdown" and isinstance(text, str):
            text, kwargs["parse_mode"] = markdown_to_html(text), "HTML"
        return await super().send_message(chat_id, text, *args, **kwargs)

    async def edit_message_text(self, text, *args, **kwargs):  # type: ignore[override]
        if str(kwargs.get("parse_mode", "")).lower() == "markdown" and isinstance(text, str):
            text, kwargs["parse_mode"] = markdown_to_html(text), "HTML"
        return await super().edit_message_text(text, *args, **kwargs)
