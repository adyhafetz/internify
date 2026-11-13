"""Offline test of reading GitHub for proof of tools. GitHub and the AI are replaced by fakes."""
from __future__ import annotations
import json, os, shutil, sys, tempfile
import httpx

sys.path.insert(0, os.path.abspath("."))
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
tmp = tempfile.mkdtemp()
from service import config, events
config.FACT_STORE_PATH, config.SKELETON_PATH = os.path.join(tmp, "facts.json"), os.path.join(tmp, "resume_skeleton.json")
config.EMBEDDING_CACHE_PATH = os.path.join(tmp, "cache.json")
shutil.copy("service/fact_store/facts.json", config.FACT_STORE_PATH)
sk = json.load(open("service/fact_store/resume_skeleton.json"))
sk["header"]["name"] = "Ady"
sk["project_entries"].append({"id": "proj_snap", "name": "SnapPick", "tech_stack": "React Native", "date": "2026"})
json.dump(sk, open(config.SKELETON_PATH, "w"))
from service import github_evidence as G
from service.models import JobPosting
from service.profile_data import _load_facts_raw, _load_skeleton_raw

# F: reading files
pkg = json.dumps({"dependencies": {"react": "18", "next": "14", "@supabase/supabase-js": "2", "left-pad": "1"}, "devDependencies": {"typescript": "5", "vitest": "1"}})
assert G.tools_in_file("package.json", pkg) >= {"React.js", "Next.js", "Supabase", "TypeScript", "Vitest", "JavaScript", "Node.js"}
assert "left-pad" not in str(G.tools_in_file("package.json", pkg)), "only well-known tools, never guesses"
assert G.tools_in_file("requirements.txt", "fastapi==0.1\npandas>=2 # data\n-e .\npsycopg2-binary\n") == {"Python", "FastAPI", "Pandas", "PostgreSQL"}
assert G.tools_in_file("pyproject.toml", '[project]\ndependencies = ["django>=5", "redis"]') >= {"Django", "Redis", "Python"}
assert G.tools_in_file("docker-compose.yml", "services:\n  db:\n    image: postgres:16\n") >= {"Docker", "Docker Compose", "PostgreSQL"}
assert G.tools_in_file("app.json", '{"expo": {"name": "x"}}') == {"Expo", "React Native"}
assert G.tools_in_file("package.json", "{not json") == set()
print("F ok: tools are read from package.json, requirements, pyproject, compose files and more; never guessed")

# I: the index (one API call for the list; files come from raw.githubusercontent.com)
calls = []
REPOS = [{"name": "snap-pick", "fork": False, "pushed_at": "2026-11-01", "default_branch": "main", "html_url": "https://github.com/u/snap-pick",
          "description": "Recycling app with YOLOv8 detection", "language": "TypeScript", "topics": ["docker", "expo"]},
         {"name": "someone-elses", "fork": True, "pushed_at": "2026-11-02"}]
FILES = {"snap-pick/main/package.json": json.dumps({"dependencies": {"react-native": "1", "expo": "1"}}),
         "snap-pick/main/Dockerfile": "FROM node:20", "snap-pick/main/README.md": "# SnapPick\nA recycling app. Runs locally with Docker."}
def fake_get(url, **kw):
    calls.append(url)
    req = httpx.Request("GET", url)
    if "api.github.com" in url: return httpx.Response(200, json=REPOS if kw.get("params", {}).get("page") == 1 else [], request=req)
    path = url.split("raw.githubusercontent.com/u/")[-1]
    return httpx.Response(200, text=FILES[path], request=req) if path in FILES else httpx.Response(404, request=req)
G._get = fake_get
idx = G.refresh_index("u")
api_calls = [c for c in calls if "api.github.com" in c]
assert len(api_calls) == 1, api_calls
assert list(idx["repos"]) == ["snap-pick"], "forks are left out"
tools = idx["repos"]["snap-pick"]["tools"]
assert {"React Native", "Expo", "Docker", "TypeScript"} <= set(tools) and "Dockerfile" in tools["Docker"] and "repo topic" in tools["Docker"]
n = len(calls); G.refresh_index("u"); assert len(calls) == n, "refreshed at most once a day"
n = len(calls); G.refresh_index("u", force=True); assert [c for c in calls[n:] if "raw." in c] == [], "unchanged repos are not downloaded again"
G._get = lambda url, **kw: (_ for _ in ()).throw(httpx.ConnectError("down"))
assert G.refresh_index("u", force=True)["repos"], "GitHub down: the old index is kept"
G._get = fake_get
print("I ok: one API call, forks skipped, cached for a day, unchanged repos not downloaded again, failures keep the old index")

# M: matching
ev = G.find_evidence("docker", idx)
assert ev and ev[0].tool == "Docker" and ev[0].repo == "snap-pick"
assert not G.find_evidence("Kubernetes", idx)
class P:
    def __init__(self, name): self.name = name
assert G.match_project("snap-pick", [P("Internify"), P("SnapPick")]).name == "SnapPick" and G.match_project("api", [P("SnapPick")]) is None
print("M ok: tools found by any spelling; repos matched to saved projects by name")

# B: drafting a bullet point is grounded
G._llm_bullet = lambda s, u: "Containerized SnapPick with Docker for a consistent local setup."
assert G.draft_bullet(ev[0], "SnapPick") == "Containerized SnapPick with Docker for a consistent local setup"
G._llm_bullet = lambda s, u: "Containerized SnapPick with Docker, serving 10,000 users"
assert G.draft_bullet(ev[0], "SnapPick") is None, "an invented number is refused"
G._llm_bullet = lambda s, u: "Improved the local setup of SnapPick"
assert G.draft_bullet(ev[0], "SnapPick") is None, "it must name the tool"
print("B ok: a drafted bullet point must name the tool and add nothing the repo doesn't say")

# C: the Archivist step: no question, skill + bullet point added, the user told
config.GITHUB_USERNAME = "u"
G._llm_bullet = lambda s, u: "Containerized SnapPick with Docker for a consistent local setup"
job = JobPosting(company="X", role="Intern", core_tech_stack=["Docker", "Kubernetes", "Python"])
seen = []
unsub = events.bus.subscribe(lambda e: seen.append(e))
rid = events.start_run(url="x")
res = G.check_github(job, ["Docker", "Kubernetes"])
assert [a.skill for a in res.answers] == ["Docker"] and res.answers[0].has and res.answers[0].fact is not None
assert res.added == ["Docker (snap-pick)"]
assert "Docker" in _load_skeleton_raw()["skills"]["Developer Tools & Infra"] or any("Docker" in v for v in _load_skeleton_raw()["skills"].values())
f = next(x for x in _load_facts_raw() if x["entry_id"] == "proj_snap")
assert f["text"].startswith("Containerized SnapPick with Docker") and f["source"] == "github:snap-pick" and "Docker" in f["tech_tags"]
says = [events.describe(e) for e in seen if e["agent"] == "archivist"]
assert any("Checking your GitHub" in (x or "") for x in says) and any("Found Docker in your repo snap-pick" in (x or "") for x in says), says
config.GITHUB_USERNAME = ""
assert G.check_github(job, ["Docker"]).answers == [], "no username: nothing is looked up"
print("C ok: a tool found in your repos is added with no question; Kubernetes (not found) is left for the usual question")
print("\nALL GITHUB TESTS PASSED")
