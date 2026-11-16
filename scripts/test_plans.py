"""Offline test of plans and limits in the hosted version: what each plan includes, reserving and giving back a resume (never
over the limit, even with many at once), the daily check being part of the paid plan, usage counting, the server's monthly AI
ceiling, /plan, the plan-ending reminders and the maintainer's commands. Single mode has no limits at all. Telegram and the AI
are faked."""
from __future__ import annotations
import asyncio, datetime as dt, os, sys, tempfile, threading, types

sys.path.insert(0, os.path.abspath("."))
tmp = tempfile.mkdtemp()
os.environ.update(OPENAI_API_KEY="sk-test", DATABASE_PATH=os.path.join(tmp, "t.db"), OUTPUT_DIR=os.path.join(tmp, "out"),
                  FACT_STORE_PATH=os.path.join(tmp, "out", "data", "facts.json"), SKELETON_PATH=os.path.join(tmp, "out", "data", "resume_skeleton.json"),
                  EMBEDDING_CACHE_PATH=os.path.join(tmp, "out", "data", "embeddings_cache.json"), TELEGRAM_BOT_TOKEN="123:abc")
for k in ("MODE", "OUTCOMES_PATH", "TELEGRAM_ALLOWED_USER_IDS", "INTERNIFY_CLOUD_PREVIEW"): os.environ.pop(k, None)

from bot import admin, cloud, config, jobflow, scanner, status
from bot.services import accounts, db, plans, watchlist
from service import clients, metering, users

db.init_db()
run = asyncio.run
today = dt.date.today()
iso = lambda d: d.isoformat()

# ---- 1. what each plan includes
users.MODE = "cloud"
mk = lambda tg, name="P": accounts.create(tg, name)
free, paid, unl = mk(1), mk(2), mk(3)
accounts.set_plan(paid.id, "paid", iso(today + dt.timedelta(days=40))); accounts.set_plan(unl.id, "unlimited")
L = lambda a: plans.limits_for(accounts.get(a.id))
assert (L(free).plan, L(free).resumes, L(free).daily_check) == ("free", 3, False)
assert (L(paid).plan, L(paid).resumes, L(paid).daily_check, L(paid).scan_max) == ("paid", 30, True, 3)
assert (L(unl).plan, L(unl).resumes, L(unl).daily_check) == ("unlimited", None, True)
lapsed = mk(4); accounts.set_plan(lapsed.id, "paid", iso(today - dt.timedelta(days=1)))
assert L(lapsed).plan == "free" and L(lapsed).lapsed and L(lapsed).resumes == 3 and not L(lapsed).daily_check, "a paid plan whose end date has passed counts as free"
accounts.set_plan(lapsed.id, "paid", iso(today)); assert not L(lapsed).lapsed, "it lasts through its end date"
accounts.set_plan(lapsed.id, "mystery", ""); assert L(lapsed).plan == "free", "an unknown plan name is treated as free, never as unlimited"
assert plans.next_reset(dt.date(2026, 12, 31)) == dt.date(2027, 1, 1) and plans.next_reset(dt.date(2026, 11, 16)) == dt.date(2026, 12, 1)
assert plans.month_key(dt.date(2026, 3, 9)) == "2026-03"
print("1 ok: free 3 / paid 30 with the daily check / unlimited; a lapsed paid plan is free; resets on the 1st")

# ---- 2. single mode: no limits, nothing counted
users.MODE = "single"
d = plans.reserve_resume(); assert d.ok and not d.reserved
assert plans.allows_daily_check() and plans.scan_budget() is None and plans.resumes_left() is None and plans.short_summary() == "" and plans.current_limits() is None
plans.refund_resume(); plans.record("ai"); assert plans.server_total("ai") == 0
print("2 ok: single mode has no limits and counts nothing")

# ---- 3. reserving and giving back
users.MODE = "cloud"
with users.use(free.id):
    assert [plans.reserve_resume().ok for _ in range(3)] == [True, True, True] and plans.used("resume") == 3 and plans.resumes_left() == 0
    r = plans.reserve_resume()
    assert not r.ok and not r.reserved and "all 3 resumes of your Free plan" in r.message and plans.next_reset().strftime("%d %b") in r.message and config.PAY_INSTRUCTIONS in r.message
    assert plans.used("resume") == 3, "a refusal costs nothing"
    plans.refund_resume(); assert plans.used("resume") == 2 and plans.reserve_resume().ok
    for _ in range(10): plans.refund_resume()
    assert plans.used("resume") == 0, "refunds never go below zero"
    ok = []
    def grab():
        users._current.set(free.id)      # a new thread starts with no user: set it, as a handler's work would have
        ok.append(plans.reserve_resume().ok)
    threads = [threading.Thread(target=grab) for _ in range(12)]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert sum(ok) == 3 and plans.used("resume") == 3, "twelve at once: exactly the last three get through"
