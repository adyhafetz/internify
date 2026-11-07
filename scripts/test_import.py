"""Offline test of resume import: reading PDF/Word/text files, saving (replace/add), the Telegram flow, and the
manual commands. The AI step is replaced by a fixed result, so no API key or network is needed."""
from __future__ import annotations
import asyncio, io, os, shutil, sys, tempfile, time, types

sys.path.insert(0, os.path.abspath("."))
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
tmp = tempfile.mkdtemp()
store = os.path.join(tmp, "fact_store"); os.makedirs(store)
for name in ("facts.json", "resume_skeleton.json"):
    shutil.copy(os.path.join("service/fact_store", name), os.path.join(store, name))
from service import config as service_config
service_config.FACT_STORE_PATH = os.path.join(store, "facts.json")
service_config.SKELETON_PATH = os.path.join(store, "resume_skeleton.json")
service_config.EMBEDDING_CACHE_PATH = os.path.join(store, "cache.json")

from bot import resume_import
from bot.resume_import import ResumeFileError, read_resume_text
from bot.extraction import (ParsedResume, ParsedHeader, ParsedEducation, ParsedExperience, ParsedProject,
                            ParsedLeadership, ParsedBullet)
from service import importer
from service.fact_store.store import load_facts, load_skeleton
from service.admin import _load_facts_raw, _load_skeleton_raw

RESUME_LINES = ["Ady Student", "ady_student@uni.edu.my | +60 12-345 6789 | github.com/adystudent",
                "EDUCATION", "Universiti Example, B.Sc. Computer Science, CGPA 3.7", "EXPERIENCE",
                "Software Engineering Intern, Acme Sdn Bhd, Kuala Lumpur, Jun 2024 - Aug 2024",
                "Built REST APIs with FastAPI and PostgreSQL serving 2,000 daily requests",
                "PROJECTS", "TaskFlow, React Node.js Docker", "Built a kanban app used by 40 students"]


def make_pdf(lines) -> bytes:
    """A minimal one-page PDF with real text (what a text-based resume export contains)."""
    esc = lambda s: s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    content = "BT /F1 11 Tf 50 760 Td 16 TL " + " ".join(f"({esc(l)}) Tj T*" for l in lines) + " ET"
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            f"<< /Length {len(content)} >>\nstream\n{content}\nendstream", "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out)); out += f"{i} 0 obj\n{o}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs)+1}\n0000000000 65535 f \n".encode() + "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    return out + f"trailer\n<< /Size {len(objs)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()


def write(name, data):
    path = os.path.join(tmp, name)
    with open(path, "wb") as f: f.write(data)
    return path


def err(path, name):
    try: read_resume_text(path, name)
    except ResumeFileError as e: return str(e)
    raise AssertionError("expected ResumeFileError")


# ------------------------------------------------------------------ 1. reading files
text = read_resume_text(write("r.pdf", make_pdf(RESUME_LINES)), "r.pdf")
assert "Software Engineering Intern" in text and "FastAPI" in text, text
print("1a ok: PDF text read")

import docx
d = docx.Document(); [d.add_paragraph(l) for l in RESUME_LINES[:5]]
t = d.add_table(rows=1, cols=1); t.cell(0, 0).text = " ".join(RESUME_LINES[5:])      # sections inside a table
d.save(os.path.join(tmp, "r.docx"))
text = read_resume_text(os.path.join(tmp, "r.docx"), "My Resume.DOCX")
assert "Ady Student" in text and "FastAPI" in text, text
print("1b ok: Word text read, including text inside tables")

assert "kanban" in read_resume_text(write("r.txt", "\n".join(RESUME_LINES).encode()), "r.txt")
print("1c ok: text file read")

