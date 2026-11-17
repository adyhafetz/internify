"""Offline test of the hosted version's accounts (MODE=cloud): sign-up on /start only, who may use the bot (unknown, paused,
too fast), running every action as the right person, scheduled jobs once per active user, /plan, /deleteaccount (asked
twice, deletes only that person), and that single mode still works exactly as before. Telegram is faked."""
from __future__ import annotations
import asyncio, os, sqlite3, sys, tempfile, types

sys.path.insert(0, os.path.abspath("."))
tmp = tempfile.mkdtemp()
os.environ.update(OPENAI_API_KEY="sk-test", DATABASE_PATH=os.path.join(tmp, "t.db"), OUTPUT_DIR=os.path.join(tmp, "out"),
                  FACT_STORE_PATH=os.path.join(tmp, "out", "data", "facts.json"), SKELETON_PATH=os.path.join(tmp, "out", "data", "resume_skeleton.json"),
                  EMBEDDING_CACHE_PATH=os.path.join(tmp, "out", "data", "embeddings_cache.json"), TELEGRAM_BOT_TOKEN="123:abc")
for k in ("MODE", "OUTCOMES_PATH", "TELEGRAM_ALLOWED_USER_IDS", "INTERNIFY_CLOUD_PREVIEW"): os.environ.pop(k, None)

from bot import cloud, config, scanner, status
from bot.handlers import common
from bot.services import accounts, db, watchlist
from service import users
from service.profile_data import _load_skeleton_raw, _save_skeleton_raw

db.init_db()
run = asyncio.run

# ---------------------------------------------------------------- fakes
class Msg:
    def __init__(self, text=""): self.text, self.sent = text, []
    async def reply_text(self, t, **kw): self.sent.append(t)
class Query:
    def __init__(self, data): self.data, self.answers, self.edits, self.message = data, [], [], Msg()
    async def answer(self, t="", show_alert=False, **kw): self.answers.append((t, show_alert))
    async def edit_message_text(self, t, **kw): self.edits.append((t, kw.get("reply_markup")))
def update(tg_id, text="", name="Sam", query=None):
    msg = Msg(text)
    return types.SimpleNamespace(effective_user=types.SimpleNamespace(id=tg_id, first_name=name, username="u"), effective_message=msg,
                                 effective_chat=types.SimpleNamespace(id=tg_id), callback_query=query)
calls = []
@common.restricted
async def handler(update, context): calls.append(users.current()); await update.effective_message.reply_text("handled")
def ctx(): return types.SimpleNamespace(user_data={}, bot=object())

# ---- 1. single mode is untouched: the owner, the allowed list, no accounts
assert not users.cloud()
u = update(5, "hello"); run(handler(u, ctx())); assert calls == ["owner"] and u.effective_message.sent == ["handled"]
config.ALLOWED_USER_IDS = {99}; u = update(5); run(handler(u, ctx()))
assert "private" in u.effective_message.sent[0] and calls == ["owner"], "the allowed list still works in single mode"
config.ALLOWED_USER_IDS = set()
assert accounts.user_ids() == ["owner"]
print("1 ok: single mode is exactly as before (the owner, the allowed list, no accounts)")

