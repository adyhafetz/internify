"""Offline test of the Curator (tidy-up). The AI is replaced by a fake."""
from __future__ import annotations
import asyncio, json, os, sys, tempfile, types

sys.path.insert(0, os.path.abspath("."))
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
tmp = tempfile.mkdtemp()
os.environ["DATABASE_PATH"] = os.path.join(tmp, "app.db")
from service import config
config.FACT_STORE_PATH, config.SKELETON_PATH = os.path.join(tmp, "facts.json"), os.path.join(tmp, "resume_skeleton.json")
config.EMBEDDING_CACHE_PATH = os.path.join(tmp, "cache.json")
sk = json.load(open("service/fact_store/resume_skeleton.json"))
sk["skills"] = {"Languages": ["Python", "python", "Docker", "JS"], "Frameworks & Libraries": ["React", "React.js", "Teamwork"],
                "Databases": ["postgres"], "AI/ML": [], "Developer Tools & Infra": ["Git"]}
json.dump(sk, open(config.SKELETON_PATH, "w"))
E = sk["experience_entries"][0]["id"]
WEAK = "Responsible for building REST APIs with FastAPI and PostgreSQL, handling 2,000 requests a day for the payments team across several regions and time zones of the company"
facts = [
    {"id": "f1", "entry_id": E, "section": "experience", "text": "Built REST APIs with FastAPI", "tech_tags": ["fastapi", "FastAPI", "python"]},
    {"id": "f2", "entry_id": E, "section": "experience", "text": "Built REST APIs with FastAPI", "tech_tags": ["FastAPI"]},
    {"id": "f3", "entry_id": E, "section": "experience", "text": "Cached hot paths with Redis to cut latency by 40%", "tech_tags": ["redis"]},
    {"id": "f4", "entry_id": E, "section": "experience", "text": "Cached the hot paths with Redis to cut latency by 40%", "tech_tags": ["Redis"]},
    {"id": "f5", "entry_id": E, "section": "experience", "text": WEAK, "tech_tags": ["FastAPI"]},
    {"id": "f6", "entry_id": E, "section": "experience", "text": "Helped the team ship features", "tech_tags": []},
]
json.dump(facts, open(config.FACT_STORE_PATH, "w"))
from service import curator
from service.profile_data import _load_facts_raw, _load_skeleton_raw

GOOD = "Built FastAPI and PostgreSQL REST APIs handling 2,000 requests a day for the payments team"
def fake_rewrite(items, groups=None):
    out = {}
    if "f5" in items: out["f5"] = GOOD
    if "f6" in items: out["f6"] = "Shipped 12 features with the team"          # adds a number: must be refused
    return out

r = curator.tidy(rewrite=fake_rewrite, github=False, already_covered=lambda t, s: set())
s = _load_skeleton_raw()["skills"]
assert s["Languages"] == ["Python", "Docker", "JavaScript"] and s["Frameworks & Libraries"] == ["React.js", "Teamwork"] and s["Databases"] == ["PostgreSQL"], s
fs = _load_facts_raw()
assert [f["id"] for f in fs] == ["f1", "f3", "f4", "f5", "f6"], "the exact duplicate f2 is removed"
assert fs[0]["tech_tags"] == ["FastAPI", "Python"] and fs[1]["tech_tags"] == ["Redis"]
assert r.backup and os.path.exists(os.path.join(r.backup, "facts.json")) and len(json.load(open(os.path.join(r.backup, "facts.json")))) == 6, "a backup of the old data is kept"
assert any("Renamed skill postgres → PostgreSQL" == x for x in r.fixed) and any("repeated skill" in x for x in r.fixed)
print("A ok: automatic fixes: repeated and misspelled skills, tag spellings, exact duplicate bullet; backup kept:", r.fixed)

kinds = {(x.kind, x.data.get("item") or x.data.get("fact_id")) for x in r.suggestions}
assert ("remove_skill", "Teamwork") in kinds and ("move_skill", "Docker") in kinds and ("add_skill", "Redis") in kinds and ("add_skill", "FastAPI") in kinds
assert ("drop_duplicate", "f3") in kinds or ("drop_duplicate", "f4") in kinds
rw = [x for x in r.suggestions if x.kind == "rewrite"]
assert [x.data["fact_id"] for x in rw] == ["f5"], "the rewrite that adds a number (f6) is refused"
print("B ok: suggestions: soft skill, wrong category, missing skills, near-duplicate, a grounded rewrite")

# nothing changes until applied
assert _load_skeleton_raw()["skills"]["Frameworks & Libraries"] == ["React.js", "Teamwork"]
for x in r.suggestions:
    curator.apply(x)
