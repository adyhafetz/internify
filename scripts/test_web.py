"""Offline test of the web side: the marketing pages (search tags, structured data, one clear call to action, no filler words),
robots.txt and the sitemap, and in the hosted version the one-time sign-in link, per-person dashboards, events and PDFs that
nobody else can open, the maintainer's admin page, and the Telegram commands that hand out the links. Telegram is faked."""
from __future__ import annotations
import asyncio, contextvars, datetime as dt, json, os, re, sqlite3, sys, tempfile, types

sys.path.insert(0, os.path.abspath("."))
tmp = tempfile.mkdtemp()
os.environ.update(OPENAI_API_KEY="sk-test", DATABASE_PATH=os.path.join(tmp, "t.db"), OUTPUT_DIR=os.path.join(tmp, "out"),
                  FACT_STORE_PATH=os.path.join(tmp, "out", "data", "facts.json"), SKELETON_PATH=os.path.join(tmp, "out", "data", "resume_skeleton.json"),
                  EMBEDDING_CACHE_PATH=os.path.join(tmp, "out", "data", "embeddings_cache.json"), TELEGRAM_BOT_TOKEN="123:abc")
for k in ("MODE", "OUTCOMES_PATH", "TELEGRAM_ALLOWED_USER_IDS", "INTERNIFY_CLOUD_PREVIEW", "MARKETING_SITE", "PUBLIC_BOT_USERNAME"): os.environ.pop(k, None)

from fastapi.testclient import TestClient
from bot import admin, cloud, config, main
from bot.dashboard import server, site
from bot.services import accounts, db, plans, websession
from service import events, users

db.init_db()
run = asyncio.run
c = TestClient(server.app, follow_redirects=False)
def anon(): return TestClient(server.app, follow_redirects=False)      # a browser with no cookies (a client keeps the cookies it is given)
BASE = "https://internify.example.dev"
BANNED = ("seamless", "revolutionary", "powerful", "cutting-edge", "supercharge", "game-chang", "unlock", "leverage", "world-class", "state-of-the-art",
          "effortless", "best-in-class", "next-level", "delve", "elevate", "empower", "robust", "innovative", "synergy", "holistic", "streamline")

def tag(html, pattern): return re.findall(pattern, html, re.S)
def meta(html, name):
    m = re.search(rf'<meta (?:name|property)="{re.escape(name)}" content="([^"]*)"', html); return m.group(1) if m else None
def ld(html): return [json.loads(b) for b in tag(html, r'<script type="application/ld\+json">(.*?)</script>')]

# ---------------------------------------------------------------- the marketing pages
config.MARKETING_SITE, config.BOT_USERNAME, config.PUBLIC_URL = True, "InternifyBot", BASE
config.FREE_RESUMES, config.PAID_RESUMES, config.PAID_PRICE, config.PAID_SCAN_MAX = 3, 30, "RM9.90", 3
pages = {p: c.get(p).text for p in site.INDEXED}
assert all(c.get(p).status_code == 200 for p in site.INDEXED)
titles = set()
for path, html in pages.items():
    assert 'lang="en"' in html and '<meta name="viewport"' in html and "{{" not in html and "{%" not in html, path
    assert len(tag(html, r"<h1[ >]")) == 1, f"{path}: exactly one h1"
    title = tag(html, r"<title>(.*?)</title>")[0]; desc = meta(html, "description")
    assert 15 <= len(title) <= 65 and title not in titles, (path, title); titles.add(title)
    assert 70 <= len(desc) <= 160, (path, len(desc), desc)
    assert f'<link rel="canonical" href="{BASE}{path}">' in html, path
    for k in ("og:title", "og:description", "og:url", "og:image", "og:type", "og:locale", "twitter:card", "twitter:image"):
        assert meta(html, k), (path, k)
    assert meta(html, "og:url") == BASE + path and meta(html, "og:image") == BASE + "/assets/og-image.png" and meta(html, "twitter:card") == "summary_large_image"
    assert meta(html, "robots").startswith("index"), path
    assert all("alt=" in img for img in tag(html, r"<img [^>]*>")), f"{path}: every image has alt text"
    assert not [h for h in tag(html, r'href="([^"]*)"') if h in ("", "#")], f"{path}: no empty links"
    assert "click here" not in html.lower() and "read more" not in html.lower(), f"{path}: link text says where it goes"
