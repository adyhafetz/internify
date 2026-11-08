"""The setup wizard's small web server. Runs on your own computer only, and stops itself when you finish."""
from __future__ import annotations

import html as _html
import os
import re
from typing import Optional
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from bot.setup import envfile, google_setup

SETUP_DIR = os.getenv("SETUP_DIR", ".")
ENV_PATH = os.path.join(SETUP_DIR, ".env")
TEMPLATE_PATH = os.path.join(SETUP_DIR, ".env.example")
STATIC = os.path.join(os.path.dirname(__file__), "static")

OPENAI_PREFERRED = {
    "EXTRACTOR_MODEL": ["gpt-5.4-nano", "gpt-4o-mini"],
    "CRITIC_MODEL": ["gpt-5.4-mini", "gpt-4o-mini"],
    "GENERATOR_MODEL": ["gpt-5.4-mini", "gpt-4o"],
}
OPENAI_EMBED = ["text-embedding-3-small"]

app = FastAPI(docs_url=None, redoc_url=None)
app.mount("/assets", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "..", "dashboard", "static", "assets")), name="assets")
state = {"saved": False, "done": False, "finish": None, "google": {}}    # "finish" is set by __main__ to stop the server


def _get(url: str, **kw) -> httpx.Response:
    """One place for outgoing requests, so tests can replace it."""
    return httpx.get(url, timeout=kw.pop("timeout", 15.0), **kw)


def _fail(message: str) -> JSONResponse:
    return JSONResponse({"ok": False, "message": message})


# ---------------------------------------------------------------- choosing models from what the account offers

def pick_openai_models(available: set[str]) -> dict[str, str]:
    """The preferred model of each job if the account has it, else the next one; never an empty answer."""
    return {k: next((m for m in prefs if m in available), prefs[0]) for k, prefs in OPENAI_PREFERRED.items()} | {
        "EMBEDDING_MODEL": next((m for m in OPENAI_EMBED if m in available), OPENAI_EMBED[0])}


_SKIP = re.compile(r"image|tts|live|audio|vision|robotics|computer|thinking|exp|latest|learnlm|gemma|aqa", re.I)


def looks_openai(model: str) -> bool:
    return bool(re.match(r"(gpt-|o\d|chatgpt|text-embedding-3|text-embedding-ada)", model.lower()))


def looks_gemini(model: str) -> bool:
    return bool(re.match(r"(gemini|models/gemini|text-embedding-004|embedding-001)", model.lower()))


def _version(name: str) -> tuple:
    m = re.search(r"gemini-(\d+(?:\.\d+)*)", name)
    return tuple(int(x) for x in m.group(1).split(".")) if m else (0,)


def _embed_rank(name: str) -> tuple:
    """Prefer the 'gemini-embedding' family, then the highest number in the name."""
    nums = re.findall(r"\d+", name)
    return (name.startswith("gemini-embedding"), int(nums[-1]) if nums else 0)


def pick_gemini_models(items: list[dict]) -> Optional[dict[str, str]]:
    """Gemini model names change often, so choose from the live list: the newest 'flash' for writing and checking, the
    newest 'flash-lite' for reading (or flash if there is none), and an embedding model."""
    def short(i): return i.get("name", "").removeprefix("models/")
    chat = [short(i) for i in items if "generateContent" in i.get("supportedGenerationMethods", []) and not _SKIP.search(short(i))]
    emb = [short(i) for i in items if "embedContent" in i.get("supportedGenerationMethods", []) or "embedding" in short(i)]
    flash = sorted([m for m in chat if re.fullmatch(r"gemini-[\d.]+-flash(-\d+)?", m)], key=_version)
    lite = sorted([m for m in chat if re.fullmatch(r"gemini-[\d.]+-flash-lite(-\d+)?", m)], key=_version)
    if not flash or not emb:
        return None
    best, small = flash[-1], (lite[-1] if lite else flash[-1])
    return {"EXTRACTOR_MODEL": small, "CRITIC_MODEL": best, "GENERATOR_MODEL": best, "EMBEDDING_MODEL": sorted(emb, key=_embed_rank)[-1]}


# ---------------------------------------------------------------- tests of each key

class AIKey(BaseModel):
    provider: str
    key: str


