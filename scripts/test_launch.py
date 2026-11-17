"""Offline test of what launching the hosted version needs: the payment reference (and finding someone by it), the start-up
check, daily backups (consistent copy, pruning, restore, refusing bad files) and moving an old single-user copy in (old
database with and without user ids, dry run, existing account, source never changed)."""
from __future__ import annotations
import hashlib, json, os, sqlite3, sys, tarfile, tempfile, time

sys.path.insert(0, os.path.abspath("."))
tmp = tempfile.mkdtemp()
out = os.path.join(tmp, "out")
os.environ.update(OPENAI_API_KEY="sk-test", DATABASE_PATH=os.path.join(tmp, "t.db"), OUTPUT_DIR=out,
                  FACT_STORE_PATH=os.path.join(out, "data", "facts.json"), SKELETON_PATH=os.path.join(out, "data", "resume_skeleton.json"),
                  EMBEDDING_CACHE_PATH=os.path.join(out, "data", "embeddings_cache.json"), TELEGRAM_BOT_TOKEN="123:abc")
for k in ("MODE", "OUTCOMES_PATH", "TELEGRAM_ALLOWED_USER_IDS", "INTERNIFY_CLOUD_PREVIEW"): os.environ.pop(k, None)

from bot import admin, backup, cloud, config, migrate
from bot.services import accounts, db, plans, watchlist
from service import users
from service.profile_data import _load_facts_raw, _load_skeleton_raw

db.init_db()
users.MODE = "cloud"
def check(c, msg):
    assert c, msg
    print("ok:", msg)

# ---- 1. payment reference
a = accounts.create(1001, "Aina")
b = accounts.create(1002, "Budi")
ref = accounts.reference(a.id)
check(ref == "INT-" + a.id[:6].upper() and len(ref) == 10, "reference is INT- and six letters")
check(accounts.by_reference(ref).id == a.id and accounts.by_reference(ref.lower()).id == a.id, "found by reference, any case")
check(accounts.by_reference(a.id[:6]).id == a.id, "found without the INT- too")
check(accounts.by_reference("INT-ZZZZZZ") is None and accounts.by_reference("") is None and accounts.by_reference("INT-12") is None, "unknown or malformed gives nobody")
check(admin.find(ref).id == a.id, "/setplan accepts the reference")
check(admin.find("1002").id == b.id and admin.find(b.id).id == b.id, "user id and Telegram id still work")
accounts.set_plan(a.id, plans.PAID, "2099-01-01")
with users.use(a.id):
    txt = cloud.plan_text(accounts.get(a.id))
check(ref in txt, "/plan shows the reference")
check(ref in cloud.welcome_text("Aina", False, True, ref), "upgrade welcome shows the reference")

# ---- 2. start-up check
for k, v in (("ADMIN_TELEGRAM_ID", 0), ("TELEGRAM_BOT_TOKEN", "123:abc"), ("PUBLIC_URL", ""), ("PAY_INSTRUCTIONS", cloud.DEFAULT_PAY)):
    setattr(config, k, v)
stop, warn = cloud.preflight()
check(any("ADMIN_TELEGRAM_ID" in s for s in stop), "refuses without an admin")
check(any("PUBLIC_URL" in w for w in warn) and any("PAY_INSTRUCTIONS" in w for w in warn) and any("backup" in w for w in warn), "warns about site, payment text, backups")
config.ADMIN_TELEGRAM_ID, config.PUBLIC_URL, config.PAY_INSTRUCTIONS = 5, "https://x.example", "Pay Maybank 123"
stop, warn = cloud.preflight()
check(stop == [], "no blockers when set")
check(not any(w.startswith(("PUBLIC_URL", "PAY_INSTRUCTIONS")) for w in warn), "fixed settings stop warning")
config.TELEGRAM_BOT_TOKEN = ""
check(any("TELEGRAM_BOT_TOKEN" in s for s in cloud.preflight()[0]), "refuses without the bot token")
config.TELEGRAM_BOT_TOKEN = "123:abc"

# ---- 3. backups
with users.use(a.id):
    os.makedirs(users.data_dir(), exist_ok=True)
    open(users.facts_path(), "w").write('[{"id":"x"}]')
check(backup.age_hours() is None and "No backup" in backup.status_line(), "no backup yet is reported")
p = backup.make()
check(os.path.isfile(p) and "internify.db" in tarfile.open(p).getnames(), "backup holds the database")
names = tarfile.open(p).getnames()
check(any(n.startswith("users/") and n.endswith("facts.json") for n in names), "backup holds each person's files")
check(not any(".env" in n for n in names) and not any("server_backups" in n for n in names), "no .env, no backups inside backups")
check(backup.age_hours() < 0.1 and "✓" in backup.status_line(), "fresh backup reads as fine")
old = os.path.join(backup.folder(), "internify-backup-20200101-000000.tar.gz")
open(old, "w").write("x"); os.utime(old, (time.time() - 90 * 3600,) * 2)
for i in range(16):
    open(os.path.join(backup.folder(), f"internify-backup-2021{i:02d}01-000000.tar.gz"), "w").write("x")