for path in ("/", "/how-it-works", "/pricing"):
    low = pages[path].lower()
    assert not [w for w in BANNED if w in low], (path, [w for w in BANNED if w in low])
assert os.path.exists(site.OG_IMAGE_FILE) and open(site.OG_IMAGE_FILE, "rb").read(24)[16:24] == (1200).to_bytes(4, "big") + (630).to_bytes(4, "big"), "link-preview image is 1200x630"
print("1 ok: five pages with one h1, unique titles, descriptions, canonical, Open Graph, alt text, no empty links, no filler words")

# structured data is valid JSON and matches what the page says
home = pages["/"]; blocks = ld(home)
kinds = {b["@type"] for b in blocks}
assert {"SoftwareApplication", "WebSite", "FAQPage"} <= kinds
app = next(b for b in blocks if b["@type"] == "SoftwareApplication")
assert app["operatingSystem"] == "Telegram" and {o["priceCurrency"] for o in app["offers"]} == {"MYR"} and [o["price"] for o in app["offers"]] == ["0", "9.90"]
faq = next(b for b in blocks if b["@type"] == "FAQPage")
assert len(faq["mainEntity"]) == len(tag(home, r"<details>")) == 8 and all(q["acceptedAnswer"]["text"] for q in faq["mainEntity"])
assert {b["@type"] for b in ld(pages["/pricing"])} == {"SoftwareApplication", "FAQPage"} and not ld(pages["/privacy"])
print("2 ok: structured data (software with MYR prices, website, FAQ) is valid and matches the page")

# the call to action: one clear main action everywhere, same destination, verb first
CTA = "https://t.me/InternifyBot?start=web"; PAID = "https://t.me/InternifyBot?start=paid"
for path in ("/", "/how-it-works", "/pricing"):
    ctas = tag(pages[path], r'<a class="btn[^"]*" href="([^"]+)"[^>]*>(Start free on Telegram)</a>')
    assert ctas and {u for u, _ in ctas} == {CTA}, (path, ctas)
assert tag(home, r'<a class="btn block" href="([^"]+)"[^>]*>Get Paid on Telegram</a>') == [PAID], "the paid plan's button opens the bot with a paid hint"
assert 'class="btn small" href="' + CTA in home, "the top bar always carries the main action"
hero = home[home.index('class="hero"'):home.index('id="how"')]
assert len(tag(hero, r'class="btn"')) == 1 and "See how it works" in hero and "No card needed" in hero, "one primary button in the hero, one quiet secondary"
assert "Self-host from GitHub" in home and config.PROJECT_URL in home
config.BOT_USERNAME = ""
nobot = c.get("/").text
assert "t.me" not in nobot and "Self-host from GitHub" in nobot and "Start free on Telegram" not in nobot, "with no bot address there is no dead button"
config.BOT_USERNAME = "InternifyBot"
print("3 ok: one clear call to action (verb first, same bot link, paid hint), no dead button when the bot's address is unknown")

# everything shown comes from the real settings
config.FREE_RESUMES, config.PAID_RESUMES, config.PAID_PRICE = 5, 40, "RM12.50"
changed = c.get("/pricing").text
assert "5 resumes a month" in changed and "40 resumes a month" in changed and "RM12.50" in changed and "RM9.90" not in changed
assert [o["price"] for o in next(b for b in ld(changed) if b["@type"] == "SoftwareApplication")["offers"]] == ["0", "12.50"]
config.FREE_RESUMES, config.PAID_RESUMES, config.PAID_PRICE = 3, 30, "RM9.90"
print("4 ok: plans and prices on the page (and in the structured data) follow the settings")

# internal links all work
links = set()
for html in pages.values(): links |= {h for h in tag(html, r'href="(/[^"#]*)"') if not h.startswith("//")}
assert {"/how-it-works", "/pricing", "/privacy", "/terms", "/"} <= links
for l in sorted(links): assert c.get(l).status_code == 200, l
print("5 ok: every internal link on the public pages opens")