s = _load_skeleton_raw()["skills"]
assert "Teamwork" not in sum(s.values(), []) and "Docker" in s["Developer Tools & Infra"] and "Docker" not in s["Languages"]
assert "Redis" in s["Databases"] and "FastAPI" in s["Frameworks & Libraries"]
fs = {f["id"]: f for f in _load_facts_raw()}
assert fs["f5"]["text"] == GOOD and len([i for i in ("f3", "f4") if i in fs]) == 1
assert "no longer" in curator.apply(rw[0]) or "edited since" in curator.apply(rw[0]), "a stale suggestion is refused politely"
r2 = curator.tidy(rewrite=lambda items, groups=None: {}, github=False, already_covered=lambda t, s: set())
assert not r2.fixed and r2.backup is None, "a tidy profile is left alone, no backup"
print("C ok: suggestions apply one by one; stale ones are refused; a second run finds nothing to fix")

# D the Telegram screen
from bot import tidy
from bot.services import db
db.init_db()
class Q:
    def __init__(self): self.edits = []
    async def edit_message_text(self, text, **kw): self.edits.append(text)
json.dump(facts, open(config.FACT_STORE_PATH, "w"))
_orig_tidy = curator.tidy
curator.tidy = lambda **kw: _orig_tidy(rewrite=fake_rewrite, github=False, already_covered=lambda t, s: set())
q = Q()
text, markup = asyncio.run(tidy.handle(q, "ty", []))
assert q.edits and "Checking" in q.edits[0] and "Fixed automatically" in text and "Suggestions" in text
ids = [b.callback_data for row in markup.inline_keyboard for b in row]
first = next(c for c in ids if c.startswith("xp:ta:"))
n = len(tidy.pending())
text, _ = asyncio.run(tidy.handle(q, "ta", [first.split(":")[2]]))
assert len(tidy.pending()) == n - 1 and "✅" in text
text, _ = asyncio.run(tidy.handle(q, "tX", [])); assert tidy.pending() == [] and "Skipped" in text
assert tidy.summary(curator.TidyReport()) == "" and "2 suggestions" in tidy.summary(curator.TidyReport(suggestions=[curator.Suggestion("a", "x", "t"), curator.Suggestion("b", "x", "t")]))
class JQ:
    def __init__(self): self.calls = []
    def run_daily(self, cb, time, days, name): self.calls.append((days, name))
app = types.SimpleNamespace(job_queue=JQ()); tidy.schedule(app); assert app.job_queue.calls == [((0,), "weekly-tidy")]
from service import events as EV
seen_ev = []; EV.bus.subscribe(lambda e: seen_ev.append(e))
asyncio.run(tidy.run("weekly"))
started = [e for e in seen_ev if e["kind"] == "run" and e["data"].get("state") == "started"][-1]
finished = [e for e in seen_ev if e["kind"] == "run" and e["data"].get("state") == "finished"][-1]
assert started["data"]["mode"] == "tidy" and started["data"]["trigger"] == "weekly" and finished["data"]["tidy"] is True
cur = [EV.describe(e) for e in seen_ev if e["agent"] == "curator"]
assert any("Checking your" in (x or "") for x in cur) and any("suggestion" in (x or "") or "tidy" in (x or "") for x in cur), cur
# your answers reach the dashboard, in the tidy-up's own run
seen_ev.clear()
rid = tidy.watchlist.get_setting("tidy_run", "")
tidy._store([curator.Suggestion("s1", "remove_skill", "x", {"item": "Nope"}), curator.Suggestion("s2", "remove_skill", "y", {"item": "Nope2"})])
asyncio.run(tidy.handle(Q(), "tx", ["s1"])); asyncio.run(tidy.handle(Q(), "tA", []))
rev = [e for e in seen_ev if e["agent"] == "curator" and e["data"].get("reviewed")]
assert [e["run_id"] for e in rev] == [rid, rid] and [e["data"]["remaining"] for e in rev] == [1, 0]
assert EV.describe(rev[-1]) == "All suggestions handled ✓" and "skipped" in EV.describe(rev[0]).lower()
print("D ok: /profile → 🧹 Tidy up: shows fixes and suggestions, ✅ applies one, skip all clears; runs weekly on Sunday")
# G with GitHub on: tools from repos are added automatically (reported), repo updates become suggestions
from service import github_evidence as GE
config.GITHUB_USERNAME = "u"
proj = _load_skeleton_raw()["project_entries"][0]
GE.sync_from_github = lambda **kw: [GE.Added("Go", "weather-cli", "Weather Cli", True)]
GE.project_updates = lambda sk, facts: [{"kind": "update_stack", "entry_id": proj["id"], "old": proj["tech_stack"], "new": proj["tech_stack"] + ", Go",
                                         "text": "update stack"}]