@app.post("/api/test/ai")
def test_ai(body: AIKey):
    key = body.key.strip()
    if not key:
        return _fail("Paste your key first.")
    try:
        if body.provider == "gemini":
            r = _get("https://generativelanguage.googleapis.com/v1beta/models", params={"key": key, "pageSize": 200})
        else:
            r = _get("https://api.openai.com/v1/models", headers={"Authorization": f"Bearer {key}"})
    except httpx.HTTPError:
        return _fail("I couldn't reach the service. Check your internet connection and try again.")
    if r.status_code in (400, 401, 403):
        return _fail("That key was rejected. Check that you copied all of it, with nothing extra before or after.")
    if r.status_code == 429:
        return _fail("The service says you are over a limit right now. Wait a minute and try again.")
    if r.status_code != 200:
        return _fail(f"The service answered with an unexpected error (code {r.status_code}). Try again in a moment.")
    data = r.json()
    if body.provider == "gemini":
        models = pick_gemini_models(data.get("models", []))
        if models is None:
            return JSONResponse({"ok": True, "models": {}, "message": "Your key works, but I couldn't choose models automatically. "
                                 "Open “Models” below and type the names from ai.google.dev."})
        return JSONResponse({"ok": True, "models": models, "message": "Your key works."})
    models = pick_openai_models({m.get("id", "") for m in data.get("data", [])})
    return JSONResponse({"ok": True, "models": models, "message": "Your key works. (Resumes are paid for from your OpenAI credit, so make sure you added some.)"})


class Token(BaseModel):
    token: str


@app.post("/api/test/telegram")
def test_telegram(body: Token):
    token = body.token.strip()
    if not re.fullmatch(r"\d+:[\w-]{20,}", token):
        return _fail("That doesn't look like a bot token. It looks like 123456789:ABCdef… and BotFather sends it right after you create the bot.")
    try:
        r = _get(f"https://api.telegram.org/bot{token}/getMe")
    except httpx.HTTPError:
        return _fail("I couldn't reach Telegram. Check your internet connection and try again.")
    if r.status_code != 200 or not r.json().get("ok"):
        return _fail("Telegram rejected that token. Copy it again from BotFather.")
    me = r.json()["result"]
    return JSONResponse({"ok": True, "username": me.get("username", ""), "message": f"Found your bot: @{me.get('username', '')}."})


@app.post("/api/telegram/detect")
def detect_user(body: Token):
    """Your own Telegram ID, taken from the message you just sent to your bot."""
    token = body.token.strip()
    try:
        r = _get(f"https://api.telegram.org/bot{token}/getUpdates", params={"timeout": 0, "limit": 50})
    except httpx.HTTPError:
        return _fail("I couldn't reach Telegram.")
    if r.status_code == 409:
        return _fail("Another copy of this bot is running, so I can't listen. Stop it, or type your ID by hand (see below).")
    if r.status_code != 200 or not r.json().get("ok"):
        return _fail("Telegram didn't accept the token.")
    updates = r.json().get("result", [])
    msgs = [u for u in updates if (u.get("message") or {}).get("chat", {}).get("type") == "private"]
    if not msgs:
        return JSONResponse({"ok": True, "found": False, "message": "I haven't seen your message yet. Open your bot in Telegram, press Start, send “hi”, then try again."})
    last = msgs[-1]
    sender = last["message"]["from"]
    try:   # mark the messages as read so the bot does not answer an old “hi” when it starts
        _get(f"https://api.telegram.org/bot{token}/getUpdates", params={"offset": updates[-1]["update_id"] + 1, "timeout": 0})
    except httpx.HTTPError:
        pass
    name = " ".join(x for x in (sender.get("first_name"), sender.get("last_name")) if x)
    return JSONResponse({"ok": True, "found": True, "user_id": str(sender["id"]), "name": name, "message": f"Found you: {name or sender['id']}."})


class Key(BaseModel):
    key: str


@app.post("/api/test/scrapingant")
def test_scrapingant(body: Key):
    key = body.key.strip()
    if not key:
        return _fail("Paste your key first.")
    try:
        r = _get("https://api.scrapingant.com/v2/general", params={"url": "https://example.com", "x-api-key": key, "browser": "false"}, timeout=30.0)
    except httpx.HTTPError:
        return _fail("I couldn't reach ScrapingAnt.")
    if r.status_code == 200:
        return JSONResponse({"ok": True, "message": "Your key works."})
    if r.status_code in (401, 403):
        return _fail("ScrapingAnt rejected that key. Copy it again from your dashboard.")
    return _fail(f"I couldn't confirm it (code {r.status_code}). You can keep it and check later.")


# ---------------------------------------------------------------- state and saving