backup.prune()
check(len(backup.listing()) == backup.KEEP and os.path.basename(p) in backup.listing(), "only the newest 14 are kept")
target = os.path.join(tmp, "restored")
backup.restore(p, target)
check(os.path.isfile(os.path.join(target, "internify.db")) and sqlite3.connect(os.path.join(target, "internify.db")).execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 2, "restore brings back the database")
check(os.path.isfile(os.path.join(target, "users", a.id, "data", "facts.json")), "restore brings back the files")
bad = os.path.join(tmp, "bad.tar.gz")
with tarfile.open(bad, "w:gz") as t:
    f = os.path.join(tmp, "f.txt"); open(f, "w").write("x"); t.add(f, arcname="../evil.txt")
for path, why in ((bad, "path outside the folder"), (f, "not a backup")):
    try: backup.restore(path, target); check(False, why)
    except ValueError: check(True, f"restore refuses: {why}")

# ---- 4. moving an old copy in
with users.use(b.id): FACTS, SKEL = _load_facts_raw(), _load_skeleton_raw()
def old_copy(with_user_id):
    root = os.path.join(tmp, "old_" + str(with_user_id)); os.makedirs(os.path.join(root, "data"))
    json.dump(FACTS, open(os.path.join(root, "data", "facts.json"), "w"))
    json.dump(SKEL, open(os.path.join(root, "data", "resume_skeleton.json"), "w"))
    c = sqlite3.connect(os.path.join(root, "internify.db"))
    u = "user_id TEXT NOT NULL DEFAULT 'owner'," if with_user_id else ""
    c.execute(f"CREATE TABLE applications (id INTEGER PRIMARY KEY, {u} company TEXT NOT NULL, job_title TEXT NOT NULL, status TEXT)")
    c.execute("CREATE TABLE settings (key TEXT, value TEXT)")
    c.execute("INSERT INTO applications (company, job_title, status) VALUES ('Acme','Intern','applied'), ('Beta','Dev','saved')")
    c.execute("INSERT INTO settings VALUES ('owner_chat_id','7777'), ('scan_roles','python'), ('secret_thing','nope')")
    c.commit(); c.close()
    return root
def digest(root): return {n: hashlib.md5(open(os.path.join(d, n), "rb").read()).hexdigest() for d, _, fs in os.walk(root) for n in fs for d in [d]}

for flag in (False, True):
    src = old_copy(flag); before = digest(src)
    tg = 7777 if not flag else 8888
    if flag:
        c = sqlite3.connect(os.path.join(src, "internify.db")); c.execute("UPDATE settings SET value='8888' WHERE key='owner_chat_id'"); c.commit(); c.close(); before = digest(src)
    r = migrate.migrate(src, dry_run=True)
    check(r.dry_run and accounts.by_telegram(tg) is None and r.counts["applications"] == 2 and r.facts == len(FACTS), f"dry run changes nothing (user_id column: {flag})")
    r = migrate.migrate(src, name="Old Owner")
    acct = accounts.by_telegram(tg)
    check(acct and acct.plan == plans.UNLIMITED and r.created, "account created on Unlimited")
    with users.use(acct.id):
        apps = db.export_tracker()["applications"]
        check(sorted(x["company"] for x in apps) == ["Acme", "Beta"], "applications moved")
        check(watchlist.get_setting("scan_roles", "") == "python" and watchlist.get_setting("secret_thing", "") == "", "only known settings moved")
        check(watchlist.owner_chat_id() == tg, "chat id set in the new account")
    check(digest(src) == before, "old folder untouched")
    try: migrate.migrate(src); check(False, "existing account")
    except migrate.MigrateError as e: check("--replace" in str(e), "existing account refused without --replace")
    check(migrate.migrate(src, replace=True).created is False, "--replace overwrites")

for bad_dir, needle in ((os.path.join(tmp, "nothing"), "internify.db"),):
    try: migrate.migrate(bad_dir); check(False, "missing folder")
    except migrate.MigrateError as e: check(needle in str(e), "missing folder gives a plain message")
nofacts = old_copy("nf"); os.remove(os.path.join(nofacts, "data", "facts.json"))
try: migrate.migrate(nofacts); check(False, "no facts")
except migrate.MigrateError as e: check("saved experience" in str(e), "no saved experience gives a plain message")
noowner = old_copy("no"); c = sqlite3.connect(os.path.join(noowner, "internify.db")); c.execute("DELETE FROM settings WHERE key='owner_chat_id'"); c.commit(); c.close()
try: migrate.migrate(noowner); check(False, "no owner")
except migrate.MigrateError: check(migrate.migrate(noowner, telegram_id=9999, dry_run=True).telegram_id == 9999, "no owner id: refused, but --telegram-id works")
print("ALL OK")