# robots.txt and the sitemap
robots = c.get("/robots.txt").text
assert "Disallow: /u/" in robots and "Disallow: /admin" in robots and "Disallow: /auth/" in robots and f"Sitemap: {BASE}/sitemap.xml" in robots and "Allow: /" in robots
sm = c.get("/sitemap.xml"); assert sm.headers["content-type"].startswith("application/xml")
locs = tag(sm.text, r"<loc>(.*?)</loc>")
assert locs == [BASE + p for p in site.INDEXED] and all(re.search(r"<lastmod>\d{4}-\d\d-\d\d</lastmod>", sm.text) for _ in locs)
config.PUBLIC_URL = ""
assert "Sitemap:" not in c.get("/robots.txt").text and "<loc>" not in c.get("/sitemap.xml").text and 'rel="canonical"' not in c.get("/").text, "no made-up addresses without PUBLIC_URL"
config.PUBLIC_URL = BASE
print("6 ok: robots.txt keeps private paths out and points to the sitemap; the sitemap lists the five pages with dates")

# contact details are escaped; the policy pages follow the mode
config.CONTACT_EMAIL = 'a"<script>x</script>@x.com'
assert "<script>x</script>" not in c.get("/privacy").text and "&lt;script&gt;" in c.get("/privacy").text
config.CONTACT_EMAIL = "help@example.com"; assert 'mailto:help@example.com' in c.get("/terms").text
priv, terms = pages["/privacy"], pages["/terms"]
for must in ("Limited Use", "Google API Services User Data Policy", "9. The hosted service", "/deleteaccount", "Personal Data Protection Act 2010", "six months", "hash"):
    assert must in priv, must
for must in ("Plans and limits", "3 resumes a month", "30 resumes a month", "RM9.90", "No promise of results", "/deleteaccount"):
    assert must in terms, must
config.MARKETING_SITE = False
personal, p2, t2 = c.get("/").text, c.get("/privacy").text, c.get("/terms").text
assert "run by its owner for their own use" in personal and "hosted service" not in p2 and "Plans and limits" not in t2, "a normal copy keeps its personal wording"
assert c.get("/pricing").status_code == 404 and c.get("/how-it-works").status_code == 404 and "<loc>" not in c.get("/sitemap.xml").text
assert "hosted Internify service" not in p2 and "operated by the person who owns this domain" in p2
config.MARKETING_SITE = True
print("7 ok: contact details are escaped; hosted sections appear only for the hosted/marketing site; a normal copy is unchanged")

# ---------------------------------------------------------------- the hosted version: signing in
users.MODE = "cloud"
A, B = accounts.create(1001, "Alice"), accounts.create(1002, "Bob")
for p in list(site.INDEXED) + ["/healthz", "/robots.txt", "/sitemap.xml", "/dashboard", "/login", "/assets/brand/logo-mark.svg", "/site/style.css"]:
    assert c.get(p).status_code == 200, p
for p in ("/live", "/api/runs", "/api/agents", "/api/stream", "/pdf/x.pdf", "/admin", "/something", "/u/" + A.id + "/../live"):
    assert c.get(p).status_code in (401, 404), p
assert c.post("/api/simulate").status_code == 404, "the scripted demo is not reachable in the hosted version"
r = c.get(f"/u/{A.id}/live"); assert r.status_code == 401 and "/dashboard" in r.text and 'name="robots" content="noindex' in r.text
assert c.get(f"/u/{A.id}/api/runs").status_code == 401 and c.get(f"/u/{A.id}/api/runs").json()["detail"].startswith("Sign in first")
assert "Sign in from Telegram" in c.get("/dashboard").text and "t.me/InternifyBot" in c.get("/dashboard").text
assert c.get("/admin").status_code == 404, "the admin page isn't even acknowledged to others"
print("8 ok: hosted: public pages open; everything private says 'sign in from Telegram' (or isn't there)")

