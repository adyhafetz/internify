"""Reading your public GitHub repos for proof of the tools you have used.

When a job wants a tool your saved experience doesn't mention, the Archivist looks here before asking you. If one of your
own repos (not a fork) uses it, the tool is added to your skills, and if that repo is one of your saved projects a short,
grounded bullet point is added to that project too: no question, and the Telegram message says what was added.

The index of your repos is kept in output/data/github_index.json and refreshed at most once a day. Only one GitHub API call
per 100 repos is needed (the rest are plain file downloads), so two copies on one server stay well inside GitHub's limits."""
from __future__ import annotations

import json
import logging
import os
import re
import time
import tomllib
from dataclasses import dataclass, field
from typing import Optional

import httpx

from service import config, events, tools_catalog

logger = logging.getLogger(__name__)

MAX_AGE = 24 * 3600
MAX_TOOLS_PER_RUN = 6
FILES = ["package.json", "requirements.txt", "pyproject.toml", "Pipfile", "environment.yml", "Dockerfile", "docker-compose.yml",
         "docker-compose.yaml", "compose.yaml", "go.mod", "Cargo.toml", "pom.xml", "build.gradle", "composer.json", "Gemfile",
         "pubspec.yaml", "app.json", "vercel.json", "README.md"]


def _get(url: str, **kw) -> httpx.Response:
    """One place for outgoing requests, so tests can replace it."""
    return httpx.get(url, timeout=20.0, follow_redirects=True, headers={"User-Agent": "internify", **kw.pop("headers", {})}, **kw)


# ---------------------------------------------------------------- reading one repo's files

def _python_names(text: str) -> set[str]:
    names = set()
    for line in text.splitlines():
        line = line.split("#")[0].strip()
        m = re.match(r"([A-Za-z0-9_.\-\[\]]+)", line)
        if m and not line.startswith("-"):
            names.add(m.group(1).split("[")[0].lower())
    return names


def tools_in_file(path: str, text: str) -> set[str]:
    """Proper tool names found in one file (only well-known tools, never guesses)."""
    found: set[str] = set()
    names: set[str] = set()
    low = path.lower()
    try:
        if low == "package.json" or low == "composer.json":
            data = json.loads(text)
            for k in ("dependencies", "devDependencies", "require", "require-dev", "peerDependencies"):
                names |= {n.lower() for n in (data.get(k) or {})}
            if low == "composer.json":
                found.add("PHP")
            elif "typescript" in names:
                found.add("TypeScript")
            if low == "package.json":
                found.add("JavaScript"); found.add("Node.js")
        elif low in ("requirements.txt", "pipfile", "environment.yml"):
            names |= _python_names(text); found.add("Python")
        elif low == "pyproject.toml":
            data = tomllib.loads(text)
            deps = list((data.get("project") or {}).get("dependencies") or [])
            deps += list(((data.get("tool") or {}).get("poetry") or {}).get("dependencies") or {})
            names |= _python_names("\n".join(deps)); found.add("Python")
        elif low == "dockerfile":
            found.add("Docker")
        elif low in ("docker-compose.yml", "docker-compose.yaml", "compose.yaml"):
            found |= {"Docker", "Docker Compose"}
            for img in re.findall(r"image:\s*['\"]?([a-z0-9._/-]+)", text, re.I):
                names.add(img.split("/")[-1])
        elif low == "go.mod":
            found.add("Go")
        elif low == "cargo.toml":
            found.add("Rust")
        elif low == "pom.xml":
            found |= {"Java", "Maven"}; names |= {"spring"} if "spring-boot" in text else set()
        elif low == "build.gradle":
            found |= {"Gradle"}; names |= {"spring"} if "springframework.boot" in text else set()
        elif low == "gemfile":
            found.add("Ruby"); names |= {m.lower() for m in re.findall(r"gem ['\"]([\w-]+)", text)}
        elif low == "pubspec.yaml":
            found |= {"Dart", "Flutter"} if "flutter" in text else {"Dart"}
        elif low == "app.json" and '"expo"' in text:
            found |= {"Expo", "React Native"}
        elif low == "vercel.json":
            found.add("Vercel")
    except Exception as err:   # an odd file never stops the index
        logger.info("Could not read %s: %s", path, err)
    for n in names:
        c = tools_catalog.canonical(n)
        if c:
            found.add(c)
    return found


# ---------------------------------------------------------------- the index

def _index_path() -> str:
    return os.path.join(os.path.dirname(config.FACT_STORE_PATH) or ".", "github_index.json")