SECRETS = {"OPENAI_API_KEY", "GEMINI_API_KEY", "TELEGRAM_BOT_TOKEN", "SCRAPINGANT_API_KEY", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN", "DASHBOARD_KEY"}


@app.get("/api/state")
def get_state():
    """What is already set (never the secrets themselves), so running the wizard again keeps your keys."""
    cur = envfile.read_env(ENV_PATH)
    return {"launcher": bool(os.getenv("SETUP_LAUNCHER")), "provider": cur.get("AI_PROVIDER", "openai") or "openai",
            "has": {k: bool(cur.get(k)) for k in SECRETS | {"TELEGRAM_ALLOWED_USER_IDS", "GOOGLE_CLIENT_ID", "GOOGLE_SHEET_ID", "GOOGLE_DRIVE_FOLDER_ID"}},
            "allowed_user_ids": cur.get("TELEGRAM_ALLOWED_USER_IDS", ""),
            "scan_time": cur.get("SCAN_TIME", "") or "08:00", "scan_timezone": cur.get("SCAN_TIMEZONE", ""),
            "contact_email": cur.get("CONTACT_EMAIL", ""), "public_url": cur.get("PUBLIC_URL", ""),
            "models": {k: cur.get(k, "") for k in ("EXTRACTOR_MODEL", "CRITIC_MODEL", "GENERATOR_MODEL", "EMBEDDING_MODEL")}}


# ---------------------------------------------------------------- Google: sign in, then create the folder and sheet

class GoogleClient(BaseModel):
    client_id: str
    client_secret: str


def _redirect_uri() -> str:
    # The page's own address, with no path: Google's "Desktop app" clients accept any localhost port, and nothing more is assumed.
    return f"http://localhost:{int(os.getenv('SETUP_PORT', '8765'))}/"


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>{_html.escape(title)}</title>"
                        f"<body style=\"font:16px/1.6 system-ui;max-width:480px;margin:14vh auto;padding:0 18px;color:#0F172A;background:#F8FAFC\">"
                        f"<h1 style='letter-spacing:-.03em'>{_html.escape(title)}</h1><p>{_html.escape(body)}</p>")


@app.post("/api/google/start")
def google_start(body: GoogleClient):
    cid, secret = body.client_id.strip(), body.client_secret.strip()
    if not cid or not secret:
        return _fail("Paste both the Client ID and the Client secret first.")
    try:
        flow = google_setup.make_flow(cid, secret, _redirect_uri())
        url, st = flow.authorization_url(access_type="offline", prompt="consent")
    except Exception as err:
        return _fail(f"I couldn't start the Google sign-in ({err}). Check the Client ID and secret.")
    state["google"] = {"flow": flow, "state": st, "client_id": cid, "client_secret": secret, "creds": None}
    return JSONResponse({"ok": True, "auth_url": url})


def google_callback(request: Request):
    g = state["google"]
    q = request.query_params
    if q.get("error"):
        return _page("Google sign-in was cancelled", "Nothing was connected. Close this tab and try again from the setup page.")
    if not g.get("flow") or q.get("state") != g.get("state") or not q.get("code"):
        return _page("That sign-in link is not valid", "Go back to the setup page and press Connect Google again.")
    try:
        g["flow"].fetch_token(code=q["code"])
        creds = g["flow"].credentials
    except Exception:
        return _page("Google did not accept the sign-in", "Check that the client type is Desktop app, then try again from the setup page.")
    if not creds.refresh_token:
        return _page("Google did not give a lasting login", "Remove Internify at myaccount.google.com/permissions, then try again from the setup page.")
    g["creds"] = creds
    return _page("Connected to Google", "You can close this tab and go back to the setup page.")


@app.get("/api/google/status")
def google_status():
    g = state["google"]
    return {"connected": bool(g.get("creds")), "created": bool(g.get("ids"))}


@app.post("/api/google/create")
def google_create():
    g = state["google"]
    if not g.get("creds"):
        return _fail("Connect Google first.")
    try:
        ids = google_setup.create_tracker(g["creds"])
    except Exception as err:
        return _fail(f"I couldn't create them ({err}). Check that the Google Drive API and the Google Sheets API are both enabled for your project.")
    g["ids"] = ids
    return JSONResponse({"ok": True, "message": "Created a Drive folder “Internify resumes” and a spreadsheet “Internify tracker”.",
                         "sheet_url": f"https://docs.google.com/spreadsheets/d/{ids['GOOGLE_SHEET_ID']}/edit",
                         "folder_url": f"https://drive.google.com/drive/folders/{ids['GOOGLE_DRIVE_FOLDER_ID']}"})


class Settings(BaseModel):
    provider: str = "openai"
    ai_key: str = ""
    models: dict[str, str] = {}
    telegram_token: str = ""
    user_id: str = ""
    anyone_can_use: bool = False
    scrapingant_key: str = ""
    scan_time: str = "08:00"
    scan_timezone: str = "UTC"
    contact_email: str = ""
    dashboard_key: str = ""
    public_url: str = ""
    google: dict[str, str] = {}