tok = websession.new_login(A.id)
r = c.get(f"/auth/{tok}")
assert r.status_code == 200 and f'action="/auth/{tok}"' in r.text and "Open my dashboard" in r.text and "set-cookie" not in r.headers and 'noindex' in r.text
assert r.headers["referrer-policy"] == "no-referrer"
assert websession.peek(tok), "a link preview (just fetching the page) does not use the token up"
c.get(f"/auth/{tok}"); c.get(f"/auth/{tok}"); assert websession.peek(tok)
r = anon().post(f"/auth/{tok}", headers={"x-forwarded-proto": "https"})
assert r.status_code == 303 and r.headers["location"] == f"/u/{A.id}/live"
cookie = r.headers["set-cookie"]; assert "HttpOnly" in cookie and "Secure" in cookie and "samesite=lax" in cookie.lower() and tok not in cookie
raw_cookie = cookie.split("=", 1)[1].split(";")[0]
assert c.post(f"/auth/{tok}").status_code == 400 and "expired" in c.post(f"/auth/{tok}").text, "a link works once"
assert c.get(f"/auth/{tok}").status_code == 400 and c.get("/auth/not-a-token").status_code == 400
old = websession.new_login(A.id, now=1000.0); assert c.get(f"/auth/{old}").status_code == 400 and websession.sign_in(old) is None, "a link expires after 15 minutes"
with sqlite3.connect(os.environ["DATABASE_PATH"]) as cx:
    stored = " ".join(str(v) for t in ("web_tokens", "web_sessions") for row in cx.execute(f"SELECT * FROM {t}") for v in row)
assert tok not in stored and raw_cookie not in stored, "only hashes are stored"
print("9 ok: a link shows a button (previews don't use it up), works once for 15 minutes, sets a safe cookie, and only hashes are stored")

me = TestClient(server.app, follow_redirects=False); me.cookies.set("internify_session", raw_cookie)
r = me.get(f"/u/{A.id}/live")
assert r.status_code == 200 and f'window.INTERNIFY_BASE="/u/{A.id}"' in r.text and "INTERNIFY_CLOUD=true" in r.text and 'noindex' in r.text and r.headers["cache-control"] == "no-store"
assert r.headers["x-frame-options"] == "DENY" and r.headers["x-content-type-options"] == "nosniff" and "referrer-policy" in r.headers
assert me.get(f"/u/{B.id}/live").status_code == 401 and me.get(f"/u/{B.id}/api/runs").status_code == 401 and me.get(f"/u/{B.id}/pdf/x.pdf").status_code == 401, "not someone else's pages"
assert me.get("/admin").status_code == 404 and me.get("/live").status_code == 404 and me.get(f"/u/{A.id}/api/agents").status_code == 200
assert 'const BASE = window.INTERNIFY_BASE' in me.get(f"/u/{A.id}/live").text
dash = open(os.path.join(server.STATIC_DIR, "index.html"), encoding="utf-8").read()
assert dash.count('BASE + "/api/') == 4 and 'fetch("/api/' not in dash and 'EventSource("/api' not in dash, "every call from the dashboard goes to its own person's address"
print("10 ok: signed in, you open your own dashboard (and nobody else's); the page calls only your own address")

# PDFs: only from your own folder
with users.use(A.id): os.makedirs(users.output_dir(), exist_ok=True); open(os.path.join(users.output_dir(), "cv.pdf"), "wb").write(b"%PDF-1.4 alice")
with users.use(B.id): os.makedirs(users.output_dir(), exist_ok=True); open(os.path.join(users.output_dir(), "cv.pdf"), "wb").write(b"%PDF-1.4 bob")
r = me.get(f"/u/{A.id}/pdf/cv.pdf"); assert r.status_code == 200 and r.content.endswith(b"alice") and r.headers["content-type"] == "application/pdf"
for bad in ("..%2f..%2fdata%2ffacts.json", "..%2fcv.pdf", "a%5Cb.pdf", "cv.txt", "nope.pdf"):
    assert me.get(f"/u/{A.id}/pdf/{bad}").status_code in (400, 404), bad
print("11 ok: PDFs come only from your own folder; odd file names are refused")

# events and runs: yours only
def make_run(uid, url):
    def go():
        with users.use(uid):
            rid = events.start_run(url=url); events.emit("scout", events.START, "Opening", url=url); events.emit_run("finished", score=80); return rid
    return contextvars.copy_context().run(go)
