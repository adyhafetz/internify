"""Move someone's existing single-user copy of Internify into the hosted version, as one of its people.

Reads the old copy's ``output`` folder without changing it, builds the same .zip layout as the Export my data file, checks it
with the normal import checks, then imports it into the person's hosted account (created if needed) and puts them on the
Unlimited plan. Google is not moved: after this they send /start in the hosted bot and connect Google again, and a new sheet is
made with their tracker in it."""
from __future__ import annotations

import io
import json
import os
import sqlite3
import time
import zipfile
from dataclasses import dataclass
from typing import Optional

from bot import data_export
from bot.services import accounts, plans
from service import users


class MigrateError(Exception):
    """A problem the person running the tool can fix; the message says how."""


@dataclass
class Result:
    telegram_id: int
    user_id: str
    created: bool
    counts: dict
    facts: int
    dry_run: bool


def _json(path: str, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _rows(conn: sqlite3.Connection, table: str) -> list[dict]:
    try:
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        if not cols:
            return []
        rows = conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
    except sqlite3.Error:
        return []
    return [{c: v for c, v in zip(cols, r) if c not in ("id", "user_id")} for r in rows]


def read_old_copy(folder: str, telegram_id: Optional[int] = None) -> tuple[bytes, int]:
    """Return (export zip bytes, the owner's Telegram id) for an old copy's output folder. Never writes to the folder."""
    folder = os.path.abspath(folder)
    dbfile = os.path.join(folder, "internify.db")
    if not os.path.isfile(dbfile):
        raise MigrateError(f"No internify.db in {folder}. Point this at the old copy's output folder.")
    data = os.path.join(folder, "data")
    facts = _json(os.path.join(data, "facts.json"), None)
    skeleton = _json(os.path.join(data, "resume_skeleton.json"), None)
    if not isinstance(facts, list) or not facts:
        raise MigrateError("The old copy has no saved experience (output/data/facts.json). Nothing to move.")
    if not isinstance(skeleton, dict):
        skeleton = {}
    try:   # open read-only, so the old copy is never changed, not even by a journal file
        conn = sqlite3.connect(f"file:{dbfile}?mode=ro", uri=True)
    except sqlite3.Error as e:
        raise MigrateError(f"Could not open the old database: {e}")
    try:
        tracker = {t: _rows(conn, t) for t in ("applications", "watch_sources", "seen_jobs")}
        settings: dict = {}
        try:
            owner = ""
            for r in conn.execute("SELECT key, value FROM settings").fetchall():
                if r[0] == "owner_chat_id":
                    owner = str(r[1])
                elif r[0] in data_export.SETTING_KEYS:
                    settings[r[0]] = r[1]
        except sqlite3.Error:
            owner = ""
    finally:
        conn.close()
    if telegram_id:
        owner = str(telegram_id)
    if not owner.lstrip("-").isdigit():
        raise MigrateError("The old copy never recorded its owner's Telegram id. Open the old bot and send /start once, then try again.")
    tracker["settings"] = settings
    manifest = {"app": data_export.APP, "schema_version": data_export.SCHEMA_VERSION, "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "counts": {"facts": len(facts), "applications": len(tracker["applications"]),
                           "job_pages": len(tracker["watch_sources"]), "seen_jobs": len(tracker["seen_jobs"])}}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest))
        z.writestr("data/facts.json", json.dumps(facts, ensure_ascii=False))
        z.writestr("data/resume_skeleton.json", json.dumps(skeleton, ensure_ascii=False))
        for member, name in (("data/github_index.json", "github_index.json"), ("data/outcomes.json", "outcomes.json")):
            p = os.path.join(data, name)
            if os.path.exists(p):
                z.writestr(member, open(p, encoding="utf-8").read())
        z.writestr("tracker.json", json.dumps(tracker, ensure_ascii=False, default=str))
    return buf.getvalue(), int(owner)


def migrate(folder: str, name: str = "", replace: bool = False, dry_run: bool = False, telegram_id: Optional[int] = None) -> Result:
    """Move the old copy into the hosted database the process is pointed at. ``telegram_id`` overrides the one in the old copy."""
    blob, tg = read_old_copy(folder, telegram_id)
    try:
        preview, content = data_export.inspect(blob)
    except Exception as e:
        raise MigrateError(f"The old copy's data did not pass the import checks: {e}")
    existing = accounts.by_telegram(tg)
    if existing and not replace:
        raise MigrateError(f"Telegram id {tg} already has a hosted account ({existing.id}). Add --replace to overwrite its data.")
    if dry_run:
        return Result(tg, existing.id if existing else "", existing is None, {"applications": len(content["tracker"].get("applications", [])),
                      "watch_sources": len(content["tracker"].get("watch_sources", [])), "seen_jobs": len(content["tracker"].get("seen_jobs", []))},
                      len(content["facts"]), True)
    acct = existing or accounts.create(tg, name)
    with users.use(acct.id):
        done = data_export.restore(content)
        # the person's own chat is theirs in the hosted bot too
        from bot.services import watchlist
        watchlist.set_setting("owner_chat_id", str(tg))
    accounts.set_plan(acct.id, plans.UNLIMITED)
    return Result(tg, acct.id, existing is None, done["counts"], done["facts"], False)
