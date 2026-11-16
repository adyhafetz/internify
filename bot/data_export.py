"""Export and import all your data as one .zip file, so you can move to another copy of Internify (or the hosted one) or keep a
copy of your own.

What is in it: your saved experience (profile and bullet points), the GitHub repo index, which bullet points got interviews,
your tracker (applications, the job pages you watch, jobs already seen) and your job-search settings.
What is never in it: API keys, Telegram or Google logins, your chat ID, resume PDFs (their Google Drive links are in the
tracker) and caches that are rebuilt automatically.

Importing REPLACES what you have now. Before that, a safety copy of the current data is saved in output/exports/ and the
profile is backed up the usual way, so it can be put back."""
from __future__ import annotations

import io
import json
import os
import time
import zipfile
from dataclasses import dataclass, field

from bot.services import db, watchlist
from service import github_evidence, importer, outcomes, users
from service.models import FactBullet, ResumeSkeleton

SCHEMA_VERSION = 1
APP = "internify"
MAX_ZIP_BYTES = 20 * 1024 * 1024
MAX_UNPACKED_BYTES = 80 * 1024 * 1024
KEEP_SAFETY_COPIES = 5
# Job-search settings that move with you (nothing about chats, logins or what is in progress)
SETTING_KEYS = ("scan_roles", "scan_places", "scan_interns_only", "scan_web_search", "scan_paused", "sources_removed",
                "src_linkedin", "src_jobstreet", "src_hiredly", "src_openweb")
MEMBERS = {"manifest.json", "tracker.json", "data/facts.json", "data/resume_skeleton.json", "data/github_index.json", "data/outcomes.json"}


class DataFileError(Exception):
    """The file can't be imported. The message is plain language for the user."""


@dataclass
class Preview:
    when: str = ""
    version: int = SCHEMA_VERSION
    facts: int = 0
    entries: int = 0
    applications: int = 0
    pages: int = 0
    seen: int = 0
    name: str = ""
    settings: list[str] = field(default_factory=list)


def _read_json(path: str, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def export_bytes() -> bytes:
    """Everything above, as the bytes of a .zip file."""
    importer.ensure_data_file(users.facts_path(), "facts.json")
    importer.ensure_data_file(users.skeleton_path(), "resume_skeleton.json")
    facts = _read_json(users.facts_path(), [])
    skeleton = _read_json(users.skeleton_path(), {})
    tracker = db.export_tracker()
    tracker["settings"] = {k: watchlist.get_setting(k, "") for k in SETTING_KEYS if watchlist.get_setting(k, "") != ""}
    manifest = {"app": APP, "schema_version": SCHEMA_VERSION, "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "counts": {"facts": len(facts), "applications": len(tracker["applications"]),
                           "job_pages": len(tracker["watch_sources"]), "seen_jobs": len(tracker["seen_jobs"])}}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest, indent=1))
        z.writestr("data/facts.json", json.dumps(facts, indent=1, ensure_ascii=False))
        z.writestr("data/resume_skeleton.json", json.dumps(skeleton, indent=1, ensure_ascii=False))
        for member, path in (("data/github_index.json", github_evidence._index_path()), ("data/outcomes.json", outcomes.path())):
            if os.path.exists(path):
                z.writestr(member, open(path, encoding="utf-8").read())
        z.writestr("tracker.json", json.dumps(tracker, indent=1, ensure_ascii=False, default=str))
    return buf.getvalue()


def filename() -> str:
    return f"internify-data-{time.strftime('%Y-%m-%d')}.zip"


# ---------------------------------------------------------------- reading a file safely

def _open(data: bytes) -> zipfile.ZipFile:
    if len(data) > MAX_ZIP_BYTES:
        raise DataFileError("That file is bigger than 20 MB, so it isn't an Internify data file.")
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise DataFileError("That isn't a zip file I can open. Send the file that 📦 My data → Export gave you.")
    names = set(z.namelist())
    if "manifest.json" not in names:
        raise DataFileError("That zip isn't an Internify data file (it has no manifest).")
    if sum(i.file_size for i in z.infolist()) > MAX_UNPACKED_BYTES:
        raise DataFileError("That file is far too big once opened, so I won't import it.")
    return z


