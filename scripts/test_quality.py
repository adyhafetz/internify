"""Offline test of resume quality: requirement matching, the per-job minimum in retrieval, the word-for-word grounding
check, the score shown to you, the Scribe/Critic loop keeping its best draft, protecting jobs when trimming to one page,
learning from interviews, and cutting long job pages without losing the requirements. The AI is faked."""
from __future__ import annotations
import os, sys, tempfile

sys.path.insert(0, os.path.abspath("."))
tmp = tempfile.mkdtemp()
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
os.environ["OUTCOMES_PATH"] = os.path.join(tmp, "outcomes.json")

from service import config, matching, outcomes
from service.models import (CriticFeedback, EducationEntry, ExperienceEntryMeta, FactBullet, FactBulletWithScore, GeneratedResume,
                            JobPosting, ProjectEntryMeta, ResumeBullet, ResumeHeader, ResumeSkeleton)

job = JobPosting(company="Stripe", role="Backend Intern", core_tech_stack=["Python", "Postgres", "Docker", "Kubernetes", "JS"],
                 hard_requirements=["Bachelor's degree in Computer Science", "REST APIs", "Strong communication skills"],
                 ats_keywords=["backend"])

# ---- 1. matching: aliases, whole words, phrases, soft skills left out
reqs = matching.job_requirements(job)
assert "Strong communication skills" not in reqs and len(reqs) == 7, reqs
assert matching.term_met("Postgres", "Designed a PostgreSQL schema")
assert matching.term_met("JS", "Built a JavaScript widget") and not matching.term_met("Java", "Built a JavaScript widget")
assert matching.term_met("REST APIs", "Built a REST API") and not matching.term_met("Kubernetes", "Used Docker")
assert matching.term_met("Bachelor's degree in Computer Science", "Bachelor of Computer Science (Hons)")
assert matching.term_met("Docker", "Shipped it", tags=["docker"])
f1 = FactBullet(id="f1", entry_id="e1", section="experience", text="Built REST APIs in Python on PostgreSQL", tech_tags=["Python", "PostgreSQL"])
covered, missing = matching.coverage_of_facts(job, [f1], ["Docker"])
assert set(covered) == {"Python", "Postgres", "Docker", "REST APIs"} and "Kubernetes" in missing, (covered, missing)
print("1 ok: requirements matched by tool names and aliases, as whole words; soft skills left out")

# ---- 2. retrieval: every job keeps at least MIN_FACTS_PER_JOB bullets, even if projects rank higher
from service.retrieval import retriever as R
H = ResumeHeader(name="T", phone="", email="t@x.y", linkedin_url="", linkedin_display="", github_url="", github_display="")
sk = ResumeSkeleton(header=H, experience_entries=[ExperienceEntryMeta(id="e1", title="Intern", dates="2024", company="Acme", location="KL"),
                                                 ExperienceEntryMeta(id="e2", title="Barista", dates="2023", company="Cafe", location="KL")],
                    project_entries=[ProjectEntryMeta(id="p1", name="Alpha", tech_stack="Python", date="2025")],
                    education=[EducationEntry(id="u1", institution="UM", location="KL", degree_line="Bachelor of Computer Science", dates="2026",
                                              courses=["Databases"])])
facts = [FactBullet(id=f"p{i}", entry_id="p1", section="project", text=f"Python project bullet {i}", tech_tags=["Python"]) for i in range(12)]
facts += [FactBullet(id="c1", entry_id="e2", section="experience", text="Served 200 customers a day", tech_tags=[]),
          FactBullet(id="c2", entry_id="e2", section="experience", text="Trained 3 new staff", tech_tags=[]),
          FactBullet(id="c3", entry_id="e2", section="experience", text="Closed the shop", tech_tags=[]),
          FactBullet(id="a1", entry_id="e1", section="experience", text="Built REST APIs in Python", tech_tags=["Python"])]
R.embed_query = lambda text: [1.0, 0.0]
R.get_fact_embeddings = lambda fs: {f.id: ([1.0, 0.0] if f.entry_id == "p1" else [0.6, 0.8] if f.id != "c3" else [0.0, 1.0]) for f in fs}
got = R.retrieve_relevant_facts(job, facts, top_k=10, skeleton=sk)
per = {}
for g in got: per[g.fact.entry_id] = per.get(g.fact.entry_id, 0) + 1
assert per.get("e2") == 2 and per.get("e1") == 1 and per.get("p1") == 10, per
assert {g.fact.id for g in got if g.fact.entry_id == "e2"} == {"c1", "c2"}, "the job's best two bullets, not its worst"
got = R.retrieve_relevant_facts(job, facts, top_k=10, skeleton=sk, keep_entries=["p1"])
assert len(got) == 13
print("2 ok: every job keeps its two best bullets even when projects outrank it")

