"""Offline test of the foundation for many users (MODE=cloud): who is acting, where each person's files live, and that two
people's tracker rows, settings, saved experience and files never mix. Also that a copy from before users existed (single
mode) keeps every row and behaves exactly as before. The AI and Telegram are faked."""
from __future__ import annotations
import asyncio, os, sqlite3, sys, tempfile, types

sys.path.insert(0, os.path.abspath("."))
tmp = tempfile.mkdtemp()
os.environ.update(OPENAI_API_KEY="sk-test", DATABASE_PATH=os.path.join(tmp, "t.db"), OUTPUT_DIR=os.path.join(tmp, "out"),
                  FACT_STORE_PATH=os.path.join(tmp, "out", "data", "facts.json"), SKELETON_PATH=os.path.join(tmp, "out", "data", "resume_skeleton.json"),
                  EMBEDDING_CACHE_PATH=os.path.join(tmp, "out", "data", "embeddings_cache.json"))
os.environ.pop("OUTCOMES_PATH", None); os.environ.pop("MODE", None)

from service import config, events, github_evidence, importer, outcomes, users
from service.profile_data import _load_facts_raw, _load_skeleton_raw, _save_facts_raw, _save_skeleton_raw
from service.fact_store import store
from bot import data_export, scanner
from bot.services import db, watchlist

A, B = "aaaaaa11", "bbbbbb22"
run = asyncio.run

# ---- 1. single mode: the owner, and every path where it has always been
assert users.MODE == "single" and not users.cloud() and users.current() == "owner"
assert users.valid("owner") and not users.valid("aaaaaa11") and not users.valid(None)
assert users.facts_path() == config.FACT_STORE_PATH and users.skeleton_path() == config.SKELETON_PATH and users.embedding_cache_path() == config.EMBEDDING_CACHE_PATH
assert users.data_dir() == os.path.dirname(config.FACT_STORE_PATH) and users.output_dir() == config.OUTPUT_DIR
assert users.backups_dir() == os.path.join(os.path.dirname(config.FACT_STORE_PATH), "backups")
assert users.github_index_path() == os.path.join(os.path.dirname(config.FACT_STORE_PATH), "github_index.json")
assert users.outcomes_path() == os.path.join(os.path.dirname(config.FACT_STORE_PATH), "outcomes.json")
with users.use("owner"): assert users.current() == "owner"
try: users.use("someone").__enter__(); raise SystemExit("should refuse")
except ValueError: pass
config.GITHUB_USERNAME = "adyhafetz"; assert users.github_username() == "adyhafetz"
print("1 ok: single mode is always the owner, with every path where it has always been")