with users.use(paid.id):
    assert all(plans.reserve_resume().ok for _ in range(30)) and not plans.reserve_resume().ok and plans.used("resume") == 30
with users.use(unl.id):
    assert all(plans.reserve_resume().ok for _ in range(100)) and plans.used("resume") == 100 and plans.resumes_left() is None, "unlimited is counted but never stopped"
with users.use(lapsed.id):
    accounts.set_plan(lapsed.id, "paid", iso(today - dt.timedelta(days=2)))
    assert all(plans.reserve_resume().ok for _ in range(3))
    r = plans.reserve_resume(); assert not r.ok and "ended on" in r.message and "back on the free plan" in r.message
with db._get_connection() as c:   # last month's use doesn't count this month
    c.execute("INSERT INTO usage (user_id, month, kind, n) VALUES (?, '2000-01', 'resume', 3)", (mk(5).id,)); c.commit()
with users.use(mk(5).id): assert plans.used("resume") == 0 and plans.reserve_resume().ok
print("3 ok: reserve and refund never exceed the limit (even 12 at once), refusals say why and when it resets; unlimited is only counted")

# ---- 4. the daily check belongs to the paid plan, and writes no more than what is left
with users.use(free.id): assert not plans.allows_daily_check() and "paid plan" in plans.daily_check_refusal() and config.PAY_INSTRUCTIONS in plans.daily_check_refusal()
with users.use(paid.id):
    assert plans.allows_daily_check() and plans.daily_check_refusal() == ""
    with db._get_connection() as c: c.execute("UPDATE usage SET n = 29 WHERE user_id = ? AND kind = 'resume'", (paid.id,)); c.commit()
    assert plans.scan_budget() == 1, "a check writes the plan's 3 at most, and no more than are left"
    with db._get_connection() as c: c.execute("UPDATE usage SET n = 5 WHERE user_id = ? AND kind = 'resume'", (paid.id,)); c.commit()
    assert plans.scan_budget() == 3
with users.use(unl.id): assert plans.scan_budget() == 3 and plans.resumes_left() is None
print("4 ok: no daily check on the free plan; a check never writes more than the plan's maximum or what is left")

# ---- 5. counting AI and scraping calls
plans.install(); assert metering.sink is plans.record
before = plans.server_total("ai")
with users.use(paid.id):
    a0 = plans.used("ai")
    fake = clients._counted(lambda x: x * 2, "ai"); assert fake(4) == 8 and plans.used("ai") == a0 + 1
    metering.count("scrape"); assert plans.used("scrape") == 1
    asyncio.run(asyncio.to_thread(metering.count, "ai")); assert plans.used("ai") == a0 + 2, "a call made inside a worker thread is counted for the right person"
assert hasattr(clients.instructor_client.chat.completions.create, "__wrapped__") and hasattr(clients.openai_client.embeddings.create, "__wrapped__"), "the real AI clients are counted"
metering.count("ai"); assert plans.server_total("ai") == before + 2, "a call made for the server itself (no person) is not charged to anyone"
metering.sink = lambda kind: 1 / 0; metering.count("ai")       # a broken counter never breaks the work
users.MODE = "single"; metering.sink = plans.record; metering.count("ai"); users.MODE = "cloud"
assert plans.server_total("ai") == before + 2
print("5 ok: AI and scraping calls are counted per person (also inside worker threads); counting never breaks the work")

# ---- 6. the server's monthly ceiling
with db._get_connection() as c: c.execute("DELETE FROM usage WHERE kind = 'ai'"); c.commit()
def set_ai(n):
    with db._get_connection() as c: c.execute("DELETE FROM usage WHERE kind = 'ai'"); c.execute("INSERT INTO usage (user_id, month, kind, n) VALUES (?, ?, 'ai', ?)", ("serverx", plans.month_key(), n)); c.commit()