# ---- 3. learning from interviews: a capped nudge
outcomes.record("app1", ["c3"], good=True); outcomes.record("app2", ["c3"], good=True)
assert abs(outcomes.boosts()["c3"] - 0.06) < 1e-9
for i in range(5): outcomes.record(f"x{i}", ["c3"], good=True)
assert outcomes.boosts()["c3"] == outcomes.CAP
outcomes.record("app1", [], good=False); assert outcomes.boosts()["c3"] == outcomes.CAP   # still 6 wins
got = R.retrieve_relevant_facts(job, facts, top_k=100, skeleton=sk)
assert next(g for g in got if g.fact.id == "c3").keyword_boost >= outcomes.CAP
print("3 ok: bullets from resumes that got interviews get a small, capped boost")

# ---- 4. grounding: a reworded bullet may not add a tool or a number
from service.agentic import critic as C
from service.latex.fit import new_claims
assert new_claims("Built REST APIs with FastAPI, cutting latency by 30%", "Built RESTful FastAPI services, cutting latency 30%") == []
assert new_claims("Built REST APIs with FastAPI, cutting latency by 30%", "Built FastAPI services on Kubernetes, cutting latency 45%") == ["45%", "kubernetes"]
assert new_claims("Containerised the app with Docker", "Dockerized the app") == []
cand = [FactBulletWithScore(fact=f, similarity=0.9, keyword_boost=0) for f in facts]
bad = GeneratedResume(bullets=[ResumeBullet(fact_id="a1", entry_id="e1", text="Built REST APIs in Python and Kubernetes for 5,000 users"),
                               ResumeBullet(fact_id="c1", entry_id="e2", text="Served 200 customers daily"),
                               ResumeBullet(fact_id="c2", entry_id="e1", text="Trained 3 new staff"),
                               ResumeBullet(fact_id="zz", entry_id="e1", text="Invented")])
problems = C._find_unsupported_claims(bad, cand)
assert len(problems) == 3 and "kubernetes" in problems[0].lower() and "5,000" in problems[0], problems
fixed = C.keep_to_facts(bad, cand)
assert fixed == ["a1"] and [b.text for b in bad.bullets] == ["Built REST APIs in Python", "Served 200 customers daily"]
print("4 ok: every bullet is compared with its fact; additions are caught and put back to your own words")

# ---- 5. the Critic's score: half the AI's opinion, half the requirements the resume visibly shows
class _AI:
    def __init__(self, scores): self.scores = list(scores)
    def create(self, **kw):
        return C._LLMCriticScore(ats_score=self.scores.pop(0), keyword_coverage_notes="", overflow_risk=False, revision_notes="tighten")
C.instructor_client = type("X", (), {"chat": type("Y", (), {"completions": None})})()
C.instructor_client.chat.completions = _AI([80, 80])
good = GeneratedResume(bullets=[ResumeBullet(fact_id="a1", entry_id="e1", text="Built REST APIs in Python")])
fb = C.critique(good, job, cand, sk)
cov_pct = matching.coverage_percent(fb.covered, fb.missing)
assert fb.ai_score == 80 and fb.score == round(0.5 * 80 + 0.5 * cov_pct, 1) and "Kubernetes" in fb.missing and "Python" in fb.covered, fb
assert "Requirements not visible" in fb.revision_notes
fb2 = C.critique(GeneratedResume(bullets=[ResumeBullet(fact_id="a1", entry_id="e1", text="Built Kubernetes APIs")]), job, cand, sk)
assert fb2.score <= 40 and fb2.unsupported_claims
seen_prompt = []
class _Spy(_AI):
    def create(self, **kw):
        seen_prompt.append(kw["messages"][1]["content"]); return super().create(**kw)
C.instructor_client.chat.completions = _Spy([70])
C.critique(good, job, cand, sk)
assert "Education: Bachelor of Computer Science" in seen_prompt[0] and "Courses: Databases" in seen_prompt[0], seen_prompt[0]
print("5 ok: the shown score mixes the AI's opinion with requirements counted in code; any addition caps it; the reviewer sees education and skills")

# ---- 6. the loop keeps the best draft, stops when a rewrite stops helping, and never sends an addition
from service.agentic import loop as L
drafts = [GeneratedResume(bullets=[ResumeBullet(fact_id="a1", entry_id="e1", text="Built REST APIs in Python")]),
          GeneratedResume(bullets=[ResumeBullet(fact_id="a1", entry_id="e1", text="Built APIs")]),
          GeneratedResume(bullets=[ResumeBullet(fact_id="a1", entry_id="e1", text="never reached")])]
feeds = [CriticFeedback(score=70, keyword_coverage_notes="", overflow_risk=False, revision_notes="n", ai_score=70, coverage=60),
         CriticFeedback(score=60, keyword_coverage_notes="", overflow_risk=False, revision_notes="n", ai_score=60, coverage=50)]
