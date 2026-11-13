"""Offline test of reading GitHub for proof of tools. GitHub and the AI are replaced by fakes."""
from __future__ import annotations
import json, os, shutil, sys, tempfile, time
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

# C: the Archivist step: no question, skill + one bullet point added, the user told
config.GITHUB_USERNAME = "u"
REPOS[0]["pushed_at"] = time.strftime("%Y-%m-%dT00:00:00Z")
idx = G.refresh_index("u", force=True)
G._llm_bullet = lambda s, u: "Containerized SnapPick with Docker for a consistent local setup"
job = JobPosting(company="X", role="Intern", core_tech_stack=["Docker", "Kubernetes", "Python"])
seen = []
events.bus.subscribe(lambda e: seen.append(e))
events.start_run(url="x")
res = G.check_github(job, ["Docker", "Kubernetes"])
assert [a.skill for a in res.answers] == ["Docker"] and res.answers[0].has and res.answers[0].fact is not None
assert res.added == ["Docker (bullet point in SnapPick)"], res.added
assert any("Docker" in v for v in _load_skeleton_raw()["skills"].values())
f = next(x for x in _load_facts_raw() if x["entry_id"] == "proj_snap")
assert f["text"].startswith("Containerized SnapPick with Docker") and f["source"] == "github:snap-pick" and "Docker" in f["tech_tags"]
says = [events.describe(e) for e in seen if e["agent"] == "archivist"]
assert any("Checking your GitHub" in (x or "") for x in says) and any("added it with a bullet point in SnapPick" in (x or "") for x in says), says
print("C ok: a tool found in your repos is added with a bullet point and no question; Kubernetes (not found) is left for the question")

# D: no bullet point, no skill
G._llm_bullet = lambda s, u: "Improved the setup a lot"           # doesn't name the tool -> refused
before = json.dumps(_load_skeleton_raw()["skills"])
r2 = G.check_github(JobPosting(company="X", role="I", core_tech_stack=["Expo"]), ["Expo"])
assert r2.answers == [] and json.dumps(_load_skeleton_raw()["skills"]) == before, "a skill is never added without a bullet point to back it"
config.GITHUB_USERNAME = ""
assert G.check_github(job, ["Docker"]).answers == [], "no username: nothing is looked up"
config.GITHUB_USERNAME = "u"
print("D ok: when no bullet point can be written, the skill isn't added either")

# E: the Curator's sync: tools from repos you never listed, each with a bullet point; a repo with no saved project becomes one
REPOS.append({"name": "weather-cli", "fork": False, "pushed_at": time.strftime("%Y-%m-%dT00:00:00Z"), "default_branch": "main",
              "html_url": "https://github.com/u/weather-cli", "description": "A command line weather app in Go", "language": "Go", "topics": []})
FILES["weather-cli/main/go.mod"] = "module weather"
REPOS.append({"name": "u", "fork": False, "pushed_at": time.strftime("%Y-%m-%dT00:00:00Z"), "default_branch": "main",
              "html_url": "https://github.com/u/u", "description": "My profile", "language": "Rust", "topics": []})
FILES["u/main/Cargo.toml"] = "[package]"
G.refresh_index("u", force=True)
def bullet(system, user):
    tool = user.split("Tool: ")[1].split(" (")[0]
    project = user.split("Project: ")[1].split("\n")[0]
    return f"Built {project} with {tool}"
G._llm_bullet = bullet
added = G.sync_from_github()
labels = [a.label() for a in added]
assert any(l.startswith("Go (bullet point in Weather Cli, a new project") for l in labels), labels
assert not any(l.startswith("Rust") for l in labels), "your profile-readme repo is not a project"
sk = _load_skeleton_raw()
weather = next(p for p in sk["project_entries"] if p["name"] == "Weather Cli")
assert weather["tech_stack"].startswith("Go") and weather["date"]
assert any(f["entry_id"] == weather["id"] and "Go" in f["text"] for f in _load_facts_raw())
have = {s_.lower() for v in sk["skills"].values() for s_ in v}
assert "go" in have and "expo" in have and "react native" in have, have
assert G.sync_from_github() == [], "nothing left to add the second time"
rec = G.recent_added()
assert [r["tool"] for r in rec][:3] == ["React Native", "Go", "Expo"] and rec[1]["new_project"] and not rec[0]["new_project"], rec
go = next(r for r in rec if r["tool"] == "Go")
msg = G.undo_added(go["fact_id"])
assert "the project Weather Cli" in msg and "the skill Go" in msg, msg
sk2 = _load_skeleton_raw()
assert not any(p["name"] == "Weather Cli" for p in sk2["project_entries"]) and not any(i == "Go" for v in sk2["skills"].values() for i in v)
assert G.undo_added(go["fact_id"]) == "That was already taken back"
expo = next(r for r in G.recent_added() if r["tool"] == "Expo")
msg = G.undo_added(expo["fact_id"])
assert "SnapPick" in msg and "the project" not in msg and any(p["name"] == "SnapPick" for p in _load_skeleton_raw()["project_entries"]), "a saved project is never removed"
print("T ok: one tap takes an addition back: its bullet point, a project made for it, and the skill if nothing else needs it")
print("E ok: the Curator adds every tool from your repos, each with one bullet point:", labels)

# U: refreshing a saved project from its repo (suggestions only)
facts_now, sk_now = _load_facts_raw(), _load_skeleton_raw()
snap_fact = next(f for f in facts_now if f["entry_id"] == "proj_snap")
G._llm_refresh = lambda s, u: {snap_fact["id"]: "Containerized SnapPick, a recycling app, with Docker for a consistent local setup",
                               "nope": "x"}
ups = G.project_updates(sk_now, facts_now)
kinds = {u["kind"] for u in ups}
assert "rewrite" in kinds and all(u["new"] != u["old"] for u in ups)
G._llm_refresh = lambda s, u: {snap_fact["id"]: "Containerized SnapPick with Docker for 50,000 users"}
assert not [u for u in G.project_updates(sk_now, facts_now) if u["kind"] == "rewrite"], "a rewrite that invents a number is refused"
print("U ok: project bullet points can be reworded from the repo, never with invented facts")
print("\nALL GITHUB TESTS PASSED")
