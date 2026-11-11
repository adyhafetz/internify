"""Offline test of fitting the resume on one page. The compiler, the page count and the AI are replaced by fakes."""
from __future__ import annotations
import math, os, re, sys, tempfile

sys.path.insert(0, os.path.abspath("."))
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
from service.fact_store.store import load_skeleton
from service.latex import fit as F
from service.latex.compile import render_tex
from service.models import (ExperienceEntryMeta, FactBullet, FactBulletWithScore, GeneratedResume, JobPosting, LeadershipEntry,
                            ProjectEntryMeta, ResumeBullet)

tmp = tempfile.mkdtemp()
job = JobPosting(company="Stripe", role="Backend Intern", core_tech_stack=["Python", "FastAPI", "PostgreSQL"], hard_requirements=["REST APIs"], ats_keywords=["Docker"])

def make():
    sk = load_skeleton().model_copy(deep=True)
    sk.experience_entries = [ExperienceEntryMeta(id="e1", title="Intern", dates="2024", company="Acme", location="KL")]
    sk.project_entries = [ProjectEntryMeta(id="p1", name="Alpha", tech_stack="Python", date="2025"), ProjectEntryMeta(id="p2", name="Beta", tech_stack="Go", date="2025")]
    sk.leadership = [LeadershipEntry(title="Python club lead", dates="2024", org="Uni", location="KL", bullets=["Taught Python to 40 students every week for a semester across the faculty"]),
                     LeadershipEntry(title="Charity run", dates="2023", org="Uni", location="KL", bullets=["Organised a fun run raising money for a local shelter with many volunteers helping out"])]
    long = "Built REST APIs with FastAPI and PostgreSQL that served 2,000 daily requests, adding caching, input validation, and detailed structured logging across all services so that the whole team could debug production issues faster than before"
    bullets = [("e1", "f1", long), ("e1", "f2", "Containerized with Docker"), ("e1", "f3", long.replace("Built", "Maintained")),
               ("p1", "f4", "Made a Python tool that cleans data " + "x" * 120), ("p2", "f5", "Wrote a Go game " + "y" * 120)]
    res = GeneratedResume(bullets=[ResumeBullet(entry_id=e, fact_id=f, text=t) for e, f, t in bullets], selected_courses={}, selected_skills={})
    scores = {"f1": 1.2, "f2": 0.9, "f3": 0.5, "f4": 0.8, "f5": 0.1}
    facts = [FactBulletWithScore(fact=FactBullet(id=f, entry_id=e, section="experience", text=t, tech_tags=[]), similarity=scores[f], keyword_boost=0.0) for e, f, t in bullets]
    return sk, res, facts

def est_lines(tex: str) -> int:
    items = re.findall(r"\\resumeItem\{(.*?)\}\n", tex)
    return sum(math.ceil(len(i) / 100) + 1 for i in items) + 3 * len(re.findall(r"\\resume(?:Subheading|ProjectHeading)", tex)) + 12

class World:
    def __init__(self, cap): self.cap, self.builds, self.last_est, self.tex = cap, 0, 0, ""
    def compile(self, resume, skeleton, base):
        self.builds += 1; self.tex = render_tex(resume, skeleton); self.last_est = est_lines(self.tex)
        path = os.path.join(tmp, base + ".pdf"); open(path, "w").write("x"); return path
    def pages(self, path): return 2 if self.last_est > self.cap else 1
    def over(self, path): return max(1, self.last_est - self.cap)

def run(cap, shorten=lambda items, limit: {}, pages=None):
    sk, res, facts = make(); w = World(cap)
    before = (sk.model_dump(), res.model_dump())
    r = F.fit_one_page(job, facts, res, sk, "t", compile_fn=w.compile, pages_fn=pages or w.pages, overflow_fn=w.over, shorten_fn=shorten)
    assert (sk.model_dump(), res.model_dump()) == before, "your saved profile and the original draft are never changed"
    return r, w

def cut(items, limit): return {k: v[:limit - 5].rsplit(" ", 1)[0] for k, v in items.items()}

def stage(shorten_pass=False, lead=True):
    sk, res, _ = make()
    if lead: sk.leadership = [e for e in sk.leadership if "Python" in e.title]
    if shorten_pass:
        for b in res.bullets: b.text = cut({"k": b.text}, 150)["k"]
        for e in sk.leadership: e.bullets = [cut({"k": t}, 150)["k"] for t in e.bullets]
    return est_lines(render_tex(res, sk))
est_full, est_lead, est_short = stage(lead=False), stage(), stage(True)
assert est_full > est_lead > est_short, (est_full, est_lead, est_short)

# A fits at once
r, w = run(cap=999); assert r.fits and r.note == "" and w.builds == 1 and r.shortened == 0
r, w = run(cap=1, pages=lambda p: None); assert r.fits and r.note == "" and w.builds == 1       # page count unknown: leave it alone
print("A ok: a resume that already fits is untouched")

# B leadership that does not relate to the job goes first (and only that)
r, w = run(cap=est_lead)
assert r.fits and r.dropped == ["leadership: Charity run"] and r.shortened == 0 and "Python club lead" in w.tex and "Charity run" not in w.tex, r
assert "left out 1 less relevant item" in r.note
print("B ok: unrelated leadership is left out first, related leadership stays")

# C tightening long bullets (leadership too) is enough
r, w = run(cap=est_short + 3, shorten=cut)
assert r.fits and r.shortened >= 2 and "shortened" in r.note and r.dropped == ["leadership: Charity run"], r
assert max(len(t) for t in re.findall(r"\\resumeItem\{(.*?)\}\n", w.tex)) <= 190
print("C ok: long bullets are tightened and that is enough:", r.note)

# D a shortened bullet that adds a tool or number is refused
def lying(items, limit): return {k: "Built REST APIs with Kubernetes and Redis, 99% uptime" for k in items}
r, w = run(cap=est_short + 3, shorten=lying)
assert r.shortened == 0 and "Kubernetes" not in w.tex and "99%" not in w.tex
assert F.adds_new_facts("Built APIs with FastAPI", "Built APIs with FastAPI and Redis") and not F.adds_new_facts("Built REST APIs with FastAPI serving 2,000 users", "Built FastAPI REST APIs for 2,000 users")
print("D ok: a shorter text that adds a number, tool or name is refused")

# E still too long: the least relevant things go first (by relevance across the resume), the job stays
r, w = run(cap=est_short - 4, shorten=cut)
assert r.fits and r.dropped[0] == "leadership: Charity run" and r.dropped[1] == "entry: p2", r.dropped      # relevance 0.1 is the weakest
assert "Wrote a Go game" not in w.tex and "Built REST" in w.tex, "the most relevant bullet survives"
assert re.search(r"\\resumeSubheading\s*\{Intern\}", w.tex), "the work experience entry is never dropped"
print("E ok: the least relevant items go first, the job stays:", r.note)

# F cannot fit: say so, still build a valid resume
r, w = run(cap=5, shorten=cut)
assert not r.fits and "couldn't get it onto one page" in r.note and re.search(r"\\resumeSubheading\s*\{Intern\}", w.tex)
check = [l.strip() for l in w.tex.splitlines() if l.strip()]
for i, l in enumerate(check[:-1]):
    if l == r"\resumeItemListStart": assert check[i + 1].startswith(r"\resumeItem{")
print("F ok: when it cannot fit, it says so and still builds a valid resume")
print("\nALL FIT TESTS PASSED")
