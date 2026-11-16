"""The one tracker sheet layout, the same as the template in the README ("Internify — Application Tracker"): the columns in
this order with Status last, the Status drop-down, a frozen bold header row and readable column widths. The setup page
and every other place that creates a tracker sheet use this, so a sheet never depends on how it was made.

The bot finds columns by their header name, so a sheet with the same headers in another order still works."""
from __future__ import annotations

from bot.services.db import SHEET_STATUSES

SHEET_TITLE = "Internify — Application Tracker"
TAB_NAME = "Tracker"
HEADERS = ["Company", "Job Title", "Location", "Job Description", "Job Requirements", "Job Link",
           "Resume Link", "Resume Status", "Comment", "Status"]
STATUSES = list(SHEET_STATUSES)   # the Status drop-down: the same words the bot writes and reads
WIDTHS = {"Company": 170, "Job Title": 230, "Location": 150, "Job Description": 330, "Job Requirements": 330,
          "Job Link": 230, "Resume Link": 230, "Resume Status": 110, "Comment": 380, "Status": 120}
DROPDOWN_ROWS = 2000


def create_body() -> dict:
    """The request that creates the empty spreadsheet (title, one tab, frozen header)."""
    return {"properties": {"title": SHEET_TITLE},
            "sheets": [{"properties": {"title": TAB_NAME, "gridProperties": {"frozenRowCount": 1}}}]}


def format_requests(tab_id: int) -> list[dict]:
    """Bold header, the Status drop-down and column widths for a tab."""
    status_col = HEADERS.index("Status")
    reqs: list[dict] = [
        {"repeatCell": {"range": {"sheetId": tab_id, "startRowIndex": 0, "endRowIndex": 1},
                        "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}}, "fields": "userEnteredFormat.textFormat.bold"}},
        {"setDataValidation": {"range": {"sheetId": tab_id, "startRowIndex": 1, "endRowIndex": DROPDOWN_ROWS,
                                         "startColumnIndex": status_col, "endColumnIndex": status_col + 1},
                               "rule": {"condition": {"type": "ONE_OF_LIST", "values": [{"userEnteredValue": v} for v in STATUSES]},
                                        "showCustomUi": True, "strict": False}}},
    ]
    for i, name in enumerate(HEADERS):
        reqs.append({"updateDimensionProperties": {"range": {"sheetId": tab_id, "dimension": "COLUMNS", "startIndex": i, "endIndex": i + 1},
                                                   "properties": {"pixelSize": WIDTHS[name]}, "fields": "pixelSize"}})
    return reqs


def create_sheet(sheets) -> tuple[str, int]:
    """Make a new tracker spreadsheet with the Sheets API client ``sheets``. Returns (spreadsheet id, tab id)."""
    made = sheets.spreadsheets().create(body=create_body(), fields="spreadsheetId,sheets.properties.sheetId").execute()
    sid, tab_id = made["spreadsheetId"], made["sheets"][0]["properties"]["sheetId"]
    sheets.spreadsheets().values().update(spreadsheetId=sid, range=f"{TAB_NAME}!A1:{chr(64 + len(HEADERS))}1",
                                          valueInputOption="RAW", body={"values": [HEADERS]}).execute()
    sheets.spreadsheets().batchUpdate(spreadsheetId=sid, body={"requests": format_requests(tab_id)}).execute()
    return sid, tab_id


def map_rows(values: list[list[str]]) -> list[list[str]]:
    """Rows from an older sheet, rearranged into this layout by header name (columns this layout doesn't have are left
    out, columns the old sheet didn't have stay empty). The old header row is not included; empty rows are skipped."""
    if not values:
        return []
    old = [h.strip() for h in values[0]]
    index = {h: i for i, h in enumerate(old) if h}
    out = []
    for row in values[1:]:
        mapped = [(row[index[h]] if h in index and index[h] < len(row) else "") for h in HEADERS]
        if any(str(c).strip() for c in mapped):
            out.append(mapped)
    return out