assert "image" not in err(write("p.png", b"x" * 500), "photo.png") and "PDF, Word" in err(write("p.png", b"x"), "photo.png")
assert "old Word format" in err(write("o.doc", b"x" * 500), "cv.doc")
assert "enough text" in err(write("s.pdf", make_pdf(["x"])), "scan.pdf")
assert "enough text" in err(write("e.txt", b"short"), "e.txt")
from pypdf import PdfWriter
w = PdfWriter(); w.add_blank_page(612, 792); w.encrypt("secret"); buf = io.BytesIO(); w.write(buf)
assert "password-protected" in err(write("enc.pdf", buf.getvalue()), "enc.pdf")
assert "couldn't open" in err(write("bad.pdf", b"not a pdf at all " * 30), "bad.pdf")
print("1d ok: wrong type, old .doc, scanned/empty, password-protected and damaged files all give a plain-language reason")

# ------------------------------------------------------------------ 2. saving
PARSED = ParsedResume(
    header=ParsedHeader(name="Ady Student", email="ady_student@uni.edu.my", phone="+60 12-345 6789",
                        github_url="https://github.com/adystudent"),             # no LinkedIn in this resume
    education=[ParsedEducation(institution="Universiti Example", location="Selangor", degree_line="B.Sc. Computer Science, CGPA 3.7",
                               dates="Sep 2022 -- Present", courses=["Algorithms", "Databases"], honor_bullets=["Dean's List"])],
    experience=[ParsedExperience(title="Software Engineering Intern", company="Acme Sdn Bhd", location="Kuala Lumpur", dates="Jun 2024 -- Aug 2024",
                                 bullets=[ParsedBullet(text="Built REST APIs with FastAPI and PostgreSQL serving 2,000 daily requests", tech_tags=["FastAPI", "PostgreSQL"]),
                                          ParsedBullet(text="Containerized services with Docker", tech_tags=["Docker"])]),
                ParsedExperience(title="Tutor", company="Uni Help Desk", location="", dates="2023", bullets=[])],
    projects=[ParsedProject(name="TaskFlow", tech_stack="React, Node.js, Docker", date="Dec 2024",
                            bullets=[ParsedBullet(text="Built a kanban app used by 40 students", tech_tags=["React"])])],
    leadership=[ParsedLeadership(title="Treasurer", org="CS Society", location="Universiti Example", dates="2023 -- 2024", bullets=["Managed a RM 5,000 budget"])],
    skills={"Languages": ["Python", "TypeScript"], "databases": ["PostgreSQL"], "Soft skills": ["Teamwork"]},
)
assert importer.profile_is_sample()
rep = importer.apply_resume(PARSED, "replace")

sk, facts = load_skeleton(), load_facts()                  # the real models: the data must be valid for tailoring
assert sk.header.name == "Ady Student" and sk.header.linkedin_url == "" and sk.header.linkedin_display == "", "sample LinkedIn must not survive"
assert [e.company for e in sk.experience_entries] == ["Acme Sdn Bhd", "Uni Help Desk"] and [p.name for p in sk.project_entries] == ["TaskFlow"]
assert all("jane" not in f.text.lower() and "Tech Corp" not in f.text for f in facts), "sample bullet points removed"
assert len(facts) == 3 and len({f.id for f in facts}) == 3
assert {f.entry_id for f in facts} <= {e.id for e in sk.experience_entries + sk.project_entries}
assert sk.skills["Languages"] == ["Python", "TypeScript"] and sk.skills["Databases"] == ["PostgreSQL"], sk.skills
assert rep.added == {"education": 1, "experience": 2, "projects": 1, "leadership": 1, "bullets": 3, "skills": 3}, rep.added
assert any("Soft skills" in w for w in rep.warnings) and any("Uni Help Desk" in w and "no bullet" in w for w in rep.warnings), rep.warnings
assert rep.backup_dir and sorted(os.listdir(rep.backup_dir)) == ["facts.json", "resume_skeleton.json"]
assert any(f.get("text", "").startswith("Engineered REST APIs") for f in _load_facts_raw.__globals__["json"].load(open(os.path.join(rep.backup_dir, "facts.json")))), "backup holds the old data"
print("2a ok: replace -> valid profile, sample data gone (incl. LinkedIn), backup kept:", rep.added)