with users.use(mk(6, "Fresh").id): pass
f2, p2 = mk(6), mk(7); accounts.set_plan(p2.id, "paid", iso(today + dt.timedelta(days=5)))
config.AI_MONTHLY_BUDGET_CALLS = 0; set_ai(10**6); assert plans.capacity() == "", "0 means no ceiling"
config.AI_MONTHLY_BUDGET_CALLS = 100
set_ai(89); assert plans.capacity() == ""
with users.use(f2.id): assert plans.reserve_resume().ok
set_ai(90); assert plans.capacity() == "free"
with users.use(f2.id):
    used_before = plans.used("resume"); r = plans.reserve_resume()
    assert not r.ok and "very busy" in r.message and plans.used("resume") == used_before, "at 90% free-plan resumes wait (and nothing is taken)"
with users.use(p2.id): assert plans.reserve_resume().ok, "paid people still go ahead at 90%"
set_ai(100); assert plans.capacity() == "all"
with users.use(p2.id): assert not plans.reserve_resume().ok
with users.use(unl.id): assert plans.reserve_resume().ok, "unlimited always goes ahead"
config.AI_MONTHLY_BUDGET_CALLS = 0
print("6 ok: the server's monthly ceiling: free waits at 90%, everyone but unlimited at 100%")

# ---- 7. tailor_job: reserved before the work, given back when it fails
class Reporter:
    def __init__(self): self.failed = []
    async def fail(self, t): self.failed.append(t)
inner_calls = []
async def tailor(ok=True, raise_=False, **kw):
    async def inner(*a, **k):
        inner_calls.append(1)
        if raise_: raise RuntimeError("crash")
        return jobflow.JobOutcome(ok=ok)
    jobflow._tailor_job = inner
    rep = Reporter()
    out = await jobflow.tailor_job("https://j/1", rep, bot=None, chat_id=1, loop=None, interactive=kw.get("interactive", True))
    return out, rep
fresh = mk(8, "T");
with users.use(fresh.id):
    out, rep = run(tailor(ok=True)); assert out.ok and plans.used("resume") == 1
    out, rep = run(tailor(ok=False)); assert not out.ok and plans.used("resume") == 1, "a failed run gives its resume back"
    try: run(tailor(raise_=True)); raise SystemExit("should re-raise")
    except RuntimeError: pass
    assert plans.used("resume") == 1, "so does a crash"
    run(tailor()); run(tailor()); assert plans.used("resume") == 3
    inner_calls.clear()
    out, rep = run(tailor()); assert not inner_calls and not out.ok and "all 3 resumes" in rep.failed[0] and plans.used("resume") == 3, "at the limit: told straight away, no work started"
    out, rep = run(tailor(interactive=False)); assert not inner_calls and out.skipped and not rep.failed, "the daily check skips quietly"
users.MODE = "single"; inner_calls.clear()
out, rep = run(tailor()); assert inner_calls and out.ok, "single mode: no limits"
users.MODE = "cloud"
print("7 ok: a resume is reserved before any work, refused at the limit with a plain message, and returned if the work fails")

# ---- 8. the scheduled and manual daily check
sent = []
async def fake_run_scan(bot, chat_id, trigger="schedule"): sent.append((users.current(), trigger))
real_run_scan = scanner.run_scan; scanner.run_scan = fake_run_scan; config.SCAN_ENABLED = True
for a in (free, paid):
    with users.use(a.id): watchlist.set_setting("owner_chat_id", str(a.telegram_id))
with db._get_connection() as c: c.execute("UPDATE usage SET n = 0 WHERE user_id = ? AND kind = 'resume'", (paid.id,)); c.commit()
run(scanner.daily_scan(types.SimpleNamespace(bot=object())))
assert [u for u, _ in sent] == [paid.id], sent
assert (free.id, "schedule") not in sent, "free plan: no scheduled check, and no message about it"
scanner.run_scan = real_run_scan
scanner.profile_is_sample = lambda: False
summ = scanner.ScanSummary()
with users.use(free.id): run(scanner._scan(types.SimpleNamespace(), 1, asyncio.new_event_loop(), summ))
assert "paid plan" in summ.note and config.PAY_INSTRUCTIONS in summ.note, "tapping Check now on the free plan explains it"
with db._get_connection() as c: c.execute("UPDATE usage SET n = 30 WHERE user_id = ? AND kind = 'resume'", (paid.id,)); c.commit()
summ = scanner.ScanSummary()
with users.use(paid.id): run(scanner._scan(types.SimpleNamespace(), 1, asyncio.new_event_loop(), summ))
assert "used all of this month" in summ.note and plans.next_reset().strftime("%d %b") in summ.note, summ.note
print("8 ok: the daily check runs for paid people only; Check now explains the free plan and a used-up month")

