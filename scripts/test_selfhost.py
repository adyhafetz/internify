"""Offline test of the self-hosting changes: the catch-up run after a computer that was off, the plain startup warning about
the AI key, and the Docker / compose / image-publishing files. It can't build or publish the image (that needs Docker and
GitHub); it checks that the files say what they should."""
from __future__ import annotations
import asyncio, os, re, sys, tempfile, time, types
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.abspath("."))
os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "t.db")
os.environ["OPENAI_API_KEY"] = "sk-test"
from bot import config, scanner, status
from bot.services import db, watchlist
from service import config as sc
db.init_db()

# ---- 1. was today's check missed? (08:00 Kuala Lumpur)
KL = ZoneInfo("Asia/Kuala_Lumpur")
ts = lambda *a: datetime(*a, tzinfo=KL).timestamp()
now = ts(2026, 11, 16, 9, 30)
assert scanner.missed_today(now, ts(2026, 11, 15, 8, 1), 8, 0, "Asia/Kuala_Lumpur"), "yesterday's check was the last: today's was missed"
assert not scanner.missed_today(now, ts(2026, 11, 16, 8, 2), 8, 0, "Asia/Kuala_Lumpur"), "today's check already ran"
assert not scanner.missed_today(ts(2026, 11, 16, 7, 0), ts(2026, 11, 15, 8, 1), 8, 0, "Asia/Kuala_Lumpur"), "not 08:00 yet: nothing is missed"
assert not scanner.missed_today(now, None, 8, 0, "Asia/Kuala_Lumpur"), "a copy that never ran a check waits for its first scheduled one"
assert scanner.missed_today(now, ts(2026, 11, 10, 8, 0), 8, 0, "Asia/Kuala_Lumpur"), "a laptop that was off for days catches up once"
assert scanner.missed_today(ts(2026, 11, 16, 23, 0), ts(2026, 11, 15, 22, 0), 8, 0, "Asia/Kuala_Lumpur")
print("1 ok: a missed daily check is detected (and never on a first run)")

# ---- 2. the catch-up runs the scan only when it should
ran = []
async def fake_run(bot, chat_id, trigger="schedule"): ran.append((chat_id, trigger))
scanner.run_scan = fake_run
config.SCAN_ENABLED, config.SCAN_TIME, config.SCAN_TIMEZONE = True, "00:00", "UTC"   # today's time has always passed
ctx = types.SimpleNamespace(bot=object())
run = asyncio.run
run(scanner.catch_up_scan(ctx)); assert not ran, "no chat yet, and no earlier check: nothing to catch up"
watchlist.set_setting("owner_chat_id", "42")
run(scanner.catch_up_scan(ctx)); assert not ran, "a copy that never ran a check waits for its schedule"
watchlist.set_setting("scan_last_at", str(time.time() - 3 * 86400))
run(scanner.catch_up_scan(ctx)); assert ran == [(42, "catch-up")], ran
watchlist.set_setting("scan_last_at", str(time.time())); ran.clear()
run(scanner.catch_up_scan(ctx)); assert not ran, "it already ran today"
watchlist.set_setting("scan_last_at", str(time.time() - 3 * 86400)); watchlist.set_setting("scan_paused", "1")
run(scanner.catch_up_scan(ctx)); assert not ran, "paused means paused"
watchlist.set_setting("scan_paused", "0"); config.SCAN_ENABLED = False
run(scanner.catch_up_scan(ctx)); assert not ran
config.SCAN_ENABLED = True
class JQ:
    def __init__(self): self.once, self.daily = [], []
    def run_daily(self, cb, time, name): self.daily.append(name)
    def run_once(self, cb, when, name): self.once.append((name, when))
jq = JQ(); scanner.schedule(types.SimpleNamespace(job_queue=jq))
assert jq.daily == ["daily-scan"] and jq.once == [("scan-catch-up", 90)], "the catch-up is registered a minute and a half after start"
print("2 ok: the catch-up runs once after a missed day, and not when paused, off, already done or never run")

# ---- 3. the startup warning: only when something is wrong, once per problem, in plain words
sent = []
class Bot:
    async def send_message(self, chat, text): sent.append(text)
app = types.SimpleNamespace(bot=Bot())
def with_result(result):
    status._test_ai = lambda: result
    run(status.startup_check(app))
sc.OPENAI_API_KEY = "sk-x"
with_result("✓ works"); assert not sent, "quiet when all is well"
with_result("✕ the AI key was rejected. Run the setup page again to fix it."); assert sent == ["⚠️ the AI key was rejected. Run the setup page again to fix it."], sent
with_result("✕ the AI key was rejected. Run the setup page again to fix it."); assert len(sent) == 1, "the same problem isn't repeated at every restart"
with_result("✓ works, but these models aren't available to your key: gpt-9"); assert len(sent) == 2 and "gpt-9" in sent[-1] and "setup page" in sent[-1]
with_result("✕ couldn't reach the AI service (timeout)"); assert len(sent) == 2, "no network is not a wrong key"
sc.OPENAI_API_KEY = ""; run(status.startup_check(app)); assert "key is missing" in sent[-1]
watchlist.set_setting("owner_chat_id", ""); sent.clear(); watchlist.set_setting("startup_warned", ""); run(status.startup_check(app)); assert not sent, "no chat yet: only logged"
print("3 ok: startup check warns once, in plain words, only about a wrong or missing AI key or unavailable models")

# ---- 4. Dockerfile, compose and the image workflow
docker = open("Dockerfile").read()
assert "uname -m" in docker and "aarch64" in docker and "x86_64" in docker and "x86_64-unknown-linux-musl.tar.gz" not in docker, "Tectonic is chosen for the machine"
assert "${arch}-unknown-linux-musl" in docker and "exit 1" in docker, "an unsupported machine fails with a clear message"
compose = open("docker-compose.yml").read()
assert "ghcr.io/adyhafetz/internify:latest" in compose and "INTERNIFY_IMAGE" in compose and re.search(r"image:.*\n\s+build: \.", compose), "image first, build as the fallback"
wf = open(".github/workflows/image.yml").read()
assert "linux/amd64,linux/arm64" in wf and "packages: write" in wf and 'tags: ["v*"]' in wf and "docker/build-push-action" in wf and "secrets.GITHUB_TOKEN" in wf
try:
    import yaml
    parsed = yaml.safe_load(wf); assert "publish" in parsed["jobs"]; yaml.safe_load(compose)
except ImportError:
    pass
print("4 ok: Dockerfile picks Tectonic for Intel/AMD or ARM; compose prefers the prebuilt image; the workflow publishes both")
print("\n✅ self-host test passed")
