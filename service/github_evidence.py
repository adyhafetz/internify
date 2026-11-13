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
                    readme = fr.text[:4000]
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


def match_project(repo: str, projects: list) -> Optional[object]:
    """The saved project this repo belongs to, by name ("snap-pick" matches "SnapPick"), or None."""
    r = _norm(repo)
    for p in projects:
        n = _norm(p.name)
        if n and r and (n == r or (len(n) >= 4 and (n in r or r in n))):
            return p
    return None


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
    """Look for proof of the missing tools in your repos and save what is found. Never raises."""
    res = GithubResult()
    username = config.GITHUB_USERNAME
    terms = concrete_missing(job, missing)
    if not username or not terms:
        return res
    try:
        from service.fact_store.store import load_skeleton
        from service.models import FactBullet, SkillAnswer
        from service.profile_data import AddSkillsRequest, NewFactBullet, _load_facts_raw, _save_facts_raw, add_fact_to_entry, add_skills
        events.emit("archivist", events.PROGRESS, f"Checking your GitHub for {', '.join(terms)}", github="checking", skills=terms)
        idx = refresh_index(username)
        skeleton = load_skeleton()
        for term in terms:
            found = find_evidence(term, idx)
            if not found:
                continue
            tool = found[0].tool
            add_skills(AddSkillsRequest(category=tools_catalog.category(tool) or tools_catalog.DEV, items=[tool]))
            fact = None
            for ev in found:
                project = match_project(ev.repo, skeleton.project_entries)
                if project is None:
                    continue
                text = draft_bullet(ev, project.name)
                if not text:
                    continue
                resp = add_fact_to_entry(project.id, NewFactBullet(text=text, tech_tags=[tool]))
                raw = _load_facts_raw()
                for f in raw:
                    if f["id"] == resp.fact_ids[0]:
                        f["source"] = f"github:{ev.repo}"
                        fact = FactBullet(**{k: f[k] for k in ("id", "entry_id", "section", "text", "tech_tags")})
                _save_facts_raw(raw)
                break
            res.answers.append(SkillAnswer(skill=term, has=True, fact=fact))
            res.added.append(f"{tool} ({found[0].repo})")
            events.emit("archivist", events.PROGRESS, f"Found {tool} in your repo {found[0].repo}", github="found",
                        skill=tool, repo=found[0].repo, bullet=bool(fact))
    except Exception as err:
        logger.warning("GitHub check skipped: %s", err)
    return res