# ---- 9. /plan and ⚙️ Status
with db._get_connection() as c: c.execute("UPDATE usage SET n = 12 WHERE user_id = ? AND kind = 'resume'", (paid.id,)); c.commit()
with users.use(paid.id):
    text = cloud.plan_text(accounts.get(paid.id))
    assert "Paid" in text and "12 of 30" in text and "included" in text and "Renew" in text and plans.next_reset().strftime("%d %b") in text and "AI calls" in text, text
    assert "Paid plan · 12 of 30" in plans.short_summary()
with users.use(free.id):
    text = cloud.plan_text(accounts.get(free.id))
    assert "Free" in text and "of 3" in text and "not included" in text and "Upgrade" in text and config.PAY_INSTRUCTIONS in text
with users.use(unl.id):
    text = cloud.plan_text(accounts.get(unl.id)); assert "Unlimited" in text and "no limit" in text and "Upgrade" not in text and "Renew" not in text
with users.use(lapsed.id):
    accounts.set_plan(lapsed.id, "paid", iso(today - dt.timedelta(days=3))); text = cloud.plan_text(accounts.get(lapsed.id))
    assert "ended on" in text and "Free" in text and "Renew" in text
with users.use(paid.id): assert "Plan: Paid plan" in status.screen()[0] or "Plan: Paid plan" in str(status.screen())
users.MODE = "single"; assert "Plan:" not in str(status.screen()); users.MODE = "cloud"
print("9 ok: /plan and Status show plan, use this month, reset date and how to upgrade or renew")

# ---- 10. reminders before a plan ends
msgs = []
class Bot:
    async def send_message(self, chat, text): msgs.append((chat, text))
ctx = types.SimpleNamespace(bot=Bot())
rem = mk(9, "Rem");
with users.use(rem.id): watchlist.set_setting("owner_chat_id", "909")
accounts.set_plan(rem.id, "paid", iso(today + dt.timedelta(days=30)))
msgs.clear(); run(cloud.daily_notices(ctx)); assert not [m for m in msgs if m[0] == 909], "no reminder 30 days out"
accounts.set_plan(rem.id, "paid", iso(today + dt.timedelta(days=2)))
msgs.clear(); run(cloud.daily_notices(ctx)); mine = [m for m in msgs if m[0] == 909]; assert len(mine) == 1 and "ends in 2 days" in mine[0][1] and config.PAY_INSTRUCTIONS in mine[0][1]
msgs.clear(); run(cloud.daily_notices(ctx)); assert not [m for m in msgs if m[0] == 909], "once per end date"
accounts.set_plan(rem.id, "paid", iso(today + dt.timedelta(days=1)))
msgs.clear(); run(cloud.daily_notices(ctx)); assert len([m for m in msgs if m[0] == 909]) == 1, "a renewal with a new end date reminds again"
accounts.set_plan(rem.id, "paid", iso(today - dt.timedelta(days=1)))
msgs.clear(); run(cloud.daily_notices(ctx)); mine = [m for m in msgs if m[0] == 909]; assert len(mine) == 1 and "ended on" in mine[0][1] and "Everything you saved is still here" in mine[0][1]
msgs.clear(); run(cloud.daily_notices(ctx)); assert not [m for m in msgs if m[0] == 909]
assert all(m[0] != free.telegram_id for m in msgs)
print("10 ok: a reminder a few days before the plan ends, and one when it has ended, each once per end date")

# ---- 11. the maintainer's commands
config.ADMIN_TELEGRAM_ID = 777
class Msg:
    def __init__(self): self.sent = []
    async def reply_text(self, t, **kw): self.sent.append(t)
def cmd(fn, tg, *args):
    m = Msg(); u = types.SimpleNamespace(effective_user=types.SimpleNamespace(id=tg), effective_message=m)
    run(fn(u, types.SimpleNamespace(args=list(args), bot=Bot()))); return m.sent
