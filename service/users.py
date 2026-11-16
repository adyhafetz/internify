"""Who is acting, and where that person's data lives.

Internify runs in one of two modes (the MODE setting):
  * single (the default): one person with their own keys, as always. The current user is always the "owner" and every file
    stays where it has always been (output/data/...). Nothing changes for an existing copy.
  * cloud: many people on one server. Each person has a random user id, their own folder (output/users/<id>/) and their
    own rows in the database. Acting with no user set is an error, never a silent fall-back to someone else's data.

The current user travels with the running code (a context variable), so it follows a Telegram handler into the work it
starts, including ``asyncio.to_thread``. It does NOT follow ``asyncio.run_coroutine_threadsafe`` (a worker thread handing a
coroutine to the bot's loop): that hand-over must pass the user on by hand (see ``bot/ask.py``)."""
from __future__ import annotations

import contextlib
import contextvars
import os
import re
from typing import Iterator, Optional

from service import config

OWNER = "owner"
MODE = os.getenv("MODE", "single").strip().lower() or "single"
_ID = re.compile(r"^[a-z0-9]{6,24}$")      # a cloud user id: lower-case letters and digits, so it is safe in a path
_current: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("internify_user", default=None)


class NoUserError(RuntimeError):
    """Cloud mode, and the code that needs a user was run without one. This is a bug, never something to work around."""


def cloud() -> bool:
    return MODE == "cloud"


def valid(user_id: object) -> bool:
    """Is this a user id that may be used (and put in a file path)?"""
    if not isinstance(user_id, str):
        return False
    return user_id == OWNER if not cloud() else bool(_ID.match(user_id))


def current() -> str:
    """The user who is acting now: the owner in single mode, otherwise the one set by ``use``."""
    uid = _current.get()
    if uid is None:
        if cloud():
            raise NoUserError("No user is set. In cloud mode every action must run for a specific user.")
        return OWNER
    return uid


@contextlib.contextmanager
def use(user_id: str) -> Iterator[str]:
    """Act as this user for the code inside the ``with`` block (and the work it starts)."""
    if not valid(user_id):
        raise ValueError(f"Not a valid user id: {user_id!r}")
    token = _current.set(user_id)
    try:
        yield user_id
    finally:
        _current.reset(token)


# ---------------------------------------------------------------- where the data lives

def _uid(user_id: Optional[str]) -> str:
    uid = user_id if user_id is not None else current()
    if not valid(uid):
        raise ValueError(f"Not a valid user id: {uid!r}")
    return uid


def user_root(user_id: Optional[str] = None) -> str:
    """The user's own folder (cloud mode only)."""
    return os.path.join(config.OUTPUT_DIR, "users", _uid(user_id))


def data_dir(user_id: Optional[str] = None) -> str:
    """Single mode: where your saved experience has always been (the folder of FACT_STORE_PATH)."""
    if not cloud():
        _uid(user_id)
        return os.path.dirname(config.FACT_STORE_PATH) or "."
    return os.path.join(user_root(user_id), "data")


def facts_path(user_id: Optional[str] = None) -> str:
    return config.FACT_STORE_PATH if not cloud() else os.path.join(data_dir(user_id), "facts.json")


def skeleton_path(user_id: Optional[str] = None) -> str:
    return config.SKELETON_PATH if not cloud() else os.path.join(data_dir(user_id), "resume_skeleton.json")


def embedding_cache_path(user_id: Optional[str] = None) -> str:
    return config.EMBEDDING_CACHE_PATH if not cloud() else os.path.join(data_dir(user_id), "embeddings_cache.json")


def backups_dir(user_id: Optional[str] = None) -> str:
    return os.path.join(data_dir(user_id), "backups")


def exports_dir(user_id: Optional[str] = None) -> str:
    """Safety copies saved before a data import."""
    if not cloud():
        _uid(user_id)
        return os.path.normpath(os.path.join(data_dir(user_id), "..", "exports"))
    return os.path.join(user_root(user_id), "exports")


def github_index_path(user_id: Optional[str] = None) -> str:
    return os.path.join(data_dir(user_id), "github_index.json")


def github_added_path(user_id: Optional[str] = None) -> str:
    return os.path.join(data_dir(user_id), "github_added.json")


def outcomes_path(user_id: Optional[str] = None) -> str:
    env = os.getenv("OUTCOMES_PATH")
    if env and not cloud():
        return env
    return os.path.join(data_dir(user_id), "outcomes.json")


def output_dir(user_id: Optional[str] = None) -> str:
    """Where this user's resume PDFs and .tex files are written."""
    return config.OUTPUT_DIR if not cloud() else os.path.join(user_root(user_id), "pdf")


def github_username(user_id: Optional[str] = None) -> str:
    """The GitHub username to look at: the setting of .env in single mode, this user's own setting in cloud mode."""
    if not cloud():
        return config.GITHUB_USERNAME
    from bot.services import watchlist   # the per-user settings live with the tracker's database
    with use(_uid(user_id)):
        return watchlist.get_setting("github_username", "").strip().lstrip("@")