# ---- 2. accounts: random ids, one per Telegram user, deleted ones forgotten
users.MODE = "cloud"
a = accounts.create(1001, "Alice"); b = accounts.create(1002, "Bob")
assert users.valid(a.id) and len(a.id) == 10 and a.id != b.id and a.plan == "free" and a.status == "active" and a.telegram_id == 1001
assert accounts.create(1001, "Alice again").id == a.id, "signing up twice gives the same account"
assert accounts.by_telegram(1002).id == b.id and accounts.by_telegram(7) is None and accounts.get("nope") is None
assert len({accounts.new_id() for _ in range(200)}) == 200
assert accounts.set_plan(a.id, "paid", "2027-01-01") and accounts.get(a.id).plan == "paid" and accounts.get(a.id).plan_until == "2027-01-01"
assert accounts.set_status(b.id, "paused", "chargeback") and accounts.get(b.id).status == "paused" and accounts.get(b.id).note == "chargeback"
try: accounts.set_status(b.id, "deleted"); raise SystemExit("deleting is not a status")
except ValueError: pass
assert [x.id for x in accounts.active()] == [a.id] and accounts.user_ids() == [a.id]
assert accounts.set_status(b.id, "active") and accounts.user_ids() == [a.id, b.id]
users.MODE = "single"; db.init_db(); db.init_db(); users.MODE = "cloud"
assert accounts.by_telegram(1001).id == a.id, "the accounts table survives start-ups (it is never rebuilt)"
with sqlite3.connect(os.environ["DATABASE_PATH"]) as c: assert "user_id" not in [r[1] for r in c.execute("PRAGMA table_info(accounts)")]
print("2 ok: accounts: random ids, one per person, plan and status, survive restarts")

# ---- 3. who gets in
calls.clear(); users.MODE = "cloud"
u = update(2001, "hi there"); run(handler(u, ctx()))
assert "Send /start" in u.effective_message.sent[0] and not calls and accounts.by_telegram(2001) is None, "a stranger's message creates nothing"
q = Query("xp:l"); u = update(2001, query=q); run(handler(u, ctx()))
assert "Send /start" in q.answers[0][0] and q.answers[0][1] and not calls, "...or a button tap"
c1 = ctx(); u = update(2001, "/start", name="Cara"); run(handler(u, c1))
new = accounts.by_telegram(2001)
assert new and calls == [new.id] and u.effective_message.sent == ["handled"] and c1.user_data.get("new_account") is True
with users.use(new.id): assert watchlist.owner_chat_id() == 2001, "their chat is written down for them"
u = update(2001, "/start@InternifyBot extra"); run(handler(u, ctx())); assert calls[-1] == new.id and len(accounts.active()) == 3, "no second account"
accounts.set_status(new.id, "paused")
u = update(2001, "hello"); run(handler(u, ctx())); assert "paused" in u.effective_message.sent[0] and calls[-1] == new.id and len(calls) == 2
u = update(2001, "/start"); run(handler(u, ctx())); assert "paused" in u.effective_message.sent[0], "paused people can't use /start to get round it"
accounts.set_status(new.id, "active")
config.RATE_LIMIT_PER_MINUTE = 3; cloud._hits.clear(); cloud._told.clear(); n = len(calls)
msgs = [update(2001, "x") for _ in range(6)]
for m in msgs: run(handler(m, ctx()))
assert len(calls) == n + 3 and "slow down" in msgs[3].effective_message.sent[0] and not msgs[4].effective_message.sent and not msgs[5].effective_message.sent, "told once, not every time"
assert cloud.too_fast(new.id, now=10**9) is False, "a minute later it is fine again"
config.RATE_LIMIT_PER_MINUTE = 30
print("3 ok: only /start makes an account; paused people are stopped; going too fast is slowed (told once)")

# ---- 4. every action runs as its own person
calls.clear(); seen = []
@common.restricted
async def who(update, context): seen.append((update.effective_user.id, users.current(), await asyncio.to_thread(users.current)))
async def two():
    await asyncio.gather(who(update(1001, "a"), ctx()), who(update(1002, "b"), ctx()), who(update(2001, "c"), ctx()))
run(two())
ids = {t: accounts.by_telegram(t).id for t in (1001, 1002, 2001)}
assert sorted(seen) == sorted((t, i, i) for t, i in ids.items()), seen
print("4 ok: at the same moment, three people each run as themselves (also inside worker threads)")