@app.post("/api/save")
def save(s: Settings):
    cur = envfile.read_env(ENV_PATH)
    key_name = "GEMINI_API_KEY" if s.provider == "gemini" else "OPENAI_API_KEY"
    ai_key = s.ai_key.strip() or cur.get(key_name, "")
    token = s.telegram_token.strip() or cur.get("TELEGRAM_BOT_TOKEN", "")
    ids = s.user_id.strip() or cur.get("TELEGRAM_ALLOWED_USER_IDS", "")
    if s.provider not in ("openai", "gemini"):
        return _fail("Choose OpenAI or Gemini.")
    if not ai_key:
        return _fail("The AI key is missing. Go back to step 1.")
    if not token:
        return _fail("The Telegram bot token is missing. Go back to step 2.")
    if ids and not re.fullmatch(r"\d+(,\s*\d+)*", ids):
        return _fail("Your Telegram ID should be only numbers, for example 123456789.")
    if not ids and not s.anyone_can_use:
        return _fail("I need your Telegram ID so only you can use the bot. Go back to step 2, or tick the box that lets anyone use it.")
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", s.scan_time.strip()):
        return _fail("The time should look like 08:00.")
    try:
        ZoneInfo(s.scan_timezone.strip())
    except Exception:
        return _fail("I don't know that timezone. Use a name like Asia/Kuala_Lumpur.")
    models = {k: s.models.get(k, "").strip() for k in ("EXTRACTOR_MODEL", "CRITIC_MODEL", "GENERATOR_MODEL", "EMBEDDING_MODEL")}
    if s.provider == "gemini":
        # Gemini cannot answer to an OpenAI model name, so never save one (or an empty one) for it.
        if not all(models.values()) or any(looks_openai(m) for m in models.values()):
            return _fail("I need Gemini model names. Go back to step 1 and press “Check key” again, and I'll choose them for you.")
    else:
        # The reverse too: OpenAI cannot answer to a Gemini model name. Use the usual choices instead.
        fixed = pick_openai_models(set())
        models = {k: (fixed[k] if (not v or looks_gemini(v)) else v) for k, v in models.items()}
    values: dict[str, str] = {"AI_PROVIDER": s.provider, key_name: ai_key, "TELEGRAM_BOT_TOKEN": token,
                              "TELEGRAM_ALLOWED_USER_IDS": ids, "SCAN_TIME": s.scan_time.strip(), "SCAN_TIMEZONE": s.scan_timezone.strip()}
    values.update(models)
    if s.scrapingant_key.strip():
        values["SCRAPINGANT_API_KEY"] = s.scrapingant_key.strip()
    if s.contact_email.strip():
        values["CONTACT_EMAIL"] = s.contact_email.strip()
    allowed = {"GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN", "GOOGLE_SHEET_ID", "GOOGLE_DRIVE_FOLDER_ID"}
    g = state["google"]
    if g.get("creds"):   # signed in on this page: the login comes from here, never through the browser
        values.update({"GOOGLE_CLIENT_ID": g["client_id"], "GOOGLE_CLIENT_SECRET": g["client_secret"], "GOOGLE_REFRESH_TOKEN": g["creds"].refresh_token})
        values.update(g.get("ids", {}))
    for k, v in s.google.items():   # values typed by hand win
        if k in allowed and v.strip():
            values[k] = v.strip()
    if s.public_url.strip():
        url = s.public_url.strip().rstrip("/")
        if not re.fullmatch(r"https://[\w.-]+(:\d+)?", url):
            return _fail("The website address should look like https://internify.yourdomain.com (https, no path).")
        values["PUBLIC_URL"] = url
    if s.dashboard_key.strip():
        if re.search(r"\s", s.dashboard_key):
            return _fail("The dashboard key can't contain spaces.")
        values["DASHBOARD_KEY"] = s.dashboard_key.strip()
    try:
        envfile.write_env(ENV_PATH, TEMPLATE_PATH, values)
    except (OSError, ValueError) as err:
        return _fail(f"I couldn't write the settings file ({err}).")
    state["saved"] = True
    return JSONResponse({"ok": True, "message": "Saved."})


@app.post("/api/finish")
def finish():
    """The last button: the wizard stops itself, and the start script carries on and starts Internify."""
    if not state["saved"]:
        return _fail("Save your settings first.")
    state["done"] = True
    if state["finish"]:
        state["finish"]()
    return JSONResponse({"ok": True})


@app.get("/")
def page(request: Request):
    if {"code", "error"} & set(request.query_params):   # Google sends you back here after you sign in
        return google_callback(request)
    return FileResponse(os.path.join(STATIC, "index.html"))
