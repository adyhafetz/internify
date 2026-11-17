"""Daily backups for the hosted version: the database and every person's files, in one dated .tar.gz, the newest 14 kept.

Written to ``output/server_backups/``. That folder is on the same server, so copy it somewhere else too (see docs/hosted_setup.md):
a backup that lives only on the machine it protects is lost with that machine. The ``.env`` file is never included, so back up
``.env`` (and above all GOOGLE_TOKEN_KEY) on its own: without that key the saved Google connections cannot be read.
Restore with ``python scripts/restore_backup.py <file>``."""
from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import tarfile
import tempfile
import time
from typing import Optional

from bot import config
from bot.services import db
from service import config as service_config

logger = logging.getLogger(__name__)

KEEP = 14
STALE_HOURS = 36
PREFIX = "internify-backup-"


def folder() -> str:
    return os.path.join(service_config.OUTPUT_DIR, "server_backups")


def make() -> str:
    """Write a backup now and return its path. The database is copied with SQLite's own online backup, so it is consistent
    even while the bot is writing to it."""
    os.makedirs(folder(), exist_ok=True)
    name = f"{PREFIX}{time.strftime('%Y%m%d-%H%M%S')}.tar.gz"
    final = os.path.join(folder(), name)
    with tempfile.TemporaryDirectory() as tmp:
        copy = os.path.join(tmp, "internify.db")
        src, dst = sqlite3.connect(db.DB_PATH), sqlite3.connect(copy)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        part = final + ".part"
        with tarfile.open(part, "w:gz") as tar:
            tar.add(copy, arcname="internify.db")
            users_dir = os.path.join(service_config.OUTPUT_DIR, "users")
            if os.path.isdir(users_dir):
                tar.add(users_dir, arcname="users")
        os.replace(part, final)
    prune()
    logger.info("Backup written: %s", final)
    return final


def listing() -> list[str]:
    try:
        return sorted(n for n in os.listdir(folder()) if n.startswith(PREFIX) and n.endswith(".tar.gz"))
    except OSError:
        return []


def prune(keep: int = KEEP) -> None:
    for n in listing()[:-keep]:
        try:
            os.remove(os.path.join(folder(), n))
        except OSError:
            pass


def age_hours() -> Optional[float]:
    """Hours since the newest backup, or None if there is none."""
    names = listing()
    if not names:
        return None
    return (time.time() - os.path.getmtime(os.path.join(folder(), names[-1]))) / 3600


def status_line() -> str:
    h = age_hours()
    if h is None:
        return "⚠ No backup yet"
    text = f"last backup {round(h)} h ago" if h >= 1 else "last backup under an hour ago"
    return ("⚠ " if h > STALE_HOURS else "✓ ") + text


def restore(path: str, target: str) -> None:
    """Unpack a backup into ``target`` (a folder, normally the bot's ``output`` folder), putting the database back as
    internify.db. The bot must be stopped. Refuses a file that is not one of ours or that tries to write outside ``target``."""
    if not tarfile.is_tarfile(path):
        raise ValueError("That is not a backup file made by Internify.")
    with tarfile.open(path) as tar:
        names = tar.getnames()
        if "internify.db" not in names:
            raise ValueError("That is not a backup file made by Internify (no database in it).")
        for n in names:
            if not (n == "internify.db" or n == "users" or n.startswith("users/")) or ".." in n.split("/"):
                raise ValueError(f"Refusing to restore: unexpected item {n!r} in the file.")
        os.makedirs(target, exist_ok=True)
        tar.extractall(target, filter="data")


async def job(context) -> None:
    """The daily job: back up, and tell the maintainer if it failed."""
    try:
        await asyncio.to_thread(make)
    except Exception:
        logger.exception("Backup failed")
        if config.ADMIN_TELEGRAM_ID:
            try:
                await context.bot.send_message(config.ADMIN_TELEGRAM_ID, "⚠ Today's backup failed. Check the server's disk space and the logs.")
            except Exception:
                pass


def schedule(application) -> None:
    jq = getattr(application, "job_queue", None)
    if jq is None:
        return
    from datetime import time as _t
    from zoneinfo import ZoneInfo
    try:
        at = _t(3, 30, tzinfo=ZoneInfo(config.SCAN_TIMEZONE))
    except Exception:
        at = _t(3, 30)
    jq.run_daily(job, time=at, name="backup")


