"""Turn an uploaded resume file (PDF, Word or text) into plain text."""
from __future__ import annotations

import os
import re

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_PDF_PAGES = 6        # a resume is 1 to 2 pages; this only guards against someone sending a whole book
MIN_TEXT_CHARS = 200     # less than this is a scanned image or the wrong file


class ResumeFileError(Exception):
    """The message is shown to the user as is, so it is written in plain language."""


def _pdf_text(path: str) -> str:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError
    try:
        reader = PdfReader(path)
        if reader.is_encrypted and not reader.decrypt(""):
            raise ResumeFileError("That PDF is password-protected. Save an unprotected copy and send it again.")
        return "\n".join((page.extract_text() or "") for page in reader.pages[:MAX_PDF_PAGES])
    except ResumeFileError:
        raise
    except (PdfReadError, ValueError, OSError):
        raise ResumeFileError("I couldn't open that PDF. It may be damaged. Try exporting it again.")


def _docx_text(path: str) -> str:
    from docx import Document
    try:
        doc = Document(path)
    except Exception:
        raise ResumeFileError("I couldn't open that Word file. Try saving it as .docx or PDF and sending it again.")
    lines = [p.text for p in doc.paragraphs]
    for table in doc.tables:  # many resume templates put sections inside tables
        for row in table.rows:
            lines += [cell.text for cell in row.cells]
    return "\n".join(lines)


def read_resume_text(path: str, filename: str) -> str:
    """Return the resume's text, or raise ResumeFileError with a plain-language reason."""
    ext = os.path.splitext(filename.lower())[1]
    if ext == ".pdf":
        text = _pdf_text(path)
    elif ext == ".docx":
        text = _docx_text(path)
    elif ext in (".txt", ".md"):
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    elif ext == ".doc":
        raise ResumeFileError("That's an old Word format (.doc). Please save it as .docx or PDF and send it again.")
    else:
        raise ResumeFileError(f"I can read PDF, Word (.docx) and text files, but not {ext or 'that type of file'}.")

    text = re.sub(r"[ \t]+", " ", text.replace("\x00", ""))
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) < MIN_TEXT_CHARS:
        raise ResumeFileError(
            "I couldn't find enough text in that file. If it's a scan or a photo of your resume, export a text PDF "
            "from the original document, or paste the text into a .txt file and send that."
        )
    return text
