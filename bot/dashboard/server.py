"""Live dashboard server. Runs inside the bot process so it shares the in-memory event bus."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
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
PUBLIC_PATHS = {"/", "/privacy", "/terms", "/healthz"}  # everything else needs the key (when one is set)

app = FastAPI(title="Internify Dashboard", docs_url=None, redoc_url=None)

PRIVATE_PAGE = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Private</title><body style="font:16px/1.6 system-ui;max-width:520px;margin:15vh auto;padding:0 18px;color:#1b1f27">
<h1>This dashboard is private</h1><p>Open the link with your key, for example <code>/live?key=YOUR_KEY</code>. The browser remembers it afterwards.</p>
<p><a href="/">Back to the home page</a></p>"""


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


@app.get("/")
def home():
    return FileResponse(os.path.join(SITE_DIR, "index.html"))


@app.get("/privacy")
def privacy():
    return FileResponse(os.path.join(SITE_DIR, "privacy.html"))


@app.get("/terms")
def terms():
    return FileResponse(os.path.join(SITE_DIR, "terms.html"))


@app.get("/site/style.css")
def site_css():
    return FileResponse(os.path.join(SITE_DIR, "style.css"), media_type="text/css")


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


@app.get("/api/models")
def models():
    """Which AI model (if any) each agent uses, so the dashboard can show it. Names only, no keys."""
    from service import config as sc
    helpers = bool(sc.OPENAI_API_KEY)
    return JSONResponse({
        "scout": sc.SCOUT_MODEL if helpers else "", "analyst": sc.EXTRACTOR_MODEL, "archivist": f"{sc.EMBEDDING_MODEL} (embeddings)",
        "scribe": sc.GENERATOR_MODEL, "critic": sc.CRITIC_MODEL, "publisher": "",
        "discoverer": sc.DISCOVERER_MODEL if helpers else "", "matcher": f"{sc.EXTRACTOR_MODEL} + {sc.EMBEDDING_MODEL}",
    })


@app.post("/api/simulate")
def simulate(scenario: str = "normal"):
    """Play a scripted run through the real event bus. Needs no API keys.
    scenario: normal | blocked (Scout finds another way in) | paste (no route works, Scout asks you to paste) | scan (the overnight scan)"""
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