# add mode: importing the same resume again must not duplicate anything
rep2 = importer.apply_resume(PARSED, "add")
assert rep2.added["experience"] == rep2.added["projects"] == rep2.added["education"] == 0 and len(rep2.skipped) == 5, (rep2.added, rep2.skipped)
assert len(load_facts()) == 3 and load_skeleton().header.name == "Ady Student"
print("2b ok: add mode skips what is already saved:", rep2.skipped)

# add mode with something new keeps the old and adds the new; the header is not touched on a real profile
more = ParsedResume(header=ParsedHeader(name="Someone Else"), projects=[ParsedProject(name="Chatbot", tech_stack="Python", date="Jan 2025",
                    bullets=[ParsedBullet(text="Built a support chatbot", tech_tags=["Python"])])])
importer.apply_resume(more, "add")
assert load_skeleton().header.name == "Ady Student" and {p.name for p in load_skeleton().project_entries} == {"TaskFlow", "Chatbot"} and len(load_facts()) == 4
print("2c ok: add mode keeps existing entries and your contact details")

try: importer.apply_resume(PARSED, "merge")
except ValueError: pass
else: raise AssertionError("bad mode must be rejected")

# ------------------------------------------------------------------ 3. the resume header
from service.latex.compile import render_tex
from service.models import GeneratedResume
tex = render_tex(GeneratedResume(bullets=[]), load_skeleton())
assert "ady\\_student@uni.edu.my" in tex and "\\href{mailto:ady_student@uni.edu.my}" in tex, "email shown escaped, linked raw"
head = tex[tex.index("\\begin{center}"):tex.index("\\end{center}")]          # just the contact block (the document uses $|$ elsewhere too)
assert "\\href{}" not in head and "linkedin" not in head.lower(), "no blank or sample LinkedIn link"
assert head.count("$|$") == 2 and head.rstrip().endswith("github.com/adystudent}}"), "three items, two separators, none dangling"
print("3 ok: header skips missing LinkedIn, email with underscore is safe in LaTeX")

# ------------------------------------------------------------------ 4. the Telegram flow
from bot.handlers import importresume as ir
from bot import extraction
shutil.copy(os.path.join("service/fact_store", "facts.json"), service_config.FACT_STORE_PATH)           # back to the sample for the flow test
shutil.copy(os.path.join("service/fact_store", "resume_skeleton.json"), service_config.SKELETON_PATH)
ir.extract_resume = lambda text: PARSED


class TGFile:
    def __init__(self, data): self.data = data
    async def download_to_drive(self, custom_path): open(custom_path, "wb").write(self.data)


class Doc:
    def __init__(self, name, data, size=None): self.file_name, self.data, self.file_size = name, data, size if size is not None else len(data)
    async def get_file(self): return TGFile(self.data)


class Msg:
    def __init__(self, doc=None): self.document, self.log, self.markup = doc, [], None
    async def reply_text(self, text, **kw):
        m = Msg(); m.log = self.log; self.log.append(text); m.parent = self; return m
    async def edit_text(self, text, reply_markup=None, **kw): self.log.append(text); self.last_markup = reply_markup


def upload(name, data, size=None, ctx=None):
    holder = Msg(Doc(name, data, size))
    status = {}
    async def reply_text(text, **kw):
        m = Msg(); m.log = holder.log; holder.log.append(text); status["m"] = m; return m
    holder.reply_text = reply_text
    update = types.SimpleNamespace(effective_message=holder, effective_user=types.SimpleNamespace(id=1, username="t"))
    ctx = ctx or types.SimpleNamespace(user_data={})
    asyncio.run(ir.resume_document_handler(update, ctx))
    return holder, status.get("m"), ctx


def tap(ctx, data):
    log = []
    class Q:
        def __init__(self): self.data = data
        async def answer(self, *a, **k): pass
        async def edit_message_text(self, text, **k): log.append(text)
    update = types.SimpleNamespace(callback_query=Q(), effective_message=Msg(), effective_user=types.SimpleNamespace(id=1, username="t"))
    asyncio.run(ir.import_callback(update, ctx))
    return log


