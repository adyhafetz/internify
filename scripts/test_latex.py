"""Offline test of the resume template (no LaTeX compiler needed): layout settings, one "Honors:" line, and no empty lists."""
from __future__ import annotations
import copy, os, re, sys

sys.path.insert(0, os.path.abspath("."))
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
from service.fact_store.store import load_facts, load_skeleton
from service.latex.compile import _honors_line, render_tex
from service.models import GeneratedResume, ResumeBullet

sk, facts = load_skeleton(), load_facts()
def resume(skip=()):
    return GeneratedResume(bullets=[ResumeBullet(entry_id=f.entry_id, text=f.text, fact_id=f.id) for f in facts if f.entry_id not in skip],
                           selected_courses={sk.education[0].id: ["Database Engineering", "Artificial Intelligence Algorithm"]}, selected_skills=sk.skills)

def check_no_empty_lists(tex: str) -> None:
    lines = [l.strip() for l in tex.splitlines() if l.strip()]
    for i, l in enumerate(lines[:-1]):
        if l == r"\resumeItemListStart": assert lines[i + 1].startswith(r"\resumeItem{"), f"empty bullet list after line {i}: {lines[i + 1]}"
        if l == r"\resumeSubHeadingListStart": assert lines[i + 1] != r"\resumeSubHeadingListEnd", f"empty section list at line {i}"

# H: every honor on one line after the courses
assert _honors_line(["Dean's List", "Vice Chancellor's Award"]) == "Honors: Dean's List, Vice Chancellor's Award"
assert _honors_line(["Honors: Dean's Honor Roll"]) == "Honors: Dean's Honor Roll", "an existing label is not doubled"
assert _honors_line(["Honours: Dean's List, Cum Laude", "Scholarship, 2024"]) == "Honors: Dean's List, Cum Laude; Scholarship, 2024"
assert _honors_line([]) == "" and _honors_line([" ", "Honors:"]) == ""
s2 = copy.deepcopy(sk); s2.education[0].honor_bullets = ["Dean's List", "Vice Chancellor's Award"]
tex = render_tex(resume(), s2)
edu = tex[tex.index(r"\section{Education}"):tex.index(r"\section{Work Experience}")]
items = re.findall(r"\\resumeItem\{(.*?)\}\n", edu)
assert items == ["Relevant Courses: Database Engineering, Artificial Intelligence Algorithm", "Honors: Dean's List, Vice Chancellor's Award"], items
print("H ok: Relevant Courses, then ONE Honors line")

# L: layout settings
assert r"\addtolength{\topmargin}{-.6in}" in tex and r"\addtolength{\textheight}{1.3in}" in tex and r"\usepackage[english]{babel}" in tex
assert r"\section{Work Experience}" in tex and "Jake" not in tex
check_no_empty_lists(tex)
print("L ok: margins, babel, 'Work Experience'")

# E: nothing empty is printed
first = s2.experience_entries[0].id
t = render_tex(resume(skip={first}), s2)
assert r"\section{Work Experience}" not in t, "a job with nothing selected is dropped, and so is its empty section"
check_no_empty_lists(t)
s3 = copy.deepcopy(s2); s3.experience_entries, s3.project_entries, s3.leadership = [], [], []
t = render_tex(resume(), s3)
assert r"\section{Work Experience}" not in t and r"\section{Projects}" not in t and r"\section{Leadership" not in t
check_no_empty_lists(t)
s4 = copy.deepcopy(s2); s4.education[0].courses, s4.education[0].honor_bullets = [], []
r4 = resume(); r4.selected_courses = {}
t = render_tex(r4, s4); assert r"\section{Education}" in t and r"\resumeSubheading" in t
check_no_empty_lists(t)
r5 = resume(); r5.selected_skills = {"Languages": [], "Databases": []}
t = render_tex(r5, s2) ; assert r"\section{Technical Skills}" in t, "falls back to your full skills when none were selected"
s6 = copy.deepcopy(s2); s6.skills = {k: [] for k in s6.skills}
r6 = resume(); r6.selected_skills = {}
assert r"\section{Technical Skills}" not in render_tex(r6, s6)
t7 = render_tex(resume(), s2); assert t7.rstrip().endswith(r"\end{document}")
print("E ok: empty sections, jobs without bullets, education without courses/honors and empty skills never produce an empty list")
print("\nALL TEMPLATE TESTS PASSED")