# ---- 5. quiet handlers (typed answers): only for people with an account
got = []
@cloud.quiet
async def typed(update, context): got.append(users.current())
run(typed(update(7777, "stranger"), ctx())); assert got == []
run(typed(update(1001, "me"), ctx())); assert got == [ids[1001]]
accounts.set_status(ids[1002], "paused"); run(typed(update(1002, "x"), ctx())); assert got == [ids[1001]]; accounts.set_status(ids[1002], "active")
users.MODE = "single"; run(typed(update(7777, "owner"), ctx())); assert got[-1] == "owner"; users.MODE = "cloud"
print("5 ok: typed answers are ignored from strangers and paused people")

# ---- 6. scheduled jobs: once for each active person, as that person; one failing doesn't stop the rest
for t in (1001, 1002, 2001):
    with users.use(ids[t]): watchlist.set_setting("owner_chat_id", str(t))
with users.use(ids[2001]): watchlist.set_setting("scan_paused", "1")
for t in (1001, 1002, 2001): accounts.set_plan(ids[t], "paid", accounts.get(ids[t]).plan_until)      # the daily check is part of the paid plan (see test_plans.py)
ran = []
async def fake_run_scan(bot, chat_id, trigger="schedule"):
    ran.append((users.current(), chat_id, trigger))
    if users.current() == ids[1001]: raise RuntimeError("boom")
scanner.run_scan = fake_run_scan; config.SCAN_ENABLED = True
run(scanner.daily_scan(types.SimpleNamespace(bot=object())))
assert ran == [(ids[1001], 1001, "schedule"), (ids[1002], 1002, "schedule")], ran
accounts.set_status(ids[1002], "paused"); ran.clear()
run(scanner.daily_scan(types.SimpleNamespace(bot=object()))); assert [r[0] for r in ran] == [ids[1001]], "a paused account gets no scheduled work"
accounts.set_status(ids[1002], "active")
order = []
@accounts.for_each_user
async def job(): order.append(users.current())
run(job()); assert order == accounts.user_ids() and len(order) == 3
users.MODE = "single"; order.clear(); run(job()); assert order == ["owner"]; users.MODE = "cloud"
print("6 ok: scheduled jobs run once per active person (once, as the owner, in single mode); failures are isolated")

# ---- 7. the server's AI problem goes to the maintainer, never to users
sent = []
class Bot:
    async def send_message(self, chat, text): sent.append((chat, text))
app = types.SimpleNamespace(bot=Bot())
import service.config as sc
sc.OPENAI_API_KEY = "sk-x"; status._test_ai = lambda: "✕ the AI key was rejected. Run the setup page again to fix it."
config.ADMIN_TELEGRAM_ID = 0; run(status.startup_check(app)); assert sent == [], "no admin set: logged only, no user is told"
config.ADMIN_TELEGRAM_ID = 424242; run(status.startup_check(app)); assert [c for c, _ in sent] == [424242] and "rejected" in sent[0][1]
print("7 ok: a wrong AI key is reported to the maintainer only")

# ---- 8. /plan and /deleteaccount
async def cmd(fn, tg, query=None):
    u = update(tg, "/x", query=query); c = ctx() if query is None else cmd.c.setdefault(tg, ctx())
    await common.restricted(fn)(u, c); return u, c
cmd.c = {}
u, c = run(cmd(cloud.plan_command, 1001)); assert "Paid" in u.effective_message.sent[0] and "2027-01-01" in u.effective_message.sent[0]
u, c = run(cmd(cloud.delete_command, 1002)); assert "Delete your account?" in u.effective_message.sent[0]
# two accounts' data
for t in (1001, 1002):
    with users.use(ids[t]):
        db.upsert_application("Co" + str(t), "Intern", job_link=f"https://j/{t}"); watchlist.add_source("https://jobs.lever.co/acme")
        sk = _load_skeleton_raw(); sk["header"]["name"] = f"Person {t}"; _save_skeleton_raw(sk)
folder_b, folder_a = users.user_root(ids[1002]), users.user_root(ids[1001])
assert os.path.isdir(folder_b) and os.path.isdir(folder_a)
def tap(tg, data):
    q = Query(data); u = update(tg, query=q); c = cmd.c.setdefault(tg, ctx()); run(common.restricted(cloud.account_callback)(u, c)); return q
