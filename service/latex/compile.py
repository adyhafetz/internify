from __future__ import annotations
import os
import re
import subprocess

from jinja2 import Environment, FileSystemLoader

from service import config
from service.clients import openai_client
from service.models import GeneratedResume, ResumeSkeleton

_SPECIAL_CHARS = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}
_SPECIAL_CHARS_RE = re.compile("|".join(re.escape(k) for k in _SPECIAL_CHARS))


def escape_latex(text: str) -> str:
    return _SPECIAL_CHARS_RE.sub(lambda m: _SPECIAL_CHARS[m.group()], text)


class TexCompileError(Exception):
    def __init__(self, log: str, tex_source: str):
        super().__init__("Tectonic compilation failed")
        self.log = log
        self.tex_source = tex_source


def _get_jinja_env(template_dir: str) -> Environment:
    return Environment(
        loader=FileSystemLoader(template_dir),
        variable_start_string="<<",
        variable_end_string=">>",
        block_start_string="<%",
        block_end_string="%>",
        comment_start_string="<<#",
        comment_end_string="#>>",
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_tex(
    resume: GeneratedResume,
    skeleton: ResumeSkeleton,
    template_path: str = config.TEX_TEMPLATE_PATH,
) -> str:
    template_dir = os.path.dirname(template_path)
    template_name = os.path.basename(template_path)
    env = _get_jinja_env(template_dir)
    template = env.get_template(template_name)

    bullets_by_entry: dict[str, list[str]] = {}
    for bullet in resume.bullets:
        bullets_by_entry.setdefault(bullet.entry_id, []).append(escape_latex(bullet.text))

    header = {
        "name": escape_latex(skeleton.header.name),
        "phone": escape_latex(skeleton.header.phone),
        "email": skeleton.header.email,  # inside \href{mailto:...}, left unescaped
        "linkedin_url": skeleton.header.linkedin_url,
        "linkedin_display": escape_latex(skeleton.header.linkedin_display),
        "github_url": skeleton.header.github_url,
        "github_display": escape_latex(skeleton.header.github_display),
    }

    education = []
    for edu in skeleton.education:
        bullets = []
        edu_courses = resume.selected_courses.get(edu.id, [])
        if edu_courses:
            bullets.append(escape_latex(f"Relevant Courses: {', '.join(edu_courses)}"))
        for b in edu.honor_bullets:
            bullets.append(escape_latex(b))
        education.append({
            "institution": escape_latex(edu.institution),
            "location": escape_latex(edu.location),
            "degree_line": escape_latex(edu.degree_line),
            "dates": escape_latex(edu.dates),
            "bullets": bullets,
        })

    experience = [
        {
            "meta": {
                "title": escape_latex(exp.title),
                "dates": escape_latex(exp.dates),
                "company": escape_latex(exp.company),
                "location": escape_latex(exp.location),
            },
            "bullets": bullets_by_entry.get(exp.id, [])[: config.MAX_BULLETS_PER_ENTRY],
        }
        for exp in skeleton.experience_entries
    ]

    projects = [
        {
            "meta": {
                "name": escape_latex(proj.name),
                "tech_stack": escape_latex(proj.tech_stack),
                "date": escape_latex(proj.date),
            },
            "bullets": bullets_by_entry.get(proj.id, [])[: config.MAX_BULLETS_PER_ENTRY],
        }
        for proj in skeleton.project_entries
    ]

    leadership = [
        {
            "title": escape_latex(entry.title),
            "dates": escape_latex(entry.dates),
            "org": escape_latex(entry.org),
            "location": escape_latex(entry.location),
            "bullets": [escape_latex(b) for b in entry.bullets],
        }
        for entry in skeleton.leadership
    ]

    # Use LLM-selected skills; fall back to full master list if the LLM returned nothing.
    skills_source = resume.selected_skills if resume.selected_skills else skeleton.skills
    skills = {
        escape_latex(category): [escape_latex(item) for item in items]
        for category, items in skills_source.items()
    }

    return template.render(
        header=header,
        education=education,
        experience=experience,
        projects=projects,
        leadership=leadership,
        skills=skills,
    )


def _run_tectonic(tex_path: str, output_dir: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["tectonic", "--outdir", output_dir, tex_path],
        capture_output=True,
        text=True,
        timeout=config.TECTONIC_TIMEOUT_SECONDS,
    )


def _fix_tex_with_llm(tex_source: str, error_log: str) -> str:
    response = openai_client.chat.completions.create(
        model=config.CRITIC_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "You fix LaTeX compilation errors. You will be given the .tex source "
                    "and the compiler error log. Return ONLY the corrected, complete .tex "
                    "source with no explanation, no markdown fences, nothing else."
                ),
            },
            {
                "role": "user",
                "content": f"TEX SOURCE:\n{tex_source}\n\nERROR LOG:\n{error_log}",
            },
        ],
    )
    return response.choices[0].message.content.strip()


def _normalize_pdf_for_ats(pdf_path: str) -> None:
    """
    Tectonic's default PDF output uses compressed cross-reference streams and
    object streams (valid PDF, but not universally supported by older/simpler
    ATS parsers). Rewriting with qpdf in a more conservative structure fixes
    compatibility without changing anything visible in the document.
    """
    tmp_path = pdf_path + ".normalized.pdf"
    try:
        result = subprocess.run(
            ["qpdf", "--object-streams=disable", "--decode-level=generalized", pdf_path, tmp_path],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0 and os.path.exists(tmp_path):
            os.replace(tmp_path, pdf_path)
        else:
            # Non-fatal: if qpdf fails for some reason, ship the original PDF
            # rather than losing the whole run over a normalization step.
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def compile_tex(
    resume: GeneratedResume,
    skeleton: ResumeSkeleton,
    filename_base: str,
    output_dir: str = config.OUTPUT_DIR,
    max_retries: int = config.MAX_TEX_FIX_RETRIES,
) -> str:
    """Renders, compiles, and self-heals a resume PDF. Returns the PDF path."""
    os.makedirs(output_dir, exist_ok=True)
    tex_source = render_tex(resume, skeleton)
    tex_path = os.path.join(output_dir, f"{filename_base}.tex")
    pdf_path = os.path.join(output_dir, f"{filename_base}.pdf")

    attempts = 0
    while True:
        with open(tex_path, "w", encoding="utf-8") as f:
            f.write(tex_source)

        result = _run_tectonic(tex_path, output_dir)

        if result.returncode == 0 and os.path.exists(pdf_path):
            _normalize_pdf_for_ats(pdf_path)
            return pdf_path

        attempts += 1
        if attempts > max_retries:
            raise TexCompileError(log=result.stderr, tex_source=tex_source)

        tex_source = _fix_tex_with_llm(tex_source, result.stderr)
