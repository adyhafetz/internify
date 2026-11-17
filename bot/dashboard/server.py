"""The web server inside the bot process: the public pages, the live dashboard and (hosted version) sign-in and the admin page.
It shares the in-memory event bus with the bot.

Single mode (the default): home, privacy and terms are public; the dashboard, its API and the PDFs need DASHBOARD_KEY if one is set.
Hosted mode (MODE=cloud): the marketing pages are public; each person opens their own dashboard at /u/<id>/live with a browser
session started from a one-time link sent by the bot (/dashboard); /admin is for the maintainer. Nothing else is reachable, and
nobody can open another person's pages, events or PDFs."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import threading
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi import Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response, StreamingResponse

from bot import config as bot_config
from bot.dashboard import site
from bot.services import websession
from service import config as service_config
from service import events, users

logger = logging.getLogger(__name__)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
SITE_DIR = os.path.join(STATIC_DIR, "site")
COOKIE = "internify_key"                 # single mode: the dashboard key
SESSION_COOKIE = "internify_session"     # hosted mode: a browser session
# Single mode: everything else needs the key (when one is set)
PUBLIC_PATHS = {"/", "/privacy", "/terms", "/healthz", "/dashboard", "/how-it-works", "/pricing", "/robots.txt", "/sitemap.xml"}
USER_PATH = re.compile(r"^/u/([a-z0-9]{6,24})(?:/|$)")

app = FastAPI(title="Internify Dashboard", docs_url=None, redoc_url=None)

PRIVATE_PAGE = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">
<title>Internify dashboard</title><body style="font:16px/1.6 system-ui;max-width:460px;margin:14vh auto;padding:0 18px;color:#0F172A;background:#F8FAFC">
<h1 style="letter-spacing:-.03em">Dashboard key</h1><p>The live dashboard is private. Enter your key to open it. This browser remembers it afterwards.</p>
<form method="get" action="/live"><input name="key" type="password" autofocus autocomplete="current-password" placeholder="Your key" style="font:inherit;padding:10px 14px;border:1px solid #DBE3EF;border-radius:999px;width:100%;margin-bottom:12px">
<button style="font:600 15px system-ui;padding:11px 22px;border:0;border-radius:999px;background:#2563EB;color:#fff;cursor:pointer">Open dashboard</button></form>
<p style="margin-top:28px"><a href="/" style="color:#2563EB">Back to the home page</a></p>"""


def _token(key: str) -> str:
    return hashlib.sha256(f"internify:{key}".encode()).hexdigest()


