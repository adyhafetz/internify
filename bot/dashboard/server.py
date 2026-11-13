"""Live dashboard server. Runs inside the bot process so it shares the in-memory event bus."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import html as _html
import json
import logging
import os
import threading
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi import Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse

from bot import config as bot_config
from service import config as service_config
from service import events

logger = logging.getLogger(__name__)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
SITE_DIR = os.path.join(STATIC_DIR, "site")
COOKIE = "internify_key"
PUBLIC_PATHS = {"/", "/privacy", "/terms", "/healthz", "/dashboard"}  # everything else needs the key (when one is set)

app = FastAPI(title="Internify Dashboard", docs_url=None, redoc_url=None)

PRIVATE_PAGE = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Internify dashboard</title><body style="font:16px/1.6 system-ui;max-width:460px;margin:14vh auto;padding:0 18px;color:#0F172A;background:#F8FAFC">
<h1 style="letter-spacing:-.03em">Dashboard key</h1><p>The live dashboard is private. Enter your key to open it. This browser remembers it afterwards.</p>
<form method="get" action="/live"><input name="key" type="password" autofocus autocomplete="current-password" placeholder="Your key" style="font:inherit;padding:10px 14px;border:1px solid #DBE3EF;border-radius:999px;width:100%;margin-bottom:12px">
<button style="font:600 15px system-ui;padding:11px 22px;border:0;border-radius:999px;background:#2563EB;color:#fff;cursor:pointer">Open dashboard</button></form>
<p style="margin-top:28px"><a href="/" style="color:#2563EB">Back to the home page</a></p>"""


def _token(key: str) -> str:
    return hashlib.sha256(f"internify:{key}".encode()).hexdigest()


def _same(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


@app.middleware("http")
async def require_key(request: Request, call_next):
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
        https = "https" in (request.headers.get("x-forwarded-proto", "") + request.headers.get("cf-visitor", ""))
        resp.set_cookie(COOKIE, _token(key), max_age=60 * 60 * 24 * 30, httponly=True, samesite="lax", secure=https)
        return resp
    if _same(request.cookies.get(COOKIE, ""), _token(key)):
        return await call_next(request)
    if path.startswith("/api/") or path.startswith("/pdf/"):
        return JSONResponse({"detail": "Private. Open /live?key=... first."}, status_code=401)
    return HTMLResponse(PRIVATE_PAGE, status_code=401)


# Brand assets (logo, Inter font): public, so the home and privacy pages can use them as well
app.mount("/assets", StaticFiles(directory=os.path.join(STATIC_DIR, "assets")), name="assets")


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


def _site_page(name: str) -> HTMLResponse:
    """A public page, with {{CONTACT}} replaced by the owner's contact address (or the GitHub issues page)."""
    with open(os.path.join(SITE_DIR, name), encoding="utf-8") as f:
        html = f.read()
    email = bot_config.CONTACT_EMAIL
    contact = (f'<a href="mailto:{_html.escape(email)}">{_html.escape(email)}</a>, or open an issue at '
               if email else "open an issue at ") + '<a href="https://github.com/adyhafetz/internify/issues">github.com/adyhafetz/internify/issues</a>'
    return HTMLResponse(html.replace("{{CONTACT}}", contact))


@app.get("/")
def home():
    return _site_page("index.html")


@app.get("/privacy")
def privacy():
    return _site_page("privacy.html")


@app.get("/terms")
def terms():
    return _site_page("terms.html")


@app.get("/site/style.css")
def site_css():
    return FileResponse(os.path.join(SITE_DIR, "style.css"), media_type="text/css")


@app.get("/dashboard")
def dashboard_alias():
    """A friendly address for the dashboard: it sends you to /live (which asks for the key if needed)."""
    return RedirectResponse("/live", status_code=307)


@app.get("/live")
def live():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/api/agents")
def agents():
    return events.AGENTS


@app.get("/api/runs")
def runs():
    return events.bus.list_runs()


@app.get("/api/runs/{run_id}")
def run_events(run_id: str):
    evs = events.bus.get_run(run_id) or events.load_trace(run_id)
    if not evs:
        raise HTTPException(status_code=404, detail="Run not found")
    return evs


@app.get("/pdf/{filename}")
def pdf(filename: str):
    if "/" in filename or "\\" in filename or ".." in filename or not filename.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Invalid filename")
    path = os.path.join(service_config.OUTPUT_DIR, filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path, media_type="application/pdf")


@app.post("/api/simulate")
def simulate(scenario: str = "normal"):
    """Play a scripted run through the real event bus. Needs no API keys.
    scenario: normal | blocked (Scout finds another way in) | paste (no route works, Scout asks you to paste) | scan (the overnight scan) | tidy (the Curator)"""
    from bot.dashboard.simulate import run_simulation

    threading.Thread(target=run_simulation, kwargs={"scenario": scenario}, daemon=True).start()
    return JSONResponse({"started": True})


def _sse(ev: dict[str, Any]) -> str:
    return f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"


@app.get("/api/stream")
async def stream(run: Optional[str] = None):
    """Server-Sent Events: backlog of one run, then live events (all runs, or just ``run``)."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def on_event(ev: dict[str, Any]) -> None:
        if run and ev.get("run_id") != run:
            return
        loop.call_soon_threadsafe(queue.put_nowait, ev)

    # Subscribe before reading the backlog so nothing is missed; de-duplicate by (run, seq).
    unsubscribe = events.bus.subscribe(on_event)
    backlog_run = run or events.bus.latest_run_id()
    backlog = events.bus.get_run(backlog_run) if backlog_run else []
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