holder, status, ctx = upload("cv.pdf", make_pdf(RESUME_LINES))
preview = holder.log[-1]
assert "I read your resume" in preview and "Software Engineering Intern, Acme Sdn Bhd (2 bullet points)" in preview, preview
assert "still the sample one (Jane Doe)" in preview and "3 in 2 categories" not in preview and "Skills:" in preview
labels = [b.text for row in status.last_markup.inline_keyboard for b in row]
assert labels == ["♻️ Replace what's saved (recommended)", "➕ Add to what's saved", "Cancel"], labels
assert importer.profile_is_sample(), "nothing is saved before the user taps a button"
print("4a ok: file -> preview with Replace (recommended) / Add / Cancel; nothing saved yet")

out = tap(ctx, "imp:replace")
assert out[0].startswith("⏳") and "Imported!" in out[-1] and "backed up" in out[-1] and "/experience" in out[-1], out
assert load_skeleton().header.name == "Ady Student"
assert tap(ctx, "imp:replace")[-1].startswith("That import has expired"), "a second tap must not import again"
print("4b ok: Replace saves, shows a summary; a double tap does nothing")

holder, status, ctx = upload("cv.docx", open(os.path.join(tmp, "r.docx"), "rb").read())
assert tap(ctx, "imp:cancel")[-1].startswith("👍") and ctx.user_data.get(ir.PENDING_KEY) is None
holder, status, ctx = upload("cv.txt", "\n".join(RESUME_LINES).encode())
ctx.user_data[ir.PENDING_KEY]["at"] = time.time() - ir.PENDING_SECONDS - 5
assert "expired" in tap(ctx, "imp:add")[-1]
print("4c ok: Cancel changes nothing; an old preview expires")

holder, status, _ = upload("photo.png", b"x" * 300)
assert "PDF, Word" in holder.log[-1], holder.log
holder, status, _ = upload("big.pdf", b"x", size=resume_import.MAX_FILE_BYTES + 1)
assert "bigger than 5 MB" in holder.log[-1]
ir.extract_resume = lambda text: ParsedResume()
holder, status, _ = upload("x.txt", ("some unrelated text. " * 30).encode())
assert "couldn't find any resume sections" in holder.log[-1]
print("4d ok: wrong file type, oversize file and a non-resume are refused politely")

# ------------------------------------------------------------------ 5. the manual commands still work (they used to error after saving)
from bot.handlers import experience as exp_h, project as proj_h, education as edu_h
extraction_calls = types.SimpleNamespace()
from service.admin import AddExperienceRequest, AddProjectRequest, AddEducationRequest, NewFactBullet
exp_h.extract_experience = lambda text: AddExperienceRequest(title="Dev", company="Foo", location="KL", dates="2025", bullets=[NewFactBullet(text="Did things")])
proj_h.extract_project = lambda text: AddProjectRequest(name="Bar", tech_stack="Go", date="Jan 2025", bullets=[NewFactBullet(text="Built Bar")])
edu_h.extract_education = lambda text: AddEducationRequest(institution="Baz Uni", location="KL", degree_line="BSc", dates="2025")

class Cmd:
    def __init__(self): self.sent = []
    async def reply_text(self, t, **kw): self.sent.append(t)
def run_cmd(fn, text):
    m = Cmd(); asyncio.run(fn(types.SimpleNamespace(effective_message=m), text)); return m.sent[-1]

for label, fn, text in (("experience", exp_h._process_and_save_experience, "x"), ("project", proj_h._process_and_save_project, "x"),
                        ("education", edu_h._process_and_save_education, "x")):
    last = run_cmd(fn, text)
    assert "❌" not in last and "Added" in last, (label, last)
class A: args = ["exp_foo"]
m = Cmd(); asyncio.run(exp_h.delete_experience_command(types.SimpleNamespace(effective_message=m, effective_user=types.SimpleNamespace(id=1, username="t")), A()))
assert "❌" not in m.sent[-1] and "Deleted experience" in m.sent[-1], m.sent
print("5 ok: /addexperience, /addproject, /addeducation and /deleteexperience no longer report an error after succeeding")
print("\n✅ import test passed")