assert cmd(admin.admin_command, 5) == [] and cmd(admin.setplan_command, 5, free.id, "paid", "2099-01-01") == [] and accounts.get(free.id).plan == "free", "only the maintainer is answered"
config.ADMIN_TELEGRAM_ID = 0; assert cmd(admin.admin_command, 0) == [], "no maintainer set: nobody is the maintainer"; config.ADMIN_TELEGRAM_ID = 777
assert admin.find(free.id).id == free.id and admin.find("1").id == free.id and admin.find("nope") is None and admin.find("") is None
bad = lambda *a: admin.apply_plan(*a)[:2]
assert bad("zzz", "paid", "2099-01-01")[0] is False and bad(free.id, "gold", "")[0] is False and bad(free.id, "paid", "")[0] is False
assert bad(free.id, "paid", "next week")[0] is False and bad(free.id, "free", "2099-01-01")[0] is False and bad(free.id, "paid", iso(today - dt.timedelta(days=1)))[0] is False
assert accounts.get(free.id).plan == "free", "a rejected command changes nothing"
with users.use(free.id): watchlist.set_setting("owner_chat_id", "101")
told = []
class Bot2:
    async def send_message(self, chat, text): told.append((chat, text))
m = Msg(); u = types.SimpleNamespace(effective_user=types.SimpleNamespace(id=777), effective_message=m)
run(admin.setplan_command(u, types.SimpleNamespace(args=["1", "paid", iso(today + dt.timedelta(days=30))], bot=Bot2())))
assert accounts.get(free.id).plan == "paid" and "now on the paid plan" in m.sent[0] and told and told[0][0] == 101 and "Paid" in told[0][1] and "/plan" in told[0][1], (m.sent, told)
run(admin.setplan_command(u, types.SimpleNamespace(args=["1", "unlimited"], bot=Bot2()))); assert accounts.get(free.id).plan == "unlimited" and accounts.get(free.id).plan_until == ""
assert "Use:" in cmd(admin.setplan_command, 777, "only-one")[0]
accounts.set_plan(free.id, "free", "")
assert "paused" in cmd(admin.pause_command, 777, free.id, "chargeback")[0] and accounts.get(free.id).status == "paused" and accounts.get(free.id).note == "chargeback"
assert "active again" in cmd(admin.resume_command, 777, str(free.telegram_id))[0] and accounts.get(free.id).status == "active"
overview = cmd(admin.admin_command, 777)[0]
assert "people" in overview and "paid" in overview and "AI calls" in overview and rem.id in overview, overview   # the newest eight are listed
assert free.id in cmd(admin.users_command, 777, "P")[0] and cmd(admin.users_command, 777, "nobody-called-this") == ["Nobody found."]
print("11 ok: only the maintainer is answered; plans are validated, applied and the person is told; pause, resume, find, overview")

# ---- 12. deleting an account also deletes its usage
with users.use(rem.id): plans.reserve_resume(); assert plans.used("resume") == 1
accounts.delete_account(rem.id)
with db._get_connection() as c: assert c.execute("SELECT COUNT(*) FROM usage WHERE user_id = ?", (rem.id,)).fetchone()[0] == 0
print("12 ok: deleting an account removes its usage too")

# ---- 13. start-up wiring
os.environ["INTERNIFY_CLOUD_PREVIEW"] = "1"; config.TELEGRAM_BOT_TOKEN = "123:abc"
from bot import main
cmds = lambda app: {c for h in app.handlers.get(0, []) for c in getattr(h, "commands", [])}
cloud_cmds = cmds(main.create_bot_app())
users.MODE = "single"; single_cmds = cmds(main.create_bot_app())
assert {"admin", "users", "setplan", "pauseuser", "resumeuser", "plan"} <= cloud_cmds and not ({"admin", "setplan", "plan"} & single_cmds)
metering.sink = None; users.MODE = "cloud"
class App:
    bot = types.SimpleNamespace(set_my_commands=lambda *a, **k: asyncio.sleep(0), set_my_short_description=lambda *a, **k: asyncio.sleep(0), set_my_description=lambda *a, **k: asyncio.sleep(0))
    class job_queue:
        daily, once = [], []
        @staticmethod
        def run_daily(cb, time=None, days=None, name=None): App.job_queue.daily.append(name)
        @staticmethod
        def run_once(cb, when=None, name=None): App.job_queue.once.append(name)
        @staticmethod
        def get_jobs_by_name(n): return []
async def boot(): await main.post_init(App)
status._test_ai = lambda: "✓ works"; run(boot())
assert metering.sink is plans.record and "plan-notices" in App.job_queue.daily, "the hosted start-up counts usage and schedules the plan reminders"
users.MODE = "single"; metering.sink = None; os.environ.pop("INTERNIFY_CLOUD_PREVIEW")
print("13 ok: hosted start-up registers the maintainer's commands, counts usage and schedules reminders; single mode does none of it")
print("\n✅ plans test passed")