# ---- 2. old database (before users existed) keeps every row as the owner's
old = os.path.join(tmp, "old.db")
with sqlite3.connect(old) as c:
    c.execute("CREATE TABLE applications (id INTEGER PRIMARY KEY AUTOINCREMENT, company TEXT NOT NULL, job_title TEXT NOT NULL, location TEXT DEFAULT '', job_description TEXT DEFAULT '', job_requirements TEXT DEFAULT '', job_link TEXT UNIQUE, status TEXT DEFAULT 'New', score REAL DEFAULT 0.0, resume_status TEXT DEFAULT '', pdf_path TEXT DEFAULT '', drive_link TEXT DEFAULT '', comment TEXT DEFAULT '', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    c.execute("CREATE INDEX idx_apps_job_link ON applications(job_link)")
    c.execute("CREATE TABLE watch_sources (id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT UNIQUE NOT NULL, kind TEXT NOT NULL, ident TEXT DEFAULT '', label TEXT DEFAULT '', auto INTEGER DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, last_checked TIMESTAMP, last_status TEXT DEFAULT '')")
    c.execute("CREATE TABLE seen_jobs (url TEXT PRIMARY KEY, title TEXT DEFAULT '', company TEXT DEFAULT '', status TEXT DEFAULT 'new', fit REAL DEFAULT 0.0, reason TEXT DEFAULT '', first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    c.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    c.execute("INSERT INTO applications (id, company, job_title, job_link, status, score) VALUES (7, 'Shopee', 'Data Intern', 'https://s/1', 'Applied', 72.5)")
    c.execute("INSERT INTO applications (id, company, job_title, job_link, status) VALUES (9, 'Grab', 'SWE Intern', 'https://g/1', 'Interview')")
    c.execute("INSERT INTO watch_sources (url, kind, label, auto) VALUES ('https://jobs.lever.co/zed', 'lever', 'Zed', 1)")
    c.execute("INSERT INTO seen_jobs (url, title, status) VALUES ('https://x/1', 'T', 'tailored')")
    c.execute("INSERT INTO settings (key, value) VALUES ('owner_chat_id', '42'), ('scan_roles', 'backend intern')")
saved_path = db.DB_PATH; db.DB_PATH = old
db.init_db(); db.init_db()      # twice: harmless
a_ = db.get_application(7)
assert a_["company"] == "Shopee" and a_["status"] == "Applied" and a_["score"] == 72.5 and db.get_application(9)["status"] == "Interviewing", "ids kept, old status names renamed"
assert [s.label for s in watchlist.list_sources()] == ["Zed"] and watchlist.seen("https://x/1")
assert watchlist.get_setting("owner_chat_id") == "42" and watchlist.owner_chat_id() == 42 and watchlist.get_setting("scan_roles") == "backend intern"
with sqlite3.connect(old) as c:
    assert {r[0] for r in c.execute("SELECT user_id FROM applications")} == {"owner"} and not c.execute("SELECT name FROM sqlite_master WHERE name LIKE '%_old'").fetchall()
    assert c.execute("PRAGMA table_info(applications)").fetchall()[1][1] == "user_id"
new_id = db.upsert_application("Maybank", "Dev Intern", job_link="https://m/1")
assert new_id > 9, "new rows carry on after the old ids"
db.DB_PATH = saved_path
print("2 ok: an existing database keeps every row, with the same ids, as the owner's")

# ---- 3. cloud mode: strict
db.init_db()
users.MODE = "cloud"
try: users.current(); raise SystemExit("cloud mode with no user must fail")
except users.NoUserError: pass
for bad in ("owner", "../etc", "a/b", "AAAAAA11", "short", "x" * 30, "aaaaaa.1", "", None, 12345678):
    assert not users.valid(bad), bad
    try:
        with users.use(bad): raise SystemExit(f"{bad!r} must be refused")
    except ValueError: pass
assert users.valid(A) and users.valid("k3f9x2a1")
with users.use(A):
    assert users.current() == A
    assert users.facts_path() == os.path.join(tmp, "out", "users", A, "data", "facts.json")
    assert users.skeleton_path().endswith(os.path.join("users", A, "data", "resume_skeleton.json"))
    assert users.output_dir() == os.path.join(tmp, "out", "users", A, "pdf") and users.exports_dir() == os.path.join(tmp, "out", "users", A, "exports")
    assert users.backups_dir().startswith(os.path.join(tmp, "out", "users", A)) and users.outcomes_path().endswith(os.path.join(A, "data", "outcomes.json"))
    with users.use(B): assert users.current() == B and B in users.facts_path()
    assert users.current() == A, "leaving a block puts the previous user back"
assert users.facts_path(B) != users.facts_path(A)
try: db.upsert_application("X", "Y", job_link="u"); raise SystemExit("database use with no user must fail in cloud mode")
except users.NoUserError: pass
print("3 ok: cloud mode refuses to act without a valid user id (nothing that could climb out of a folder)")

# ---- 4. the user follows the work it starts, and nobody else's
seen = {}
async def work(name, uid, delay):
    with users.use(uid):
        await asyncio.sleep(delay)
        seen[name] = (users.current(), await asyncio.to_thread(users.current))      # to_thread keeps the user
        await asyncio.sleep(delay)
        seen[name + "2"] = users.current()
async def both(): await asyncio.gather(work("a", A, 0.02), work("b", B, 0.01))
run(both())
assert seen == {"a": (A, A), "b": (B, B), "a2": A, "b2": B}, seen
# the hand-over from a worker thread to the bot's loop does not carry the user: bot/ask.py passes it on by hand
from bot import ask
got = []
async def fake_ask_all(self, skills, run_id, user_id=None):
    users._current.set(user_id) if user_id else None
    got.append((user_id, users.current())); return []
ask.SkillQuestioner._ask_all = fake_ask_all
async def thread_side():
    loop = asyncio.get_running_loop(); q = ask.SkillQuestioner(object(), 1, loop)
    def in_thread():
        with users.use(B): return q(["Docker"])
    await asyncio.to_thread(in_thread)
run(thread_side())
assert got == [(B, B)], "the Archivist's questions run for the user whose job is being tailored"
print("4 ok: the user follows work into threads and tasks, and is passed on by hand where it can't")

# ---- 5. the tracker: two users, same links, nothing shared
with users.use(A):
    a1 = db.upsert_application("Shopee", "Data Intern", job_link="https://s/1", status="Processing"); db.update_application(a1, status="Ready", score=70.0)
    a2 = db.upsert_application("Grab", "SWE Intern", job_link="https://g/1", status="Processing")
    db.set_status(a1, db.APPLIED)
    watchlist.set_setting("scan_roles", "ai intern"); watchlist.add_source("https://jobs.lever.co/zed", label="Zed"); watchlist.mark_seen("https://x/1", "T", "C", "tailored")
with users.use(B):
    b1 = db.upsert_application("Shopee", "Data Intern", job_link="https://s/1", status="Processing")     # the SAME link: allowed
    assert b1 != a1 and db.get_application(b1)["status"] == "Processing"
    assert db.get_application(a1) is None and db.get_application(a2) is None, "another user's row is not visible"
    assert [x["company"] for x in db.all_applications()] == ["Shopee"] and db.status_counts() == {"Processing": 1}
    assert not db.is_duplicate_url("https://g/1") and db.is_duplicate_url("https://s/1")
    assert db.find_similar("Grab", "SWE Intern") is None and db.get_application_by_link("https://g/1") is None
    assert watchlist.get_setting("scan_roles", "none") == "none" and watchlist.list_sources() == [] and not watchlist.seen("https://x/1") and not watchlist.known("https://x/1")
    db.update_application(a1, status="Rejected", comment="hacked")        # another user's id: does nothing
    db.set_status(a1, db.OFFER); db.snooze_followup(a1, 30); db.mark_deadline_reminded(a1)
    watchlist.set_setting("scan_roles", "design intern"); wa = watchlist.add_source("https://jobs.lever.co/zed")
    assert [x.url for x in watchlist.list_sources()] == ["https://jobs.lever.co/zed"] and wa.label == "Zed"
    watchlist.mark_checked(wa.id, "ok: 3 jobs")
    assert db.week_stats()["written"] == 1 and db.week_stats()["applied"] == 0
with users.use(A):
    mine = db.get_application(a1)
    assert mine["status"] == db.APPLIED and mine["comment"] == "" and mine["score"] == 70.0, "B couldn't change A's row"
    assert db.status_counts() == {"Applied": 1, "Processing": 1} and db.week_stats()["applied"] == 1
    assert watchlist.get_setting("scan_roles") == "ai intern" and len(watchlist.list_sources()) == 1 and watchlist.seen("https://x/1")
    assert watchlist.remove_source(watchlist.list_sources()[0].id) and watchlist.list_sources() == []
with users.use(B):
    assert len(watchlist.list_sources()) == 1, "A removing a page doesn't remove B's"
    assert watchlist.remove_source(10_000) is False
with users.use(A):
    exported = db.export_tracker()
    assert [r["company"] for r in exported["applications"]] == ["Shopee", "Grab"] and all("user_id" not in r for r in exported["applications"])
with users.use(B):
    db.replace_tracker({"applications": [{"id": a1, "user_id": A, "company": "Imported", "job_title": "Dev", "job_link": "https://i/1"}], "watch_sources": [], "seen_jobs": []})
    assert [x["company"] for x in db.all_applications()] == ["Imported"] and db.all_applications()[0]["id"] != a1, "ids and owners in a file are never trusted"
with users.use(A):
    assert [x["company"] for x in db.all_applications()] == ["Shopee", "Grab"], "B importing didn't touch A"
print("5 ok: two users share nothing: same link allowed, rows, settings, pages and seen jobs apart, another user's ids do nothing")

# ---- 6. saved experience and files, per user
with users.use(A):
    assert importer.profile_is_sample(), "a new user starts from the sample profile (so no resume is written from it)"
    sk = _load_skeleton_raw(); sk["header"]["name"] = "Alice A"; _save_skeleton_raw(sk)
    fx = _load_facts_raw(); fx.append({"id": "a-only", "entry_id": sk["experience_entries"][0]["id"], "section": "experience", "text": "Alice's bullet", "tech_tags": []}); _save_facts_raw(fx)
    outcomes.record("x", ["a-only"], good=True); backup_a = importer.backup_current("a test")
    assert not importer.profile_is_sample() and store.load_skeleton().header.name == "Alice A" and any(f.id == "a-only" for f in store.load_facts())
with users.use(B):
    assert importer.profile_is_sample() and store.load_skeleton().header.name != "Alice A", "B sees none of A's profile"
    assert not any(f.id == "a-only" for f in store.load_facts()) and outcomes.boosts() == {}
    assert importer.list_backups() == [], "B has no backups of A's"
    sk = _load_skeleton_raw(); sk["header"]["name"] = "Bob B"; _save_skeleton_raw(sk)
with users.use(A):
    assert store.load_skeleton().header.name == "Alice A" and outcomes.boosts() == {"a-only": outcomes.STEP} and len(importer.list_backups()) == 1
assert os.path.isdir(os.path.join(tmp, "out", "users", A, "data")) and os.path.isdir(os.path.join(tmp, "out", "users", B, "data"))
assert backup_a.startswith(os.path.join(tmp, "out", "users", A))
print("6 ok: saved experience, backups and what worked before are per user; a new user starts from the sample profile")

# ---- 7. the data file: A's export holds only A, and importing into B leaves A alone
with users.use(A): blob = data_export.export_bytes()
with users.use(B):
    pv, content = data_export.inspect(blob)
    assert pv.name == "Alice A"
    report = data_export.restore(content)
    assert report["safety_copy"].startswith(os.path.join(tmp, "out", "users", B)) and store.load_skeleton().header.name == "Alice A"
    assert [x["company"] for x in db.all_applications()] == ["Shopee", "Grab"]
with users.use(A):
    assert store.load_skeleton().header.name == "Alice A" and len(db.all_applications()) == 2
print("7 ok: Export / Import my data works per user (the safety copy goes in the importing user's own folder)")

# ---- 8. everything else that was global
with users.use(A):
    watchlist.set_setting("github_username", "@alice-gh"); assert users.github_username() == "alice-gh"
with users.use(B): assert users.github_username() == "" and users.github_username(A) == "alice-gh", "GitHub username is per user in cloud mode"
with users.use(A): pa = github_evidence._index_path()
with users.use(B): assert github_evidence._index_path() != pa and B in github_evidence._index_path()
scanner._running.add(A)
with users.use(A): assert scanner.is_running()
with users.use(B): assert not scanner.is_running(), "A's scan running doesn't block B's"
scanner._running.discard(A)
with users.use(A):
    rid = events.start_run(url="https://j/1")
    assert events.bus.get_run(rid)[0]["data"]["user"] == A, "a run records whose it is"
from bot.handlers import common
with users.use(A): common._remember_chat(types.SimpleNamespace(effective_chat=types.SimpleNamespace(id=111)))
with users.use(B): common._remember_chat(types.SimpleNamespace(effective_chat=types.SimpleNamespace(id=222)))
with users.use(A): assert watchlist.owner_chat_id() == 111
with users.use(B): assert watchlist.owner_chat_id() == 222, "each user's chat is written down for them"
from bot import tracker
import bot.services.sheets as _sh
reads = []
_sh.read_statuses = lambda: reads.append(users.current()) or {}
with users.use(A): tracker.sync_from_sheet(force=True)
with users.use(A): tracker.sync_from_sheet()
with users.use(B): tracker.sync_from_sheet()
assert reads == [A, B], "A's recent read doesn't throttle B (and A is throttled), got %s" % reads
print("8 ok: GitHub username, repo index, running scan, run trace, chat and sheet sync are all per user")

# ---- 9. cloud mode can't be switched on by accident yet
from bot import main
os.environ.pop("INTERNIFY_CLOUD_PREVIEW", None)
try: main.create_bot_app(); raise SystemExit("cloud mode must be refused for now")
except SystemExit as e: assert e.code == 1
users.MODE = "single"
print("9 ok: MODE=cloud is refused at start-up until sign-up and limits exist")
print("\n✅ users test passed")
