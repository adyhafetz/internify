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
def fake_rewrite(items):
    out = {}
    if "f5" in items: out["f5"] = GOOD
    if "f6" in items: out["f6"] = "Shipped 12 features with the team"          # adds a number: must be refused
    return out

r = curator.tidy(rewrite=fake_rewrite)
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
r2 = curator.tidy(rewrite=lambda items: {})
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
curator.tidy = lambda: _orig_tidy(rewrite=fake_rewrite)
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
print("D ok: /profile → 🧹 Tidy up: shows fixes and suggestions, ✅ applies one, skip all clears; runs weekly on Sunday")
print("\nALL CURATOR TESTS PASSED")