def _load(z: zipfile.ZipFile, name: str, default=None):
    """One known member of the zip as JSON. Members are only ever read by name, never extracted to disk, so a file name
    inside the zip can't write anywhere."""
    if name not in z.namelist():
        return default
    try:
        return json.loads(z.read(name).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, KeyError):
        raise DataFileError(f"The file is damaged ({name} can't be read).")


def inspect(data: bytes) -> tuple[Preview, dict]:
    """Check the file and return (what is in it, the checked content). Nothing is changed."""
    z = _open(data)
    manifest = _load(z, "manifest.json", {})
    if not isinstance(manifest, dict) or manifest.get("app") != APP:
        raise DataFileError("That zip isn't an Internify data file.")
    version = manifest.get("schema_version")
    if not isinstance(version, int) or version < 1:
        raise DataFileError("That file has no valid version, so I can't tell how to read it.")
    if version > SCHEMA_VERSION:
        raise DataFileError(f"That file was made by a newer Internify (data version {version}). Update this copy first, then import it.")
    facts = _load(z, "data/facts.json", None)
    skeleton = _load(z, "data/resume_skeleton.json", None)
    if not isinstance(facts, list) or not isinstance(skeleton, dict):
        raise DataFileError("The file has no saved experience in it (or it is damaged).")
    try:
        [FactBullet(**f) for f in facts]
        sk = ResumeSkeleton(**skeleton)
    except Exception as err:
        raise DataFileError(f"The saved experience in the file doesn't look right ({str(err)[:120]}).")
    tracker = _load(z, "tracker.json", {})
    if not isinstance(tracker, dict):
        raise DataFileError("The tracker in the file is damaged.")
    for name in ("applications", "watch_sources", "seen_jobs"):
        if not isinstance(tracker.get(name, []), list):
            raise DataFileError(f"The tracker in the file is damaged ({name}).")
    settings = tracker.get("settings", {})
    settings = {k: str(v) for k, v in settings.items() if k in SETTING_KEYS} if isinstance(settings, dict) else {}
    content = {"facts": facts, "skeleton": skeleton, "tracker": tracker, "settings": settings,
               "github_index": _load(z, "data/github_index.json", None), "outcomes": _load(z, "data/outcomes.json", None)}
    pv = Preview(when=str(manifest.get("exported_at", ""))[:16], version=version, facts=len(facts),
                 entries=len(sk.experience_entries) + len(sk.project_entries),
                 applications=len(tracker.get("applications", [])), pages=len(tracker.get("watch_sources", [])),
                 seen=len(tracker.get("seen_jobs", [])), name=sk.header.name, settings=sorted(settings))
    return pv, content


# ---------------------------------------------------------------- replacing what you have

def safety_copy() -> str:
    """Save the current data as a zip in output/exports/ (only the newest few are kept) and return its path."""
    folder = users.exports_dir()
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"before-import-{time.strftime('%Y%m%d-%H%M%S')}.zip")
    with open(path, "wb") as f:
        f.write(export_bytes())
    old = sorted(n for n in os.listdir(folder) if n.startswith("before-import-"))
    for n in old[:-KEEP_SAFETY_COPIES]:
        try:
            os.remove(os.path.join(folder, n))
        except OSError:
            pass
    return path


def _write_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def restore(content: dict) -> dict:
    """Replace the profile, tracker and job-search settings with a checked file's content (from ``inspect``).
    Returns what was done. A safety copy and a profile backup are made first."""
    safety = safety_copy()
    backup = importer.backup_current("before importing a data file")
    _write_json(users.facts_path(), content["facts"])
    _write_json(users.skeleton_path(), content["skeleton"])
    try:   # the saved bullet points changed: their search index is rebuilt the next time it is needed
        os.remove(users.embedding_cache_path())
    except OSError:
        pass
    if isinstance(content.get("github_index"), dict):
        _write_json(github_evidence._index_path(), content["github_index"])
    if isinstance(content.get("outcomes"), dict):
        _write_json(outcomes.path(), content["outcomes"])
    counts = db.replace_tracker(content["tracker"])
    for key in SETTING_KEYS:
        if key in content["settings"]:
            watchlist.set_setting(key, content["settings"][key])
    return {"safety_copy": safety, "backup": backup, "counts": counts, "facts": len(content["facts"])}