q = tap(1002, "acct:d2"); assert "expired" in q.edits[-1][0] and os.path.isdir(folder_b), "the second button alone does nothing"
q = tap(1002, "acct:no"); assert "kept" in q.edits[-1][0]
q = tap(1002, "acct:d1"); assert "Last chance" in q.edits[-1][0] and os.path.isdir(folder_b), "the first 'yes' deletes nothing"
cmd.c[1002].user_data["delete_step"] -= cloud.CONFIRM_SECONDS + 1
q = tap(1002, "acct:d2"); assert "expired" in q.edits[-1][0] and os.path.isdir(folder_b), "a stale second tap does nothing"
tap(1002, "acct:d1"); q = tap(1002, "acct:d2")
assert "deleted" in q.edits[-1][0] and not os.path.exists(folder_b), q.edits
assert os.path.isdir(folder_a) and accounts.by_telegram(1001).id == ids[1001], "only that person's data went"
with users.use(ids[1001]):
    assert [x["company"] for x in db.all_applications()] == ["Co1001"] and len(watchlist.list_sources()) == 1 and _load_skeleton_raw()["header"]["name"] == "Person 1001"
with sqlite3.connect(os.environ["DATABASE_PATH"]) as cx:
    assert cx.execute("SELECT COUNT(*) FROM applications WHERE user_id = ?", (ids[1002],)).fetchone()[0] == 0
    assert cx.execute("SELECT COUNT(*) FROM settings WHERE user_id = ?", (ids[1002],)).fetchone()[0] == 0
    row = cx.execute("SELECT status, telegram_id, name FROM accounts WHERE id = ?", (ids[1002],)).fetchone()
    assert row == ("deleted", None, ""), "a bare record stays, with no name or Telegram id"
assert accounts.by_telegram(1002) is None and ids[1002] not in accounts.user_ids()
u = update(1002, "hello"); run(handler(u, ctx())); assert "Send /start" in u.effective_message.sent[0], "after deleting, the bot doesn't know them"
c2 = ctx(); u = update(1002, "/start"); run(handler(u, c2)); fresh = accounts.by_telegram(1002)
assert fresh and fresh.id != ids[1002] and c2.user_data.get("new_account"), "they can sign up again, from scratch, with a new id"
try: accounts.delete_account("owner"); raise SystemExit("must refuse")
except ValueError: pass
print("8 ok: /plan; /deleteaccount asks twice, deletes only that person's rows and files, and they can start over")

# ---- 9. start-up registers the hosted commands only in cloud mode
os.environ["INTERNIFY_CLOUD_PREVIEW"] = "1"; os.environ["TELEGRAM_BOT_TOKEN"] = "123:abc"; config.TELEGRAM_BOT_TOKEN = "123:abc"
from bot import main
def commands(app): return {c for h in app.handlers.get(0, []) for c in getattr(h, "commands", [])}
cloud_cmds = commands(main.create_bot_app())
users.MODE = "single"; single_cmds = commands(main.create_bot_app())
assert {"plan", "deleteaccount", "start"} <= cloud_cmds and not ({"plan", "deleteaccount"} & single_cmds) and "start" in single_cmds
assert "/plan" in cloud.welcome_text("Sam", True) and "Welcome Sam" in cloud.welcome_text("Sam", True) and "Welcome" not in cloud.welcome_text("Sam", False)
users.MODE = "cloud"; os.environ.pop("INTERNIFY_CLOUD_PREVIEW"); config.ADMIN_TELEGRAM_ID = 0
try: main.create_bot_app(); raise SystemExit("still started without an admin")
except SystemExit as e: assert e.code == 1
config.ADMIN_TELEGRAM_ID = 424242; main.create_bot_app()      # complete settings: starts (only warnings)
users.MODE = "single"
print("9 ok: /plan and /deleteaccount exist only in cloud mode; cloud mode refuses to start without an admin and starts once set up")
print("\n✅ accounts test passed")
