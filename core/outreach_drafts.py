"""Free, evidence-based drafts with a deterministic plain-text and HTML signature."""
import hashlib
import html
import re
from .config import config


def clean(value, limit=200):
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def signature_links():
    return [("LinkedIn", config.LINKEDIN_URL), ("GitHub", config.GITHUB_URL), ("Upwork", config.UPWORK_URL)]


def with_signature(text, your_name):
    links = [(label, url) for label, url in signature_links() if url.startswith("https://")]
    footer = f"Best,\n{clean(your_name,100)}"
    body = text.strip() + "\n\n" + footer + "\n" + " | ".join(f"{label}: {url}" for label, url in links)
    paragraphs = "".join(f"<p>{html.escape(p).replace(chr(10),'<br>')}</p>" for p in text.strip().split("\n\n"))
    html_body = paragraphs + f"<p>Best,<br>{html.escape(clean(your_name,100))}<br>"
    html_body += " | ".join(f'<a href="{html.escape(url,quote=True)}">{label}</a>' for label, url in links) + "</p>"
    return body, html_body


def draft(lead, your_name, stage=0, original_subject=""):
    company = clean(lead.get('company_name')) or clean(lead.get('domain'))
    industry = clean(lead.get('industry'))
    service = clean(lead.get('recommended_service'))
    if not service or service.lower() in ('unknown', 'none', 'null'):
        service = 'customer enquiries and routine administrative workflows'
    variation = int(hashlib.sha256(lead.get('domain',company).encode()).hexdigest()[:2],16) % 3
    if stage == 0:
        openings = [f"I came across {company}'s website while researching {industry or 'businesses in your market'}.",
                    f"I'm reaching out after finding {company}'s public business website.",
                    f"I found {company} while researching {industry or 'businesses in your market'} and wanted to introduce myself."]
        subject = f"A workflow idea for {company}"
        text = (f"Hi {company} team,\n\n{openings[variation]}\n\n"
                f"I'm {clean(your_name)}, a freelance AI and full-stack developer. I build small, practical tools for {service.lower()}. "
                "If this is relevant to your team, would a 10-minute conversation about one workflow be useful?\n\n"
                "If you'd prefer no further emails, just reply and I'll stop.")
    else:
        subject = original_subject if original_subject.lower().startswith('re:') else 'Re: ' + original_subject
        if stage == 1:
            text = (f"Hi {company} team,\n\nFollowing up on my note about {service.lower()}. "
                    "A small pilot could focus on one repetitive task before considering anything larger. "
                    "Would a brief conversation be useful?\n\nIf this isn't relevant, reply and I'll close the loop.")
        else:
            text = (f"Hi {company} team,\n\nOne final follow-up on my workflow idea. "
                    "Would you like to discuss a small automation pilot? If the timing isn't right, no problem; "
                    "I won't follow up again. You can also reply to opt out.")
    body, html_body = with_signature(text, your_name)
    return {"subject": clean(subject), "body": body, "html": html_body}
