import gspread
from gspread.exceptions import APIError, WorksheetNotFound
from google.oauth2.service_account import Credentials
from datetime import datetime
from .config import config

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

_client = None
_sheet = None

LEADS_HEADERS = [
    "lead_id", "company_name", "website", "domain", "industry", "city", "state", "country",
    "phone", "email", "email_source", "email_status", "decision_maker", "decision_maker_role",
    "source", "source_url", "lead_score", "fit", "likely_problem", "recommended_service",
    "outreach_angle", "linkedin_url", "linkedin_status", "status", "created_at", "updated_at",
    "last_contacted", "next_followup", "followup_count"
]


def get_client():
    global _client
    if _client is None:
        creds = Credentials.from_service_account_file(config.SERVICE_ACCOUNT_FILE, scopes=SCOPES)
        _client = gspread.authorize(creds)
    return _client


def get_spreadsheet():
    global _sheet
    if _sheet is None:
        try:
            _sheet = get_client().open_by_key(config.GOOGLE_SHEET_ID)
        except PermissionError as exc:
            raise PermissionError(
                "Google Sheet access denied. Share the configured Sheet with "
                "lead-gen-bot@x-avenue-506305-u4.iam.gserviceaccount.com as Editor."
            ) from exc
    return _sheet


def get_or_create_worksheet(name, headers):
    ss = get_spreadsheet()
    try:
        ws = ss.worksheet(name)
    except WorksheetNotFound:
        ws = ss.add_worksheet(title=name, rows=1000, cols=len(headers) + 2)
        ws.append_row(headers)
    return ws


def _permission_error(exc):
    if isinstance(exc, APIError) and getattr(exc.response, "status_code", None) == 403:
        return PermissionError(
            "Google Sheet write permission denied. Share the Sheet with "
            "lead-gen-bot@x-avenue-506305-u4.iam.gserviceaccount.com as Editor."
        )
    return exc


def read_all(sheet_name, headers):
    ws = get_or_create_worksheet(sheet_name, headers)
    return _read_records(ws, headers)


def _read_records(ws, expected_headers):
    """Read rows without requiring the existing sheet header row to be unique."""
    values = ws.get_all_values()
    if not values:
        return []

    actual_headers = values[0]
    header_indexes = {}
    for index, header in enumerate(actual_headers):
        # Keep the first matching column when an old sheet has duplicate headers.
        if header and header not in header_indexes:
            header_indexes[header] = index

    records = []
    for row in values[1:]:
        record = {}
        for header in expected_headers:
            index = header_indexes.get(header)
            record[header] = row[index] if index is not None and index < len(row) else ""
        if any(record.values()):
            records.append(record)
    return records


def _col_letter(col_num: int) -> str:
    """Convert a 1-indexed column number to its spreadsheet letter (1 -> A, 29 -> AC)."""
    letters = ""
    while col_num > 0:
        col_num, remainder = divmod(col_num - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def upsert_lead(lead: dict):
    """Insert a new lead row, or update the existing row if the domain already exists."""
    ws = get_or_create_worksheet("Leads", LEADS_HEADERS)
    records = _read_records(ws, LEADS_HEADERS)
    row_values = [str(lead.get(h, "")) for h in LEADS_HEADERS]

    domain = lead.get("domain", "")
    last_col = _col_letter(len(LEADS_HEADERS))
    for i, r in enumerate(records):
        if domain and r.get("domain") == domain:
            row_num = i + 2
            try:
                ws.update(f"A{row_num}:{last_col}{row_num}", [row_values])
            except APIError as exc:
                raise _permission_error(exc) from exc
            return "updated"

    try:
        ws.append_row(row_values)
    except APIError as exc:
        raise _permission_error(exc) from exc
    return "inserted"


def update_lead_fields(domain: str, fields: dict):
    """Update only specific columns for the lead matching this domain."""
    ws = get_or_create_worksheet("Leads", LEADS_HEADERS)
    records = _read_records(ws, LEADS_HEADERS)
    for i, r in enumerate(records):
        if r.get("domain") == domain:
            row_num = i + 2
            for key, value in fields.items():
                if key in LEADS_HEADERS:
                    col_num = LEADS_HEADERS.index(key) + 1
                    try:
                        ws.update_cell(row_num, col_num, str(value))
                    except APIError as exc:
                        raise _permission_error(exc) from exc
            return True
    return False


def append_row_to(sheet_name, headers, row_dict):
    ws = get_or_create_worksheet(sheet_name, headers)
    try:
        ws.append_row([str(row_dict.get(h, "")) for h in headers])
    except APIError as exc:
        raise _permission_error(exc) from exc


def domain_in_suppression(domain: str, email: str) -> bool:
    headers = ["email", "domain", "reason", "added_at"]
    records = read_all("Suppression", headers)
    email_l = (email or "").lower()
    domain_l = (domain or "").lower()
    for r in records:
        if (r.get("email", "").lower() == email_l and email_l) or (r.get("domain", "").lower() == domain_l and domain_l):
            return True
    return False


def now_iso():
    return datetime.utcnow().isoformat()


def today_str():
    return datetime.utcnow().strftime("%Y-%m-%d")


def append_new_qualified(leads):
    """Bulk append only. Never overwrite existing CRM status, notes or follow-ups."""
    from .websites import domain_from_url
    ws = get_or_create_worksheet("Leads", LEADS_HEADERS)
    values = ws.get_all_values()
    actual_headers = values[0] if values else []
    if not actual_headers:
        ws.update(range_name="A1", values=[LEADS_HEADERS], value_input_option="RAW")
        actual_headers = LEADS_HEADERS[:]
        values = [actual_headers]
    for required in ("domain", "email", "status"):
        if actual_headers.count(required) != 1:
            raise ValueError(f"CRM must have exactly one '{required}' column before syncing.")
    existing = _read_records_from_values(values, actual_headers)
    domains = {domain_from_url(row.get("domain", "")) for row in existing if row.get("domain")}
    emails = {row.get("email", "").lower() for row in existing if row.get("email")}
    rows = []
    for lead in leads:
        if lead.get("qualification_status") != "QUALIFIED":
            continue
        domain, email = lead.get("domain", ""), lead.get("email", "").lower()
        if not domain or domain in domains or (email and email in emails):
            continue
        rows.append([str(lead.get(header, "")) for header in actual_headers])
        domains.add(domain)
        if email:
            emails.add(email)
    for start in range(0, len(rows), 200):
        ws.append_rows(rows[start:start + 200], value_input_option="RAW")
    return len(rows)


def _read_records_from_values(values, headers):
    return [{key: row[index] if index < len(row) else "" for index, key in enumerate(headers)} for row in values[1:]]
