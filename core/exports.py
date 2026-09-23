"""Spreadsheet-safe CSV exports with readable provenance columns."""
import csv
import io
import json

EXPORT_FIELDS = [
    "company_name", "website", "domain", "industry", "city", "country", "target_city", "target_country",
    "email", "email_status", "email_source", "phone", "phone_source", "contact_form_url",
    "lead_score", "rules_score", "qualification_status", "qualification_reasons", "location_status",
    "recommended_service", "likely_problem", "ai_status", "ai_score", "ai_reason", "description",
    "source", "source_url", "search_query", "pages_checked", "evidence", "crawl_status", "crawl_errors",
    "social_links", "lead_id", "created_at",
    "source_license", "source_attribution", "source_address", "listed_email", "listed_phone",
]


def csv_bytes(leads):
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=EXPORT_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for lead in leads:
        row = {}
        for key in EXPORT_FIELDS:
            value = lead.get(key, "")
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False)
            if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
                value = "'" + value
            row[key] = value
        writer.writerow(row)
    return ("\ufeff" + buffer.getvalue()).encode("utf-8")