ra, rb = make_run(A.id, "https://a/1"), make_run(B.id, "https://b/1")
assert [x["run_id"] for x in events.bus.list_runs(user=A.id)] == [ra] and [x["run_id"] for x in events.bus.list_runs(user=B.id)] == [rb]
assert events.bus.latest_run_id(user=A.id) == ra and events.bus.latest_run_id(user=B.id) == rb and events.bus.latest_run_id() == rb and events.bus.latest_run_id(user="nobody0") is None
assert all(e["user"] == A.id for e in events.bus.get_run(ra)), "every event says whose it is"
assert [x["run_id"] for x in me.get(f"/u/{A.id}/api/runs").json()] == [ra]
assert me.get(f"/u/{A.id}/api/runs/{ra}").status_code == 200 and me.get(f"/u/{A.id}/api/runs/{rb}").status_code == 404, "another person's run looks like no run"
events.save_trace(rb); events.bus._runs.pop(rb); events.bus._order.remove(rb)
assert me.get(f"/u/{A.id}/api/runs/{rb}").status_code == 404, "and so does a saved trace of theirs"
async def sse():
    resp = server._stream_response(None, A.id); it = resp.body_iterator
    chunks = [await it.__anext__(), ]
    while len([x for x in chunks if x.startswith("data:")]) < 3: chunks.append(await asyncio.wait_for(it.__anext__(), 2))
    got = [json.loads(x[6:]) for x in chunks if x.startswith("data:")]
    assert {g["user"] for g in got} == {A.id} and {g["run_id"] for g in got} == {ra}, "the first events are only yours"
    def live():
        with users.use(B.id): events.start_run(url="https://b/2"); events.emit("scout", events.START, "theirs")
        with users.use(A.id): events.start_run(url="https://a/2"); events.emit("scout", events.START, "mine")
    contextvars.copy_context().run(live)
    more = []
    while len([m for m in more if m.startswith("data:")]) < 2: more.append(await asyncio.wait_for(it.__anext__(), 3))
    assert {json.loads(m[6:])["user"] for m in more if m.startswith("data:")} == {A.id}, "live events from someone else never arrive"
    await it.aclose()
run(sse())
print("12 ok: runs, saved traces and the live stream show only your own events")

# the maintainer's page
atok = websession.new_login(websession.ADMIN, "admin")
for bad in (("admin", "user"), (A.id, "admin")):
    try: websession.new_login(*bad); raise SystemExit("role and id must match")
    except ValueError: pass
r = anon().post(f"/auth/{atok}"); assert r.status_code == 303 and r.headers["location"] == "/admin"
adm = TestClient(server.app, follow_redirects=False); adm.cookies.set("internify_session", r.headers["set-cookie"].split("=", 1)[1].split(";")[0])
with users.use(A.id): plans.reserve_resume(); plans.record("ai")
page = adm.get("/admin")
assert page.status_code == 200 and A.id in page.text and B.id in page.text and "Alice" in page.text and "resumes this month" in page.text and 'noindex' in page.text and "<form" not in page.text, "read-only"
assert adm.get(f"/u/{A.id}/live").status_code == 401, "an admin session is not a person's session"
assert me.get("/admin").status_code == 404 and c.get("/admin").status_code == 404
data = admin.dashboard_data(); assert data["total"] == 2 and data["resumes"] == 1 and any(u["id"] == A.id and u["resumes"] == 1 for u in data["users"])
accounts.set_plan(A.id, "paid", (dt.date.today() + dt.timedelta(days=9)).isoformat()); config.AI_MONTHLY_BUDGET_CALLS = 100
assert admin.dashboard_data()["percent"] == admin.dashboard_data()["ai"] and "paid" in dict(admin.dashboard_data()["by_plan"]); config.AI_MONTHLY_BUDGET_CALLS = 0
print("13 ok: the admin page is read-only, only for the maintainer's session, and separate from people's sessions")

# pausing, deleting and signing out end sessions at once
accounts.set_status(A.id, "paused"); assert me.get(f"/u/{A.id}/live").status_code == 401
accounts.set_status(A.id, "active"); assert me.get(f"/u/{A.id}/live").status_code == 200
assert me.post("/signout").status_code == 303 and me.get(f"/u/{A.id}/live").status_code == 401
t2 = websession.new_login(A.id); ck, uid, role = websession.sign_in(t2); assert websession.session(ck) == (A.id, "user")
assert websession.session(ck, now=10**11) is None, "a session lasts 30 days"
t3 = websession.new_login(A.id); accounts.delete_account(A.id)
assert websession.session(ck) is None and not websession.peek(t3) and websession.sign_in(t3) is None, "deleting an account ends its sessions and links"
t4 = websession.new_login(B.id); accounts.set_status(B.id, "paused"); assert websession.sign_in(t4) is None, "a paused account can't sign in"
accounts.set_status(B.id, "active")
print("14 ok: pausing, deleting or signing out ends a session at once; paused accounts can't sign in")