L.generate_resume = lambda **kw: drafts.pop(0)
L.critique = lambda **kw: feeds.pop(0)
config.MAX_GENERATOR_ITERATIONS = 3
res = L.run_generator_critic_loop(job, cand, sk)
assert res.iterations_used == 2 and res.resume.bullets[0].text == "Built REST APIs in Python" and res.feedback.score == 70, res
drafts[:] = [GeneratedResume(bullets=[ResumeBullet(fact_id="a1", entry_id="e1", text="Built REST APIs in Python on Kubernetes")])]
feeds[:] = [CriticFeedback(score=40, keyword_coverage_notes="", overflow_risk=False, revision_notes="n", ai_score=90, coverage=50,
                           unsupported_claims=["adds kubernetes"])]
config.MAX_GENERATOR_ITERATIONS = 1
res = L.run_generator_critic_loop(job, cand, sk)
assert res.resume.bullets[0].text == "Built REST APIs in Python" and not res.feedback.unsupported_claims and res.feedback.score > 40
config.MAX_GENERATOR_ITERATIONS = 3
print("6 ok: best draft kept, early stop when rewrites stop helping, additions put back before sending")

# ---- 7. fitting to one page leaves out projects before a whole job
from service.latex import fit as F
work = ResumeSkeleton(header=H, experience_entries=sk.experience_entries, project_entries=sk.project_entries)
r = GeneratedResume(bullets=[ResumeBullet(fact_id="a1", entry_id="e1", text="x"), ResumeBullet(fact_id="c1", entry_id="e2", text="y"),
                             ResumeBullet(fact_id="p0", entry_id="p1", text="z")])
scores = {"a1": 0.9, "c1": 0.1, "p0": 0.8}
assert F._least_relevant(r, work, scores, set()) == ("entry", "p1"), "a project goes before a job, even a less related job"
r.bullets = r.bullets[:2]
assert F._least_relevant(r, work, scores, set()) == ("entry", "e2")
r.bullets.append(ResumeBullet(fact_id="p0", entry_id="p1", text="z"))
assert F._least_relevant(r, work, {"a1": 0.9, "c1": 0.1, "p0": 0.01}, set(), {"p1"}) == ("entry", "e2"), "an entry you asked to keep stays"
print("7 ok: trimming leaves out projects before jobs, and never an entry you asked to keep")

# ---- 8. long job pages are cut around the job sections, not at a fixed length
from bot.extraction import focus_text
page = "Backend Intern at Stripe\n" + "[Jobs](/jobs)\n" * 100 + ("Other jobs you might like. " * 400) + "\n## Requirements\n- Python\n- Postgres\n"
out = focus_text(page)
assert len(out) <= 8100 and "Backend Intern at Stripe" in out and "## Requirements" in out and "- Postgres" in out and "[Jobs]" not in out
assert focus_text("short page") == "short page"
print("8 ok: long pages keep the title and the requirements")

# ---- 9. the pipeline reports what the PDF really shows, and a summary for "What changed"
from service import pipeline as P
from service.latex.fit import FitResult
P.extract_job_posting = lambda raw: job
P.load_facts = lambda: facts
P.load_skeleton = lambda: sk
P.check_github = lambda job, missing: type("G", (), {"answers": [], "added": []})()
P.retrieve_relevant_facts = lambda job, facts, **kw: cand[:2] + [c for c in cand if c.fact.entry_id != "p1"]
final = GeneratedResume(bullets=[ResumeBullet(fact_id="a1", entry_id="e1", text="Built REST APIs in Python"),
                                 ResumeBullet(fact_id="c1", entry_id="e2", text="Served 200 customers a day")])
P.run_generator_critic_loop = lambda **kw: L.LoopResult(resume=final, feedback=CriticFeedback(
    score=75, keyword_coverage_notes="", overflow_risk=False, revision_notes="ok", ai_score=80, coverage=70), iterations_used=1, status="Good")
pdf = os.path.join(tmp, "r.pdf"); open(pdf, "w").write("x")
P.fit_one_page = lambda *a, **kw: FitResult(pdf, 1, resume=final, skeleton=sk)
from service.models import ProcessRequest
out = P.run_pipeline(ProcessRequest(company="Stripe", job_title="Backend Intern", location="KL", job_description="d", job_requirements="r", job_link="u"))
assert "Kubernetes" in out.missing and "Python" in out.covered and out.gaps == ["Postgres", "Docker"], out   # capped at MAX_SKILL_QUESTIONS
assert out.summary["bullets"] == 2 and out.summary["left_out"] == [{"id": "p1", "name": "Alpha"}] and out.facts_used == ["a1", "c1"]
assert out.summary["reworded"] == 0 and out.ai_score == 80
assert abs(out.score - (0.5 * 80 + 0.5 * out.coverage)) < 0.2
print("9 ok: score, requirements shown, tools to offer and the What changed summary come from the final PDF")
print("\n✅ quality test passed")
