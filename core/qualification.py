"""Transparent qualification gates: query metadata is never treated as observed evidence."""
import hashlib
import re
from .markets import niche_terms, contains, CITY_ALIASES, normalized
from .store import now_iso
from .websites import domain_from_url


def qualify(candidate, enrichment, settings):
    niche = candidate["industry"]
    _, terms, suggested_service = niche_terms(niche, candidate.get("language", "en"), settings.get("keywords"))
    evidence, reasons = [], []
    matched_terms, city_match = set(), False
    cities = [candidate["target_city"]] + CITY_ALIASES.get(candidate["target_city"], [])
    for page in enrichment.get("pages", []):
        for term in terms:
            if contains(page["text"], term):
                matched_terms.add(term)
                evidence.append({"type": "niche", "value": term, "url": page["url"]})
        for city in cities:
            if contains(page["text"], city):
                city_match = True
                evidence.append({"type": "location", "value": city, "url": page["url"]})
    # Structured country evidence can disprove a same-name-city match.
    country_values = {normalized(value) for value in enrichment.get("countries", []) if value}
    code = candidate["country_code"]
    aliases = {normalized(candidate["target_country"]), code}
    aliases.update({"us": {"usa", "united states of america"}, "gb": {"uk", "great britain", "england", "scotland", "wales", "northern ireland"},
                    "de": {"deutschland"}, "fr": {"france"}, "es": {"espana"}, "it": {"italia"}, "nl": {"nederland"},
                    "at": {"osterreich"}, "pl": {"polska"}, "be": {"belgique", "belgie"}, "cz": {"cesko", "czech republic"}}.get(code, set()))
    country_conflict = bool(country_values and country_values.isdisjoint(aliases))
    country_match = not country_values.isdisjoint(aliases) if country_values else False
    if not country_match:
        country_match = any(contains(enrichment.get("text", ""), alias) for alias in aliases if len(alias) > 3)
    domain = candidate["domain"]
    cc_suffix = "uk" if code == "gb" else code
    country_match = country_match or (code != "us" and domain.endswith("." + cc_suffix))
    if code == "us" and not country_conflict:
        # A state abbreviation followed by a ZIP is also observed country evidence.
        states = "AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC"
        country_match = country_match or bool(re.search(r"\b(?:" + states + r")\s+\d{5}(?:-\d{4})?\b", enrichment.get("text", "")))
    location_ok = city_match and country_match and not country_conflict
    if country_match and not country_conflict:
        for page in enrichment.get("pages", []):
            if (any(normalized(country) in aliases for country in page.get("countries", []))
                    or any(contains(page["text"], alias) for alias in aliases if len(alias) > 3)
                    or (code != "us" and domain_from_url(page["url"]).endswith("." + cc_suffix))
                    or (code == "us" and re.search(r"\b(?:" + states + r")\s+\d{5}(?:-\d{4})?\b", page["text"]))):
                evidence.append({"type": "country", "value": candidate["target_country"], "url": page["url"]})
                break
    accessible = enrichment.get("crawl_status") == "ok"
    email = enrichment.get("email", "") if enrichment.get("email_source") else ""
    phone = enrichment.get("phone", "") if enrichment.get("phone_source") else ""
    contact_form = bool(enrichment.get("contact_form") and enrichment.get("contact_form_source"))
    contact_ok = bool(email) if settings.get("require_email", True) else bool(email or phone or contact_form)
    # Unknown directories/listicles can carry niche words and a publisher's email.
    # Keep them for review instead of treating the publisher as a target business.
    listing_page = bool(re.search(r"\b(?:directory|business listings|top\s+\d+|best\s+\d+|\d+\s+(?:best|top))\b",
                                  candidate.get("company_name", ""), re.I))
    for kind, value, url in [("email", email, enrichment.get("email_source")), ("phone", phone, enrichment.get("phone_source"))]:
        if value:
            evidence.append({"type": kind, "value": value, "url": url})
    if contact_form:
        evidence.append({"type": "contact_form", "value": "Published enquiry form", "url": enrichment["contact_form_source"]})
    if not accessible:
        reasons.append("Website could not be verified: " + enrichment.get("crawl_status", "unreachable"))
    if not matched_terms:
        reasons.append("No matching niche evidence found on the website.")
    if not location_ok:
        reasons.append("Target city and country not both supported by website evidence." if not country_conflict else "Website country conflicts with the target market.")
    if not contact_ok:
        reasons.append("No published email found." if settings.get("require_email", True) else "No published contact method found.")
    if listing_page:
        reasons.append("Search result appears to be a directory or ranked list; verify the business identity.")
    score = (15 if accessible else 0) + (30 if matched_terms else 0) + (20 if location_ok else 0)
    score += 25 if email else (15 if phone else (10 if contact_form else 0))
    score += 10 if len(enrichment.get("pages", [])) > 1 else 0
    if score < settings["min_score"]:
        reasons.append(f'Score {score} is below the campaign threshold {settings["min_score"]}.')
    qualified = accessible and bool(matched_terms) and location_ok and contact_ok and not listing_page and score >= settings["min_score"]
    status = "QUALIFIED" if qualified else "REVIEW"
    return {
        **candidate, "lead_id": "L-" + hashlib.sha256(domain.encode()).hexdigest()[:20],
        "city": candidate["target_city"] if city_match else "", "country": candidate["target_country"] if country_match and not country_conflict else "",
        "location_status": "website_supported" if location_ok else "unconfirmed", "email": email, "phone": phone,
        "email_source": enrichment.get("email_source", ""), "phone_source": enrichment.get("phone_source", ""),
        "email_status": "published_unverified" if email else "not_found",
        "description": enrichment.get("description", "") or candidate.get("description", ""),
        "contact_form_url": enrichment.get("contact_form_source", ""), "social_links": enrichment.get("social_links", ""),
        "crawl_status": enrichment.get("crawl_status", "unreachable"), "crawl_errors": enrichment.get("errors", []),
        "pages_checked": [p["url"] for p in enrichment.get("pages", [])], "evidence": evidence[:30],
        "lead_score": score, "rules_score": score, "qualification_status": status, "status": status,
        "fit": bool(qualified), "qualification_reasons": reasons or ["Niche, market and contact evidence meet the campaign requirements."],
        "recommended_service": suggested_service, "likely_problem": "unknown", "decision_maker": "unknown",
        "decision_maker_role": "unknown", "outreach_angle": "", "ai_status": "not_requested",
        "created_at": now_iso(), "updated_at": now_iso(), "last_contacted": "", "next_followup": "", "followup_count": 0,
    }