rg = _orig_tidy(rewrite=lambda items, groups=None: {}, already_covered=lambda t, s: set())
assert "Added from GitHub: Go (bullet point in Weather Cli, a new project from your repo)" in rg.fixed and rg.backup
st = next(x for x in rg.suggestions if x.kind == "update_stack")
assert curator.apply(st).startswith("Updated the tech stack") and _load_skeleton_raw()["project_entries"][0]["tech_stack"].endswith(", Go")
assert "changed since" in curator.apply(st), "applying a stale stack update is refused"
config.GITHUB_USERNAME = ""
print("G ok: with GitHub on, tools from repos are added (and reported) and tech stack updates are suggested")
# H: a tool already listed inside a broader skill is not suggested again; the AI catches what the catalog can't
from service import tools_catalog as TC
assert TC.covered_by("OpenAI API", ["LLM APIs (OpenAI, Anthropic, Gemini)"]) and not TC.mentions("Go", "Google Cloud") and TC.mentions("Go", "Go, Rust")
sk2 = _load_skeleton_raw(); sk2["skills"]["AI/ML"] = ["LLM APIs (OpenAI, Anthropic, Gemini)"]; sk2["skills"]["Frameworks & Libraries"] = ["Python web frameworks"]
from service.profile_data import _save_skeleton_raw
_save_skeleton_raw(sk2)
fx = _load_facts_raw()
fx.append({"id": "g1", "entry_id": E, "section": "experience", "text": "Called the OpenAI API from FastAPI services", "tech_tags": ["OpenAI API", "FastAPI"]})
json.dump(fx, open(config.FACT_STORE_PATH, "w"))
asked = []
def covered(tools, skills): asked.append(tools); return {"FastAPI"} if "FastAPI" in tools else set()
rh = _orig_tidy(rewrite=lambda items, groups=None: {}, github=False, already_covered=covered)
adds = {x.data["item"] for x in rh.suggestions if x.kind == "add_skill"}
assert "OpenAI API" not in adds, "OpenAI is already inside 'LLM APIs (OpenAI, ...)'"
assert "FastAPI" not in adds and asked and "OpenAI API" not in asked[0], "the AI is only asked about what the catalog couldn't settle"
print("H ok: skills inside a broader item (LLM APIs (OpenAI, ...)) count; the AI settles the rest")

# R: every project bullet point is reviewed, not only long or weak ones
P = _load_skeleton_raw()["project_entries"][0]["id"]
fx = _load_facts_raw(); fx.append({"id": "pp1", "entry_id": P, "section": "project", "text": "Made an app", "tech_tags": []})
json.dump(fx, open(config.FACT_STORE_PATH, "w"))
seen_ids = []
rr = _orig_tidy(rewrite=lambda items, groups=None: (seen_ids.extend(items), {"pp1": "Built an app"})[1], github=False, already_covered=lambda t, s: set())
assert "pp1" in seen_ids and any(x.kind == "rewrite" and x.data["fact_id"] == "pp1" for x in rr.suggestions)
print("R ok: every project bullet point is reviewed for wording")
# X: the review can remove a bullet point that isn't needed; job bullet points are only reworded when long or weak
fx = _load_facts_raw()
fx.append({"id": "v1", "entry_id": E, "section": "experience", "text": "Did various tasks for the team", "tech_tags": []})
fx.append({"id": "ok1", "entry_id": E, "section": "experience", "text": "Cut API latency by 40% with Redis caching", "tech_tags": ["Redis"]})
json.dump(fx, open(config.FACT_STORE_PATH, "w"))
got_groups = {}
def review(items, groups=None):
    got_groups.update(groups or {})
    return {"v1": {"remove": "too vague to tell a recruiter anything"}, "ok1": "Reduced API latency by 40% using Redis caching"}
rx = _orig_tidy(rewrite=review, github=False, already_covered=lambda t, s: set())
drop = [x for x in rx.suggestions if x.kind == "drop_bullet"]
assert [x.data["fact_id"] for x in drop] == ["v1"] and "too vague" in drop[0].text
assert not any(x.kind == "rewrite" and x.data["fact_id"] == "ok1" for x in rx.suggestions), "a fine, short job bullet point is not reworded"
assert "v1" in got_groups and " at " in got_groups["v1"], "the AI is told which entry each bullet point belongs to"
assert curator.apply(drop[0]) == "Removed it" and not any(f["id"] == "v1" for f in _load_facts_raw())
print("X ok: a bullet point that isn't needed can be removed (after ✅); fine job bullet points are left alone")
print("\nALL CURATOR TESTS PASSED")