# ---------------------------------------------------------------- Telegram: the commands that hand out the links
class Msg:
    def __init__(self): self.sent = []
    async def reply_text(self, t, **kw): self.sent.append((t, kw))
def upd(tg, name="X"): return types.SimpleNamespace(effective_user=types.SimpleNamespace(id=tg, first_name=name, username="u"), effective_message=Msg(), effective_chat=types.SimpleNamespace(id=tg), callback_query=None)
cB = accounts.by_telegram(1002)
from bot.handlers import common
u = upd(1002); run(common.restricted(cloud.dashboard_command)(u, types.SimpleNamespace(user_data={}, args=[])))
text, kw = u.effective_message.sent[0]; button = kw["reply_markup"].inline_keyboard[0][0]
assert "15 minutes" in text and button.url.startswith(BASE + "/auth/") and "dashboard" in button.text.lower()
token = button.url.rsplit("/", 1)[1]; assert websession.peek(token) and websession.sign_in(token)[1:] == (cB.id, "user"), "the link signs in the person who asked"
config.PUBLIC_URL = ""; u = upd(1002); run(common.restricted(cloud.dashboard_command)(u, types.SimpleNamespace(user_data={}, args=[])))
assert "isn't set up" in u.effective_message.sent[0][0]; config.PUBLIC_URL = BASE
config.ADMIN_TELEGRAM_ID = 777
u = upd(5); run(admin.adminweb_command(u, types.SimpleNamespace(args=[]))); assert u.effective_message.sent == [], "nobody but the maintainer is answered"
u = upd(777); run(admin.adminweb_command(u, types.SimpleNamespace(args=[])))
btn = u.effective_message.sent[0][1]["reply_markup"].inline_keyboard[0][0]; assert btn.url.startswith(BASE + "/auth/")
assert websession.sign_in(btn.url.rsplit("/", 1)[1])[1:] == (websession.ADMIN, "admin")
print("15 ok: /dashboard and /adminweb hand out one-time links (as buttons) to the right person only")

# /start from the pricing page's paid button, and names can't inject markup
config.PAY_INSTRUCTIONS = "Pay RM9.90 by DuitNow to <0123456789>"
t = cloud.welcome_text("<b>Eve</b>", True, upgrade=True)
assert "&lt;b&gt;Eve&lt;/b&gt;" in t and "<b>Paid plan</b>" in t and "&lt;0123456789&gt;" in t and "RM9.90 a month, 30 resumes" in t and "/dashboard" in t
assert "Paid plan" not in cloud.welcome_text("Sam", True) and "Paid plan" not in cloud.welcome_text("Sam", False, upgrade=False)
u = upd(3003, "<i>Zed</i>"); u.effective_message.text = "/start paid"
c3 = types.SimpleNamespace(user_data={}, args=["paid"])
run(common.start_command(u, c3)); txt, kw = u.effective_message.sent[0]
assert kw["parse_mode"] == "HTML" and "Welcome &lt;i&gt;Zed&lt;/i&gt;" in txt and "Paid plan" in txt and accounts.by_telegram(3003) is not None
u = upd(3004); u.effective_message.text = "/start web"; run(common.start_command(u, types.SimpleNamespace(user_data={}, args=["web"]))); assert "Paid plan" not in u.effective_message.sent[0][0]
config.PAY_INSTRUCTIONS = "To upgrade, message the person who runs this bot."
print("16 ok: the pricing page's paid button opens /start with the payment details; names can't inject markup")

# ---------------------------------------------------------------- inactive accounts (what the privacy policy promises)
msgs = []
class Bot:
    async def send_message(self, chat, text): msgs.append((chat, text))
