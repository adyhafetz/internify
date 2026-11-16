"""Your tracker as an Excel file (.xlsx), sent in Telegram: the same columns as the tracker sheet plus the dates, with
the Status drop-down. It works without Google, so Google stays an optional extra."""
from __future__ import annotations

import io
import time

from bot.services import sheet_layout

EXTRA = ["Applied Date", "Deadline", "Match %"]


def _status_for_sheet(status: str) -> str:
    """The Sheet's own words; the bot's internal "Ready" (resume written, not sent) is "New" there."""
    return status if status in sheet_layout.STATUSES else "New"


def build(apps: list[dict]) -> bytes:
    """The workbook for these applications (newest first, as given), as file bytes."""
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from openpyxl.worksheet.datavalidation import DataValidation
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_layout.TAB_NAME
    headers = sheet_layout.HEADERS + EXTRA
    ws.append(headers)
    for app in apps:
        values = {
            "Company": app.get("company"), "Job Title": app.get("job_title"), "Location": app.get("location"),
            "Job Description": (app.get("job_description") or "")[:500], "Job Requirements": (app.get("job_requirements") or "")[:500],
            "Job Link": app.get("job_link"), "Resume Link": app.get("drive_link"), "Resume Status": app.get("resume_status"),
            "Comment": app.get("comment"), "Status": _status_for_sheet(app.get("status") or ""),
            "Applied Date": str(app.get("applied_at") or "")[:10], "Deadline": app.get("deadline"),
            "Match %": round(float(app["score"])) if app.get("score") else None,
        }
        ws.append([values[h] for h in headers])
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            if isinstance(cell.value, str):
                cell.data_type = "s"      # text is never read as a formula, whatever a job posting says
    for cell in ws[1]:
        cell.font = Font(bold=True)
    ws.freeze_panes = "A2"
    for i, h in enumerate(headers, start=1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = max(10, sheet_layout.WIDTHS.get(h, 14) / 7)
    if apps:
        status_col = ws.cell(row=1, column=headers.index("Status") + 1).column_letter
        dv = DataValidation(type="list", formula1='"' + ",".join(sheet_layout.STATUSES) + '"', allow_blank=True)
        ws.add_data_validation(dv)
        dv.add(f"{status_col}2:{status_col}{max(len(apps) + 1, 200)}")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def filename() -> str:
    return f"internify-tracker-{time.strftime('%Y-%m-%d')}.xlsx"
