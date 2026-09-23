import time
from datetime import datetime, timedelta
from . import sheets, scraper, ai, email_sender
from .config import config


def run_discovery(*args, **kwargs):
    """Legacy synchronous discovery was replaced by durable campaign jobs."""
    raise ValueError("Use the Campaigns screen or core.campaigns.create_campaign/run_job for discovery.")


def run_outreach(daily_limit, min_score, your_name, log=print):
    leads = sheets.read_all("Leads", sheets.LEADS_HEADERS)
    eligible = [
        l for l in leads
        if l.get("status") == "QUALIFIED"
        and int(l.get("lead_score") or 0) >= min_score
        and l.get("email")
        and not l.get("last_contacted")
        and not sheets.domain_in_suppression(l.get("domain", ""), l.get("email", ""))
    ]
    eligible.sort(key=lambda l: int(l.get("lead_score") or 0), reverse=True)
    eligible = eligible[:daily_limit]

    sent = []
    for lead in eligible:
        log(f"✉️ Emailing {lead['company_name']}...")
        email_data = ai.generate_outreach_email(
            lead["company_name"], lead.get("likely_problem", ""),
            lead.get("recommended_service", ""), lead.get("outreach_angle", ""), your_name,
            github_url=config.GITHUB_URL, upwork_url=config.UPWORK_URL,
        )
        ok = email_sender.send_email(lead["email"], email_data.get("subject", ""), email_data.get("body", ""))
        if ok:
            sheets.update_lead_fields(lead["domain"], {
                "status": "CONTACTED",
                "last_contacted": sheets.today_str(),
                "next_followup": (datetime.utcnow() + timedelta(days=3)).strftime("%Y-%m-%d"),
                "followup_count": 0,
                "updated_at": sheets.now_iso(),
            })
            sheets.append_row_to("Outreach",
                ["outreach_id", "lead_id", "domain", "email", "subject", "body_preview", "sent_at", "status"],
                {"outreach_id": f"O-{int(time.time())}", "lead_id": lead["lead_id"], "domain": lead["domain"],
                 "email": lead["email"], "subject": email_data.get("subject", ""),
                 "body_preview": (email_data.get("body", "") or "")[:200],
                 "sent_at": sheets.now_iso(), "status": "sent"})
            sent.append(lead["company_name"])
        else:
            log(f"  ⚠️ Failed to send to {lead['email']}")

    log(f"✅ Done. {len(sent)} emails sent.")
    email_sender.notify_discord(f"📧 Outreach run complete\nEmails sent: {len(sent)}")
    return sent


STOP_STATUSES = {"REPLIED", "INTERESTED", "CALL_BOOKED", "PROPOSAL_SENT", "WON", "LOST", "DO_NOT_CONTACT"}


def run_followups(day1, day2, day3, your_name, log=print):
    leads = sheets.read_all("Leads", sheets.LEADS_HEADERS)
    today = sheets.today_str()
    due = [
        l for l in leads
        if l.get("status") == "CONTACTED"
        and l.get("next_followup") and l.get("next_followup") <= today
        and not sheets.domain_in_suppression(l.get("domain", ""), l.get("email", ""))
    ]

    sent = []
    for lead in due:
        count = int(lead.get("followup_count") or 0)
        if count == 0:
            stage, next_count, days_to_add = "Follow-up #1 - gentle nudge", 1, day2 - day1
        elif count == 1:
            stage, next_count, days_to_add = "Follow-up #2 - add value/example", 2, day3 - day2
        elif count == 2:
            stage, next_count, days_to_add = "Final follow-up - polite breakup, no pressure", 3, 0
        else:
            # sequence exhausted - stop silently, no more emails
            sheets.update_lead_fields(lead["domain"], {"next_followup": "", "updated_at": sheets.now_iso()})
            continue

        log(f"🔁 {stage} → {lead['company_name']}")
        email_data = ai.generate_followup_email(lead["company_name"], stage, lead.get("likely_problem", ""), your_name)
        ok = email_sender.send_email(lead["email"], email_data.get("subject", ""), email_data.get("body", ""))
        if ok:
            next_date = (datetime.utcnow() + timedelta(days=days_to_add)).strftime("%Y-%m-%d") if days_to_add else ""
            sheets.update_lead_fields(lead["domain"], {
                "followup_count": next_count, "next_followup": next_date, "updated_at": sheets.now_iso(),
            })
            sheets.append_row_to("Followups",
                ["followup_id", "lead_id", "domain", "stage", "sent_at", "subject", "body_preview", "status"],
                {"followup_id": f"F-{int(time.time())}", "lead_id": lead["lead_id"], "domain": lead["domain"],
                 "stage": stage, "sent_at": sheets.now_iso(), "subject": email_data.get("subject", ""),
                 "body_preview": (email_data.get("body", "") or "")[:200], "status": "sent"})
            sent.append(lead["company_name"])

    log(f"✅ Done. {len(sent)} follow-ups sent.")
    email_sender.notify_discord(f"🔁 Follow-up run complete\nFollow-ups sent: {len(sent)}")
    return sent