def load_index() -> dict:
    try:
        with open(_index_path(), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def refresh_index(username: str, force: bool = False) -> dict:
    """Bring the index up to date (at most once a day unless forced). Only repos that changed are read again.
    Never raises: on any problem the previous index is kept."""
    idx = load_index()
    if not username:
        return idx
    if not force and idx.get("username") == username and time.time() - idx.get("checked", 0) < MAX_AGE:
        return idx
    repos_out = dict(idx.get("repos", {})) if idx.get("username") == username else {}
    try:
        listed = []
        for page in (1, 2, 3):
            r = _get(f"https://api.github.com/users/{username}/repos", params={"per_page": 100, "page": page, "type": "owner", "sort": "pushed"},
                     headers={"Accept": "application/vnd.github+json"})
            if r.status_code != 200:
                logger.info("GitHub answered %s for %s", r.status_code, username)
                return idx
            batch = r.json()
            listed += batch
            if len(batch) < 100:
                break
        fresh: dict[str, dict] = {}
        for repo in listed:
            if repo.get("fork"):
                continue
            name, pushed = repo["name"], repo.get("pushed_at", "")
            old = repos_out.get(name)
            if old and old.get("pushed_at") == pushed:
                fresh[name] = old
                continue
            branch = repo.get("default_branch") or "main"
            tools: dict[str, list[str]] = {}
            readme = ""
            for path in FILES:
                fr = _get(f"https://raw.githubusercontent.com/{username}/{name}/{branch}/{path}")
                if fr.status_code != 200:
                    continue
                if path == "README.md":
                    readme = fr.text[:12000]
                    continue
                for t in tools_in_file(path, fr.text):
                    tools.setdefault(t, []).append(path)
            lang = tools_catalog.canonical(repo.get("language") or "")
            if lang:
                tools.setdefault(lang, []).append("main language")
            for topic in repo.get("topics") or []:
                c = tools_catalog.canonical(topic)
                if c:
                    tools.setdefault(c, []).append("repo topic")
            fresh[name] = {"name": name, "url": repo.get("html_url", ""), "description": repo.get("description") or "",
                           "pushed_at": pushed, "tools": tools, "readme": readme}
        idx = {"username": username, "checked": time.time(), "repos": fresh}
        os.makedirs(os.path.dirname(_index_path()) or ".", exist_ok=True)
        with open(_index_path(), "w", encoding="utf-8") as f:
            json.dump(idx, f, indent=1)
    except Exception as err:
        logger.warning("GitHub index not refreshed: %s", err)
    return idx


@dataclass
class Evidence:
    tool: str
    repo: str
    url: str
    files: list[str]
    description: str = ""
    readme: str = ""


def find_evidence(term: str, idx: dict) -> list[Evidence]:
    """Your repos that use this tool, most recently changed first."""
    want = tools_catalog.canonical(term) or term.strip()
    out = []
    for repo in sorted(idx.get("repos", {}).values(), key=lambda r: r.get("pushed_at", ""), reverse=True):
        for tool, files in repo.get("tools", {}).items():
            if tools_catalog.key(tool) == tools_catalog.key(want):
                out.append(Evidence(tool, repo["name"], repo.get("url", ""), files, repo.get("description", ""), repo.get("readme", "")))
    return out


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def names_match(repo: str, project_name: str) -> bool:
    """"snap-pick" matches "SnapPick"; "internify-bot" matches "Internify"."""
    r, n = _norm(repo), _norm(project_name)
    return bool(n and r and (n == r or (len(n) >= 4 and (n in r or r in n))))


def match_project(repo: str, projects: list) -> Optional[object]:
    """The saved project this repo belongs to, by name, or None."""
    return next((p for p in projects if names_match(repo, p.name)), None)


# ---------------------------------------------------------------- a grounded bullet point from a repo

def _llm_bullet(system: str, user: str) -> str:
    """Blocking. One short AI answer (tests replace this)."""
    from pydantic import BaseModel
    from service.clients import instructor_client

    class _Bullet(BaseModel):
        text: str
    r: _Bullet = instructor_client.chat.completions.create(
        model=config.GENERATOR_MODEL, response_model=_Bullet,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
    return r.text


def draft_bullet(ev: Evidence, project_name: str) -> Optional[str]:
    """One sentence about using the tool in that project, written only from the repo's own description and README.
    Refused (None) if it mentions any number, tool or name the repo didn't, or doesn't name the tool."""
    from service.latex.fit import adds_new_facts
    source = f"{project_name} {ev.repo} {ev.tool} {ev.description} {ev.readme}"
    try:
        text = _llm_bullet(
            "Write ONE resume bullet point (under 170 characters, starting with a past-tense verb) saying how this project used "
            f"{ev.tool}. Use only facts in the repository text below. Do not invent numbers, results, users or other tools. "
            "If the text says little, keep it simple, for example: 'Containerized the app with Docker for a consistent local setup'.",
            f"Project: {project_name}\nTool: {ev.tool} (found in: {', '.join(ev.files)})\n"
            f"Description: {ev.description}\nREADME:\n{ev.readme[:3000]}").strip().rstrip(".")
    except Exception as err:
        logger.info("No bullet drafted: %s", err)
        return None
    if not text or len(text) > 200 or adds_new_facts(source, text) or not tools_catalog.mentions(ev.tool, text):
        return None
    return text


# ---------------------------------------------------------------- adding a tool, always backed by one bullet point

MAX_REPO_AGE_DAYS = 730


def _usable(repo: dict, username: str) -> bool:
    """A repo worth writing about: yours, has some text to go on, and changed in the last two years."""
    from datetime import datetime, timezone
    if repo.get("name", "").lower() in (username.lower(), f"{username.lower()}.github.io"):
        return False
    if not (repo.get("description") or repo.get("readme")):
        return False
    try:
        pushed = datetime.fromisoformat(repo.get("pushed_at", "").replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - pushed).days <= MAX_REPO_AGE_DAYS
    except Exception:
        return True


def pretty_name(repo: str) -> str:
    return " ".join(w[:1].upper() + w[1:] for w in re.split(r"[-_]+", repo) if w) or repo


def _month(iso: str) -> str:
    from datetime import datetime
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%b %Y")
    except Exception:
        return ""


@dataclass
class Added:
    tool: str
    repo: str
    project: str
    new_project: bool
    fact: object = None          # the saved FactBullet

    def label(self) -> str:
        return f"{self.tool} (bullet point in {self.project}{', a new project from your repo' if self.new_project else ''})"


def add_tool_with_bullet(tool: str, idx: dict, username: str) -> Optional[Added]:
    """Add a tool to your skills ONLY together with one grounded bullet point that shows it: under the saved project
    that matches the repo, or under a new project made from that repo. Returns None (nothing saved) if no bullet
    point could be written from what the repo says."""
    from service.fact_store.store import load_skeleton
    from service.models import FactBullet
    from service.profile_data import (AddProjectRequest, AddSkillsRequest, NewFactBullet, _load_facts_raw, _save_facts_raw,
                                      add_fact_to_entry, add_project, add_skills)
    skeleton = load_skeleton()
    had_skill = tools_catalog.covered_by(tool, [i for v in skeleton.skills.values() for i in v])
    for ev in find_evidence(tool, idx):
        repo = idx["repos"].get(ev.repo, {})
        if not _usable(repo, username):
            continue
        project = match_project(ev.repo, skeleton.project_entries)
        name = project.name if project else pretty_name(ev.repo)
        text = draft_bullet(ev, name)
        if not text:
            continue
        bullet = NewFactBullet(text=text, tech_tags=[ev.tool])
        if project:
            fact_id = add_fact_to_entry(project.id, bullet).fact_ids[0]
        else:
            stack = [ev.tool] + [t for t in repo.get("tools", {}) if t != ev.tool][:4]
            fact_id = add_project(AddProjectRequest(name=name, tech_stack=", ".join(stack), date=_month(repo.get("pushed_at", "")),
                                                    bullets=[bullet])).fact_ids[0]
        raw = _load_facts_raw()
        fact = None
        for f in raw:
            if f["id"] == fact_id:
                f["source"] = f"github:{ev.repo}"
                fact = FactBullet(**{k: f[k] for k in ("id", "entry_id", "section", "text", "tech_tags")})
        _save_facts_raw(raw)
        add_skills(AddSkillsRequest(category=tools_catalog.category(ev.tool) or tools_catalog.DEV, items=[ev.tool]))
        entry_id = next((f["entry_id"] for f in raw if f["id"] == fact_id), "")
        _log_added({"tool": ev.tool, "repo": ev.repo, "project": name, "entry_id": entry_id, "new_project": project is None,
                    "fact_id": fact_id, "skill_added": not had_skill, "at": time.strftime("%d %b")})
        return Added(ev.tool, ev.repo, name, project is None, fact)
    return None


# ---------------------------------------------------------------- what was added, and taking one back

def _added_path() -> str:
    return os.path.join(os.path.dirname(config.FACT_STORE_PATH) or ".", "github_added.json")


def recent_added(limit: int = 10) -> list[dict]:
    """The most recent additions from GitHub (newest first), so each can be taken back with one tap."""
    try:
        with open(_added_path(), encoding="utf-8") as f:
            return list(reversed(json.load(f)))[:limit]
    except Exception:
        return []


def _log_added(rec: dict) -> None:
    items = list(reversed(recent_added(200)))
    items.append(rec)
    os.makedirs(os.path.dirname(_added_path()) or ".", exist_ok=True)
    with open(_added_path(), "w", encoding="utf-8") as f:
        json.dump(items[-200:], f, indent=1)


def undo_added(fact_id: str) -> str:
    """Take back one addition from GitHub: its bullet point; the project too if it was made for it and is now empty;
    the skill too if it was added with it and no other bullet point mentions it."""
    from service.profile_data import _load_facts_raw, _load_skeleton_raw, _save_skeleton_raw, delete_fact, delete_project
    items = list(reversed(recent_added(200)))
    rec = next((r for r in items if r.get("fact_id") == fact_id), None)
    if rec is None:
        return "That was already taken back"
    facts = _load_facts_raw()
    if any(f["id"] == fact_id for f in facts):
        delete_fact(fact_id)
    facts = _load_facts_raw()
    removed = [f"the bullet point in {rec['project']}"]
    if rec.get("new_project") and not any(f["entry_id"] == rec["entry_id"] for f in facts):
        sk = _load_skeleton_raw()
        if any(p["id"] == rec["entry_id"] for p in sk.get("project_entries", [])):
            delete_project(rec["entry_id"])
            removed.append(f"the project {rec['project']}")
    if rec.get("skill_added") and not any(tools_catalog.mentions(rec["tool"], f["text"]) for f in _load_facts_raw()):
        sk = _load_skeleton_raw()
        for cat, items_ in sk.get("skills", {}).items():
            keep = [i for i in items_ if tools_catalog.key(i) != tools_catalog.key(rec["tool"])]
            if len(keep) != len(items_):
                sk["skills"][cat] = keep
                removed.append(f"the skill {rec['tool']}")
        _save_skeleton_raw(sk)
    items = [r for r in items if r.get("fact_id") != fact_id]
    with open(_added_path(), "w", encoding="utf-8") as f:
        json.dump(items, f, indent=1)
    return "Took back " + ", ".join(removed)


def missing_from_profile(idx: dict, username: str, skills: dict[str, list[str]], limit: int) -> list[str]:
    """Tools used in your usable repos that your skills list doesn't have, the most widely used first."""
    items = [i for v in skills.values() for i in v]
    count: dict[str, int] = {}
    for repo in idx.get("repos", {}).values():
        if not _usable(repo, username):
            continue
        for tool in repo.get("tools", {}):
            if not tools_catalog.covered_by(tool, items):
                count[tool] = count.get(tool, 0) + 1
    return sorted(count, key=lambda t: (-count[t], t))[:limit]


def sync_from_github(limit: int = 8, force: bool = False) -> list[Added]:
    """Used by the Curator: add tools from your repos that your profile is missing, each with one bullet point."""
    username = config.GITHUB_USERNAME
    if not username:
        return []
    from service.profile_data import _load_skeleton_raw
    idx = refresh_index(username, force=force)
    out: list[Added] = []
    for tool in missing_from_profile(idx, username, _load_skeleton_raw().get("skills", {}), limit):
        try:
            added = add_tool_with_bullet(tool, idx, username)
        except Exception as err:
            logger.info("Could not add %s from GitHub: %s", tool, err)
            continue
        if added:
            out.append(added)
    return out


# ---------------------------------------------------------------- refreshing a project from its repo (suggestions)

def _llm_refresh(system: str, user: str) -> dict[str, str]:
    """Blocking. key -> new text (tests replace this)."""
    from pydantic import BaseModel
    from service.clients import instructor_client

    class _Item(BaseModel):
        key: str
        text: str

    class _Out(BaseModel):
        items: list[_Item]
    r: _Out = instructor_client.chat.completions.create(model=config.GENERATOR_MODEL, response_model=_Out,
                                                        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
    return {i.key: i.text.strip() for i in r.items}


def project_updates(skeleton: dict, facts: list[dict], max_projects: int = 20) -> list[dict]:
    """For each saved project that has a repo: tools the repo uses that its tech stack line doesn't show, and bullet points
    rewritten to say more precisely what the repo shows. Returned as plain suggestions for the Curator (nothing is saved).
    A rewrite may only use facts from the old bullet point or the repo."""
    from service.latex.fit import adds_new_facts
    username = config.GITHUB_USERNAME
    if not username:
        return []
    idx = load_index()
    if idx.get("username") != username:
        idx = refresh_index(username)
    out: list[dict] = []
    done = 0
    for p in skeleton.get("project_entries", []):
        repo = next((r for r in idx.get("repos", {}).values() if names_match(r["name"], p["name"])), None)
        if not repo or done >= max_projects:
            continue
        done += 1
        stack = [x.strip() for x in p.get("tech_stack", "").split(",") if x.strip()]
        have = {tools_catalog.key(tools_catalog.canonical(x) or x) for x in stack}
        extra = [t for t in repo.get("tools", {}) if tools_catalog.key(t) not in have and t not in ("JavaScript", "Node.js")][: max(0, 6 - len(stack))]
        if extra:
            new_stack = ", ".join(stack + extra)
            out.append({"kind": "update_stack", "entry_id": p["id"], "old": p.get("tech_stack", ""), "new": new_stack,
                        "text": f"{p['name']}: tech stack “{p.get('tech_stack', '')}” → “{new_stack}” (from your repo {repo['name']})"})
        mine = {f["id"]: f["text"] for f in facts if f["entry_id"] == p["id"]}
        if not mine:
            continue
        source = f"{p['name']} {repo['name']} {repo.get('description', '')} {repo.get('readme', '')} {' '.join(repo.get('tools', {}))}"
        try:
            new = _llm_refresh(
                "You review EVERY resume bullet point of a software project against its repository. Rewrite a bullet when it "
                "can be clearer, stronger or more accurate: a strong past-tense verb first, the main technologies named as the "
                "repository shows them, the result or purpose stated, one sentence under 190 characters. If the bullet states a "
                "figure (a count, a version, a feature) that the repository now states differently, use the repository's current "
                "figure. Leave out of your answer any bullet that is already good. Use ONLY facts in that bullet or the "
                "repository text: never invent numbers, users or results.",
                f"Project: {p['name']}\nRepository: {repo['name']} - {repo.get('description', '')}\nTools found: "
                f"{', '.join(repo.get('tools', {}))}\nREADME:\n{repo.get('readme', '')[:8000]}\n\nCurrent bullet points:\n"
                + "\n".join(f"{k}: {v}" for k, v in mine.items()))
        except Exception as err:
            logger.info("Project refresh skipped for %s: %s", p["name"], err)
            continue
        for fid, text in new.items():
            old = mine.get(fid)
            if not old or not text or text == old or len(text) > 200 or adds_new_facts(f"{old} {source}", text):
                continue
            out.append({"kind": "rewrite", "fact_id": fid, "old": old, "new": text,
                        "text": f"{p['name']} (from your repo {repo['name']}): reword\n“{old}”\n→ “{text}”"})
    return out


# ---------------------------------------------------------------- the step the Archivist runs

@dataclass
class GithubResult:
    answers: list = field(default_factory=list)       # SkillAnswer objects, like the user had said yes
    added: list[str] = field(default_factory=list)    # "Docker (snappick)"


def concrete_missing(job, missing: list[str]) -> list[str]:
    keys = {m.lower() for m in missing}
    out: list[str] = []
    for term in job.core_tech_stack:
        if term.lower() in keys and term not in out:
            out.append(term)
    return out[:MAX_TOOLS_PER_RUN]


def check_github(job, missing: list[str]) -> GithubResult:
    """Look for proof of the missing tools in your repos. A tool is added only with one bullet point that shows it; what
    can't be backed this way is left for the usual question. Never raises."""
    res = GithubResult()
    username = config.GITHUB_USERNAME
    terms = concrete_missing(job, missing)
    if not username or not terms:
        return res
    try:
        from service.models import SkillAnswer
        events.emit("archivist", events.PROGRESS, f"Checking your GitHub for {', '.join(terms)}", github="checking", skills=terms)
        idx = refresh_index(username)
        for term in terms:
            added = add_tool_with_bullet(term, idx, username)
            if not added:
                continue
            res.answers.append(SkillAnswer(skill=term, has=True, fact=added.fact))
            res.added.append(added.label())
            events.emit("archivist", events.PROGRESS, f"Found {added.tool} in your repo {added.repo}", github="found",
                        skill=added.tool, repo=added.repo, bullet=True, project=added.project, new_project=added.new_project)
    except Exception as err:
        logger.warning("GitHub check skipped: %s", err)
    return res
