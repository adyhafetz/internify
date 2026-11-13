"""⚙️ Status in /profile: what this copy of Internify is set up with and when its agents next run, in plain words,
plus a button that tests the AI key and the Google connection live. Handy when you run more than one copy."""
from __future__ import annotations

import asyncio
import html
import os
import time
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from bot import config
from bot.services import watchlist
from service import config as sc


def _h(t: object) -> str:
    return html.escape(str(t), quote=False)


def _btn(label: str, *parts: object) -> InlineKeyboardButton:
    return InlineKeyboardButton(label, callback_data="xp:" + ":".join(str(p) for p in parts))


def _next_run(application, name: str) -> Optional[str]:
    try:
        jobs = application.job_queue.get_jobs_by_name(name)
        when = jobs[0].next_t if jobs else None
        return when.astimezone().strftime("%a %d %b, %H:%M") if when else None
    except Exception:
        return None


def _github_line() -> str:
    if not sc.GITHUB_USERNAME:
        return "off (no GitHub username set)"
    from service import github_evidence
    idx = github_evidence.load_index()
    if idx.get("username") != sc.GITHUB_USERNAME or not idx.get("checked"):
        return f"{_h(sc.GITHUB_USERNAME)}, repos not read yet"
    hours = (time.time() - idx["checked"]) / 3600
    age = f"{int(hours * 60)} min" if hours < 1 else f"{int(hours)} h" if hours < 48 else f"{int(hours / 24)} days"
    return f"{_h(sc.GITHUB_USERNAME)}, {len(idx.get('repos', {}))} repos, read {age} ago"


def _looking_for() -> str:
    from bot.services.websearch import default_roles
    p = watchlist.get_prefs()
    roles = ", ".join(p.roles or default_roles())
    return (f"  looking for: {_h(roles)} · {_h(', '.join(p.places) or 'anywhere')}"
            f"{' · internships only' if p.interns_only else ''} · web search {'on' if p.web_search else 'off'}")


def screen(application=None, notice: str = ""):
    provider = "Google Gemini (free tier)" if sc.AI_PROVIDER == "gemini" else "OpenAI"
    google = "set up" if (config.GOOGLE_REFRESH_TOKEN or os.path.exists(config.GOOGLE_TOKEN_FILE)) and (config.GOOGLE_SHEET_ID or config.GOOGLE_DRIVE_FOLDER_ID) else "off"
    scan = "paused" if watchlist.scan_paused() else ("on" if config.SCAN_ENABLED else "off")
    lines = [notice, ""] if notice else []
    lines += [
        "⚙️ <b>Status</b>",
        "",
        "<b>Set up</b>",
        f"• AI: {provider}, key {'✓' if sc.OPENAI_API_KEY else '✕ missing'}",
        f"  writes with {_h(sc.GENERATOR_MODEL)}, checks with {_h(sc.CRITIC_MODEL)}, reads with {_h(sc.EXTRACTOR_MODEL)}",
        f"• Only you can use the bot: {'yes' if config.ALLOWED_USER_IDS else 'no (anyone who finds it can)'}",
        f"• GitHub: {_github_line()}",
        f"• Google Drive and Sheets: {google}",
        f"• ScrapingAnt (blocked job pages): {'set up' if config.SCRAPINGANT_API_KEY else 'off'}",
        f"• Dashboard: {_h(config.PUBLIC_URL + '/dashboard') if config.PUBLIC_URL else 'on this computer only' if config.DASHBOARD_ENABLED else 'off'}",
        "",
        "<b>Agents on a schedule</b>",
        f"• Daily job check ({_h(config.SCAN_TIME)} {_h(config.SCAN_TIMEZONE)}): {scan}"
        + (f", next {_next_run(application, 'daily-scan')}" if _next_run(application, "daily-scan") and scan == "on" else ""),
        f"  last: {_h(watchlist.get_setting('scan_last', 'not run yet'))}",
        _looking_for(),
        "• Tidy-up: every Sunday" + (f", next {_next_run(application, 'weekly-tidy')}" if _next_run(application, "weekly-tidy") else ""),
        f"  last: {_h(watchlist.get_setting('tidy_last', 'not run yet'))}",
    ]
    rows = [[_btn("🔍 Test connections", "sT")], [_btn("⬅ Back to profile", "l")]]
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def _test_ai() -> str:
    """Blocking. One cheap call that lists the models the key can use."""
    try:
        from service.clients import openai_client
        ids = {m.id for m in openai_client.models.list().data}
    except Exception as err:
        msg = str(err)
        if "401" in msg or "invalid" in msg.lower() or "incorrect" in msg.lower():
            return "✕ the AI key was rejected. Run the setup page again to fix it."
        return f"✕ couldn't reach the AI service ({_h(msg[:120])})"
    missing = [m for m in {sc.GENERATOR_MODEL, sc.CRITIC_MODEL, sc.EXTRACTOR_MODEL} if m not in ids and f"models/{m}" not in ids]
    return "✓ works" + (f", but these models aren't available to your key: {_h(', '.join(missing))}" if missing else "")


def _test_google() -> list[str]:
    from bot.services.google_check import check_google_setup
    return check_google_setup()


async def test(application=None):
    ai, google = await asyncio.gather(asyncio.to_thread(_test_ai), asyncio.to_thread(_test_google))
    lines = ["🔍 <b>Tested just now</b>", f"• AI: {ai}"] + [f"• {_h(line)}" for line in google]
    return screen(application, notice="\n".join(lines))