ctx = types.SimpleNamespace(bot=Bot())
def person(tg, idle_days, plan="free", until=""):
    a = accounts.create(tg, f"P{tg}")
    with db._get_connection() as cx:
        stamp = (dt.datetime.now() - dt.timedelta(days=idle_days)).strftime("%Y-%m-%d %H:%M:%S")
        cx.execute("UPDATE accounts SET last_active = ?, created_at = ?, plan = ?, plan_until = ? WHERE id = ?", (stamp, stamp, plan, until, a.id)); cx.commit()
    with users.use(a.id):
        from bot.services import watchlist; watchlist.set_setting("owner_chat_id", str(tg))
    return a
fresh, quiet, warn, gone = person(4001, 10), person(4002, 100), person(4003, 178), person(4004, 190)
paid_ok = person(4005, 300, "paid", (dt.date.today() + dt.timedelta(days=20)).isoformat()); unl = person(4006, 400, "unlimited")
lapsed = person(4007, 300, "paid", (dt.date.today() - dt.timedelta(days=5)).isoformat())
run(cloud.retention_job(ctx))
told = {chat: text for chat, text in msgs}
assert 4003 in told and "In 5 days" in told[4003] and "export your data first" in told[4003], "a week's warning, with how to keep it"
assert 4004 in told and "were deleted" in told[4004] and accounts.by_telegram(4004) is None and accounts.get(gone.id).status == "deleted"
assert 4007 in told and accounts.by_telegram(4007) is None, "a lapsed paid plan doesn't protect an idle account"
assert all(t not in told for t in (4001, 4002, 4005, 4006)) and all(accounts.get(a.id).status == "active" for a in (fresh, quiet, paid_ok, unl)), "active, recent, paid and unlimited accounts are left alone"
msgs.clear(); run(cloud.retention_job(ctx)); assert not [m for m in msgs if m[0] == 4003], "the warning is sent once"
with db._get_connection() as cx: cx.execute("UPDATE accounts SET last_active = datetime('now') WHERE id = ?", (warn.id,)); cx.commit()
msgs.clear(); run(cloud.retention_job(ctx)); assert not msgs and accounts.get(warn.id).status == "active", "using the bot starts the count again"
print("17 ok: idle accounts get a week's warning and are then deleted; recent, paid and unlimited accounts are left alone")

# ---------------------------------------------------------------- start-up wiring
os.environ["INTERNIFY_CLOUD_PREVIEW"] = "1"; config.TELEGRAM_BOT_TOKEN = "123:abc"
cmds = lambda app: {x for h in app.handlers.get(0, []) for x in getattr(h, "commands", [])}
cloud_cmds = cmds(main.create_bot_app()); users.MODE = "single"; single_cmds = cmds(main.create_bot_app())
assert {"dashboard", "adminweb", "plan"} <= cloud_cmds and not ({"dashboard", "adminweb"} & single_cmds)
users.MODE = "cloud"; config.BOT_USERNAME = ""
class App:
    bot = types.SimpleNamespace(username="LiveBot", set_my_commands=lambda *a, **k: asyncio.sleep(0), set_my_short_description=lambda *a, **k: asyncio.sleep(0), set_my_description=lambda *a, **k: asyncio.sleep(0))
    class job_queue:
        daily = []
        @staticmethod
        def run_daily(cb, time=None, days=None, name=None): App.job_queue.daily.append(name)
        @staticmethod
        def run_once(cb, when=None, name=None): pass
        @staticmethod
        def get_jobs_by_name(n): return []
from bot import status
status._test_ai = lambda: "✓ works"
async def boot(): await main.post_init(App)
run(boot()); assert config.BOT_USERNAME == "LiveBot" and "inactive-accounts" in App.job_queue.daily and "plan-notices" in App.job_queue.daily
assert site.bot_url() == "https://t.me/LiveBot", "the public pages link to the bot's real username, taken from Telegram"
config.BOT_USERNAME = "SetByHand"; run(boot()); assert config.BOT_USERNAME == "SetByHand", "a username set in .env is kept"
users.MODE = "single"; os.environ.pop("INTERNIFY_CLOUD_PREVIEW")
print("18 ok: hosted start-up registers /dashboard and /adminweb, schedules the inactive-account check and learns the bot's username")
print("\n✅ web test passed")