def _same(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def _https(request: Request) -> bool:
    return "https" in (request.headers.get("x-forwarded-proto", "") + request.headers.get("cf-visitor", "")) or request.url.scheme == "https"


async def _single_gate(request: Request, call_next):
    """The home, privacy and terms pages are public. The live dashboard, its API and the PDFs need DASHBOARD_KEY."""
    key = bot_config.DASHBOARD_KEY
    path = request.url.path
    if not key or path in PUBLIC_PATHS or path.startswith(("/site/", "/assets/")):
        return await call_next(request)

    supplied = request.query_params.get("key")
    if supplied is not None and _same(supplied, key):
        # Valid key in the link: remember it in a cookie, then reload the page without the key in the URL.
        clean = request.url.remove_query_params("key")
        resp = RedirectResponse(clean.path + (f"?{clean.query}" if clean.query else ""), status_code=303)
        resp.set_cookie(COOKIE, _token(key), max_age=60 * 60 * 24 * 30, httponly=True, samesite="lax", secure=_https(request))
        return resp
    if _same(request.cookies.get(COOKIE, ""), _token(key)):
        return await call_next(request)
    if path.startswith("/api/") or path.startswith("/pdf/"):
        return JSONResponse({"detail": "Private. Open /live?key=... first."}, status_code=401)
    return HTMLResponse(PRIVATE_PAGE, status_code=401)


CLOUD_PUBLIC = {"/", "/how-it-works", "/pricing", "/privacy", "/terms", "/healthz", "/robots.txt", "/sitemap.xml", "/dashboard", "/login", "/signout"}


def _sign_in_needed(request: Request):
    if "/api/" in request.url.path or "/pdf/" in request.url.path:
        return JSONResponse({"detail": "Sign in first: send /dashboard to the bot in Telegram."}, status_code=401)
    return HTMLResponse(_login_info(), status_code=401, headers={"Cache-Control": "no-store"})


def _login_info() -> str:
    url = site.bot_url()
    return site.message_page("Sign in from Telegram", "Your dashboard is private. Open the bot in Telegram and send /dashboard. It replies with a one-time link that "
                             "signs this browser in.", status_title="Sign in · Internify", link_url=url or "/", link_label="Open Telegram" if url else "Back to the home page")


async def _cloud_gate(request: Request, call_next):
    """Hosted mode: public pages, then a browser session for /u/<id>/... (only your own id) and for /admin (the maintainer only)."""
    path = request.url.path
    if path in CLOUD_PUBLIC or path.startswith(("/site/", "/assets/", "/auth/")):
        return await call_next(request)
    sess = await asyncio.to_thread(websession.session, request.cookies.get(SESSION_COOKIE, ""))
    m = USER_PATH.match(path)
    if m:
        if sess and sess[1] == "user" and sess[0] == m.group(1):
            return await call_next(request)
        return _sign_in_needed(request)
    if path == "/admin" or path.startswith("/admin/"):
        if sess and sess[1] == websession.ADMIN:
            return await call_next(request)
        return PlainTextResponse("Not found", status_code=404)      # the page isn't acknowledged to anyone else
    return PlainTextResponse("Not found", status_code=404)          # the single-user dashboard and API don't exist here


@app.middleware("http")
async def access(request: Request, call_next):
    response = await (_cloud_gate if users.cloud() else _single_gate)(request, call_next)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("X-Frame-Options", "DENY")
    if request.url.path.startswith(("/u/", "/admin", "/auth/", "/api/", "/pdf/", "/live")):
        response.headers["Cache-Control"] = "no-store"
    return response


# Brand assets (logo, Inter font): public, so the home and privacy pages can use them as well
app.mount("/assets", StaticFiles(directory=os.path.join(STATIC_DIR, "assets")), name="assets")


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


def _page(path: str) -> HTMLResponse:
    return HTMLResponse(site.render(path))


@app.get("/")
def home():
    return _page("/")


@app.get("/how-it-works")
def how_it_works():
    if not site.marketing_on():
        raise HTTPException(status_code=404, detail="Not found")
    return _page("/how-it-works")


@app.get("/pricing")
def pricing():
    if not site.marketing_on():
        raise HTTPException(status_code=404, detail="Not found")
    return _page("/pricing")


@app.get("/privacy")
def privacy():
    return _page("/privacy")


@app.get("/terms")
def terms():
    return _page("/terms")


@app.get("/robots.txt")
def robots():
    return PlainTextResponse(site.robots_txt())


@app.get("/sitemap.xml")
def sitemap():
    return Response(site.sitemap_xml(), media_type="application/xml")


@app.get("/site/style.css")
def site_css():
    return FileResponse(os.path.join(SITE_DIR, "style.css"), media_type="text/css")


@app.get("/dashboard")
def dashboard_alias():
    """A friendly address for the dashboard. Single mode: it sends you to /live (which asks for the key if needed). Hosted mode:
    it explains how to sign in from Telegram."""
    if users.cloud():
        return HTMLResponse(_login_info(), headers={"Cache-Control": "no-store"})
    return RedirectResponse("/live", status_code=307)


@app.get("/login")
def login_info():
    return HTMLResponse(_login_info(), headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------- signing in (hosted mode)

def _expired() -> HTMLResponse:
    return HTMLResponse(site.message_page("This link has expired", "Sign-in links work once and for 15 minutes. Send /dashboard to the bot in Telegram for a new one.",
                                          status_title="Link expired · Internify", link_url=site.bot_url() or "/", link_label="Open Telegram" if site.bot_url() else "Back to the home page"),
                        status_code=400, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


@app.get("/auth/{token}")
def auth_confirm(token: str):
    """The page the link opens. It only shows a button: apps that fetch links to make a preview must not use the token up."""
    if not users.cloud() or not websession.peek(token):
        return _expired()
    return HTMLResponse(site.message_page("Open your dashboard", "Press the button to sign in on this browser.", status_title="Sign in · Internify",
                                          button_url=f"/auth/{token}", button_label="Open my dashboard"),
                        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


@app.post("/auth/{token}")
def auth_sign_in(token: str, request: Request):
    result = websession.sign_in(token) if users.cloud() else None
    if result is None:
        return _expired()
    cookie, user_id, role = result
    resp = RedirectResponse("/admin" if role == websession.ADMIN else f"/u/{user_id}/live", status_code=303)
    resp.set_cookie(SESSION_COOKIE, cookie, max_age=websession.SESSION_TTL, httponly=True, samesite="lax", secure=_https(request), path="/")
    resp.headers["Referrer-Policy"] = "no-referrer"
    return resp


@app.post("/signout")
def sign_out(request: Request):
    websession.sign_out(request.cookies.get(SESSION_COOKIE, ""))
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


# ---------------------------------------------------------------- the dashboard

def _dashboard_html(base: str) -> HTMLResponse:
    """The live dashboard page. ``base`` is "" (single mode) or /u/<id>: where its API and PDFs are."""
    with open(os.path.join(STATIC_DIR, "index.html"), encoding="utf-8") as f:
        html = f.read()
    inject = f'<meta name="robots" content="noindex,nofollow"><script>window.INTERNIFY_BASE={json.dumps(base)};window.INTERNIFY_CLOUD={json.dumps(users.cloud())};</script>'
    return HTMLResponse(html.replace("<head>", "<head>" + inject, 1))


@app.get("/live")
def live():
    return _dashboard_html("")


@app.get("/u/{uid}/live")
def user_live(uid: str):
    return _dashboard_html(f"/u/{uid}")


@app.get("/api/agents")
def agents():
    return events.AGENTS


@app.get("/u/{uid}/api/agents")
def user_agents(uid: str):
    return events.AGENTS


@app.get("/api/runs")
def runs():
    return events.bus.list_runs()


@app.get("/u/{uid}/api/runs")
def user_runs(uid: str):
    return events.bus.list_runs(user=uid)


def _events_of(run_id: str, user: Optional[str]) -> list:
    evs = events.bus.get_run(run_id) or events.load_trace(run_id)
    if not evs or (user is not None and evs[0].get("user") != user):
        raise HTTPException(status_code=404, detail="Run not found")      # another person's run looks the same as no run
    return evs


@app.get("/api/runs/{run_id}")
def run_events(run_id: str):
    return _events_of(run_id, None)


@app.get("/u/{uid}/api/runs/{run_id}")
def user_run_events(uid: str, run_id: str):
    return _events_of(run_id, uid)


def _pdf(folder: str, filename: str) -> FileResponse:
    if "/" in filename or "\\" in filename or ".." in filename or not filename.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Invalid filename")
    path = os.path.join(folder, filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path, media_type="application/pdf")


@app.get("/pdf/{filename}")
def pdf(filename: str):
    return _pdf(service_config.OUTPUT_DIR, filename)


@app.get("/u/{uid}/pdf/{filename}")
def user_pdf(uid: str, filename: str):
    return _pdf(users.output_dir(uid), filename)       # only that person's own folder


@app.post("/api/simulate")
def simulate(scenario: str = "normal"):
    """Play a scripted run through the real event bus. Needs no API keys. Single mode only.
    scenario: normal | blocked (Scout finds another way in) | paste (no route works, Scout asks you to paste) | scan (the overnight scan) | tidy (the Curator)"""
    from bot.dashboard.simulate import run_simulation

    threading.Thread(target=run_simulation, kwargs={"scenario": scenario}, daemon=True).start()
    return JSONResponse({"started": True})


# ---------------------------------------------------------------- the maintainer's page (hosted mode)

@app.get("/admin")
def admin_page():
    from bot import admin
    return HTMLResponse(site.admin_page(admin.dashboard_data()))


def _sse(ev: dict[str, Any]) -> str:
    return f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"


def _stream_response(run: Optional[str], user: Optional[str]) -> StreamingResponse:
    """Server-Sent Events: backlog of one run, then live events (all runs, or just ``run``). With ``user``, only that person's
    events are ever sent."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def mine(ev: dict[str, Any]) -> bool:
        return user is None or ev.get("user") == user

    def on_event(ev: dict[str, Any]) -> None:
        if (run and ev.get("run_id") != run) or not mine(ev):
            return
        loop.call_soon_threadsafe(queue.put_nowait, ev)

    # Subscribe before reading the backlog so nothing is missed; de-duplicate by (run, seq).
    unsubscribe = events.bus.subscribe(on_event)
    backlog_run = run or events.bus.latest_run_id(user)
    backlog = [e for e in (events.bus.get_run(backlog_run) if backlog_run else []) if mine(e)]
    seen = {(e["run_id"], e["seq"]) for e in backlog}

    async def gen():
        try:
            yield "retry: 2000\n\n"
            for ev in backlog:
                yield _sse(ev)
            while True:
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                key = (ev["run_id"], ev["seq"])
                if key in seen:
                    continue
                seen.add(key)
                yield _sse(ev)
        finally:
            unsubscribe()

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/stream")
async def stream(run: Optional[str] = None):
    return _stream_response(run, None)


@app.get("/u/{uid}/api/stream")
async def user_stream(uid: str, run: Optional[str] = None):
    return _stream_response(run, uid)


def start_dashboard(host: str = "0.0.0.0", port: int = 8080) -> threading.Thread:
    """Serve the dashboard on a background thread. Never raises into the bot."""
    import uvicorn

    def _run() -> None:
        try:
            # loop="asyncio": uvicorn's default "auto" installs uvloop as the process-wide event loop
            # policy, which breaks python-telegram-bot's run_polling() in the main thread.
            config = uvicorn.Config(app, host=host, port=port, log_level="warning", loop="asyncio")
            uvicorn.Server(config).run()
        except Exception as e:  # port in use, etc: the bot must keep working without the dashboard
            logger.error("Dashboard failed to start: %s", e)

    t = threading.Thread(target=_run, name="dashboard", daemon=True)
    t.start()
    logger.info("Dashboard available on http://localhost:%d/live%s", port, "  (protected by DASHBOARD_KEY)" if bot_config.DASHBOARD_KEY else "")
    return t
