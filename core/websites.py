"""Bounded public website enrichment with source URLs for observed contacts."""
import base64
import ipaddress
import json
import re
import socket
import time
from urllib.parse import urlparse, urljoin, parse_qs, unquote
from urllib.robotparser import RobotFileParser

import requests
import tldextract
from bs4 import BeautifulSoup

EXTRACT = tldextract.TLDExtract(suffix_list_urls=(), include_psl_private_domains=True)
USER_AGENT = "ProspectStudio/1.0 (public business directory research)"
EXCLUDED = {
    "bing.com", "google.com", "facebook.com", "instagram.com", "linkedin.com", "twitter.com",
    "x.com", "youtube.com", "yelp.com", "yellowpages.com", "yell.com", "mapquest.com",
    "tripadvisor.com", "angi.com", "homeadvisor.com", "manta.com", "chamberofcommerce.com",
    "wikipedia.org", "wikidata.org", "crunchbase.com", "zoominfo.com", "apollo.io", "clutch.co",
    "justdial.com", "indeed.com", "glassdoor.com", "trustpilot.com", "bbb.org", "findlaw.com",
    "amazon.com", "etsy.com", "ebay.com", "houzz.com", "thumbtack.com", "squarespace.com",
}
EMAIL_RE = re.compile(r"[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
CONTACT_WORDS = re.compile(r"contact|about|kontakt|impressum|contatti|contacto|a-propos|over-ons|o-nas", re.I)


def normalize_url(value):
    value = str(value or "").strip()
    if value.startswith("//"):
        value = "https:" + value
    elif "://" not in value:
        value = "https://" + value
    try:
        parsed = urlparse(value)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username
                or parsed.password or parsed.port not in (None, 80, 443)):
            return ""
        host = parsed.hostname.encode("idna").decode().lower().rstrip(".")
        return f"{parsed.scheme}://{host}{parsed.path or '/'}" + (f"?{parsed.query}" if parsed.query else "")
    except (ValueError, UnicodeError):
        return ""


def domain_from_url(value):
    url = normalize_url(value)
    if not url:
        return ""
    host = urlparse(url).hostname
    ext = EXTRACT(host)
    return ext.top_domain_under_public_suffix or host.removeprefix("www.")


def excluded_domain(domain):
    return domain in EXCLUDED or any(domain.endswith("." + item) for item in EXCLUDED)


def decode_bing_url(url):
    if domain_from_url(url) == "bing.com":
        encoded = parse_qs(urlparse(url).query).get("u", [""])[0]
        if encoded.startswith("a1"):
            try:
                return unquote(base64.urlsafe_b64decode(encoded[2:] + "=" * (-len(encoded[2:]) % 4)).decode())
            except (ValueError, UnicodeError):
                return ""
    return url


def is_challenge(html):
    soup = BeautifulSoup(html[:250000], "html.parser")
    title = soup.title.get_text(" ", strip=True).lower() if soup.title else ""
    if any(term in title for term in ("just a moment", "access denied", "verify you are human", "captcha", "attention required")):
        return True
    text = soup.get_text(" ", strip=True).lower()
    return len(text) < 2500 and any(term in text for term in (
        "verify you are human", "unusual traffic from your computer", "complete the security check",
        "checking your browser before accessing", "please solve the challenge"))


class FetchError(Exception):
    pass


def validate_public_url(url):
    url = normalize_url(url)
    if not url:
        raise FetchError("invalid_url")
    host = urlparse(url).hostname
    try:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
            raise FetchError("non_public_address")
    except (socket.gaierror, ValueError):
        raise FetchError("dns_failed") from None
    return url


def fetch_html(session, url, timeout=12, max_bytes=1_500_000, allowed_domain=None):
    """Validate every redirect, limit download size, and never log request URLs/secrets."""
    for _ in range(5):
        url = validate_public_url(url)
        if allowed_domain and domain_from_url(url) != allowed_domain:
            raise FetchError("cross_domain_redirect")
        try:
            with session.get(url, timeout=(5, timeout), allow_redirects=False, stream=True,
                             headers={"User-Agent": USER_AGENT}) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    url = urljoin(url, response.headers.get("Location", ""))
                    continue
                if response.status_code >= 400:
                    raise FetchError(f"http_{response.status_code}")
                content_type = response.headers.get("Content-Type", "").lower()
                if content_type and not any(t in content_type for t in ("text/", "xhtml", "xml")):
                    raise FetchError("non_html")
                chunks, size, deadline = [], 0, time.monotonic() + timeout
                for chunk in response.iter_content(16384):
                    size += len(chunk)
                    if size > max_bytes or time.monotonic() > deadline:
                        raise FetchError("response_limit")
                    chunks.append(chunk)
                raw = b"".join(chunks)
                encoding = response.encoding if response.encoding and response.encoding != "ISO-8859-1" else "utf-8"
                try:
                    html = raw.decode(encoding, errors="replace")
                except LookupError:
                    # Some business servers send invalid charset labels. Keep the whole
                    # campaign alive and decode the bounded body conservatively.
                    html = raw.decode("utf-8", errors="replace")
                return html, url
        except requests.RequestException:
            raise FetchError("network_error") from None
    raise FetchError("redirect_limit")


def valid_email(value):
    email = unquote(str(value)).strip().lower().strip(".,;:")
    if not EMAIL_RE.fullmatch(email) or len(email) > 254 or ".." in email:
        return ""
    local, domain = email.rsplit("@", 1)
    if (local.split("+", 1)[0] in {"noreply", "no-reply", "donotreply", "example", "test", "yourname", "email", "username",
            "press", "media", "pr", "privacy", "abuse", "security", "dpo", "gdpr", "webmaster", "postmaster",
            "careers", "jobs", "recruiting", "recruitment", "accounts", "ap", "ar", "billing", "invoices", "payments"}
            or domain in {"example.com", "example.org", "domain.com", "email.com", "yourdomain.com", "wixpress.com"}
            or any(domain.endswith(s) for s in (".png", ".jpg", ".jpeg", ".webp", ".svg", ".gif", ".js", ".css", "sentry.io"))):
        return ""
    return email


def parse_page(html, url):
    soup = BeautifulSoup(html, "html.parser")
    structured = []
    for tag in soup.select('script[type="application/ld+json"]'):
        try:
            structured.append(json.loads(tag.string or tag.get_text()))
        except (ValueError, TypeError):
            continue
    # Extract explicitly published structured contacts only; never scrape JS tracking payloads.
    structured_text, structured_emails, structured_phones, countries = [], [], [], []

    def walk(item):
        if isinstance(item, list):
            for child in item:
                walk(child)
        elif isinstance(item, dict):
            for key, value in item.items():
                if key in ("name", "description", "streetAddress", "addressLocality", "addressRegion") and isinstance(value, str):
                    structured_text.append(value)
                if key == "email" and isinstance(value, str):
                    structured_emails.append(value.removeprefix("mailto:"))
                if key == "telephone" and isinstance(value, str):
                    structured_phones.append(value)
                if key == "addressCountry":
                    country = value.get("name", value.get("identifier", "")) if isinstance(value, dict) else value
                    if isinstance(country, str):
                        countries.append(country)
                walk(value)
    walk(structured)
    links = [(urljoin(url, a.get("href", "")), a.get_text(" ", strip=True)) for a in soup.select("a[href]")]
    mailto = [unquote(a.get("href", "")[7:].split("?")[0]) for a in soup.select('a[href^="mailto:"]')]
    phones = [unquote(a.get("href", "")[4:].split("?")[0]) for a in soup.select('a[href^="tel:"]')] + structured_phones
    has_form = bool(soup.select("form input[type=email], form textarea"))
    for element in soup(["script", "style", "noscript", "svg"]):
        element.decompose()
    text = soup.get_text(" ", strip=True) + " " + " ".join(structured_text)
    emails = {e for value in mailto + structured_emails + EMAIL_RE.findall(text) if (e := valid_email(value))}
    phones = sorted({p.strip() for p in phones if 7 <= len(re.sub(r"\D", "", p)) <= 15})
    desc = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
    return {"text": text[:20000], "description": (desc.get("content", "") if desc else "")[:600],
            "emails": sorted(emails), "phones": phones, "links": links,
            "contact_form": has_form, "countries": countries}


def enrich_website(website, timeout=12, max_pages=4):
    result = {"description": "", "email": "", "phone": "", "social_links": "", "pages": [],
              "email_source": "", "phone_source": "", "text": "", "contact_form": False,
              "contact_form_source": "", "crawl_status": "unreachable", "errors": [], "countries": []}
    website = normalize_url(website)
    if not website:
        return result
    domain = domain_from_url(website)
    origin = f"{urlparse(website).scheme}://{urlparse(website).netloc}"
    queue = list(dict.fromkeys([origin + "/", website]))
    seen, email_sources, phone_sources, socials, texts = set(), {}, {}, set(), []
    with requests.Session() as session:
        session.trust_env = False
        robots = RobotFileParser()
        try:
            robots_text, _ = fetch_html(session, origin + "/robots.txt", timeout, allowed_domain=domain)
            robots.parse(robots_text.splitlines())
        except FetchError as exc:
            if str(exc) == "http_404":
                robots.parse([])
            else:
                result["crawl_status"] = "robots_unavailable"
                result["errors"].append(str(exc))
                return result
        delay = max(0.8, float(robots.crawl_delay(USER_AGENT) or 0))
        if delay > 15:
            result["crawl_status"] = "crawl_delay_exceeded"
            return result
        while queue and len(seen) < max_pages:
            url = normalize_url(queue.pop(0))
            if not url or url in seen or domain_from_url(url) != domain:
                continue
            seen.add(url)
            if not robots.can_fetch(USER_AGENT, url):
                result["errors"].append("robots_disallowed")
                continue
            time.sleep(delay)
            try:
                html, final_url = fetch_html(session, url, timeout, allowed_domain=domain)
                if is_challenge(html):
                    result["errors"].append("captcha_required")
                    continue
                page = parse_page(html, final_url)
                if len(page["text"].strip()) < 60:
                    result["errors"].append("insufficient_content")
                    continue
                result["pages"].append({"url": final_url, "text": page["text"], "countries": page["countries"]})
                texts.append(page["text"])
                result["description"] = result["description"] or page["description"]
                result["countries"].extend(page["countries"])
                for email in page["emails"]:
                    email_sources.setdefault(email, final_url)
                for phone in page["phones"]:
                    phone_sources.setdefault(phone, final_url)
                if page["contact_form"]:
                    result["contact_form"], result["contact_form_source"] = True, final_url
                for link, label in page["links"]:
                    link_domain = domain_from_url(link)
                    if link_domain in {"facebook.com", "instagram.com", "linkedin.com", "x.com", "twitter.com"}:
                        socials.add(link)
                    if link_domain == domain and CONTACT_WORDS.search(urlparse(link).path + " " + label):
                        if not re.search(r"\.(pdf|jpg|png|zip)$", urlparse(link).path, re.I):
                            queue.append(link)
                if len(seen) == 1 and len(queue) < 2:
                    queue.extend([origin + "/contact", origin + "/about"])
            except FetchError as exc:
                result["errors"].append(str(exc))
    # Prefer business-domain inboxes; retain published free-provider addresses if no domain email exists.
    public_mail = {"gmail.com", "outlook.com", "hotmail.com", "yahoo.com", "yahoo.co.uk", "aol.com",
                   "icloud.com", "proton.me", "protonmail.com", "gmx.de", "gmx.com", "web.de",
                   "t-online.de", "orange.fr", "wanadoo.fr", "live.com", "msn.com", "btinternet.com"}
    email_sources = {email: source for email, source in email_sources.items()
                     if domain_from_url(email.rsplit("@", 1)[1]) == domain or email.rsplit("@", 1)[1] in public_mail}
    def email_rank(email):
        local, host = email.rsplit("@", 1)
        return (domain_from_url(host) != domain, local not in {"hello", "info", "contact", "sales", "office", "enquiries"}, email)
    if email_sources:
        result["email"] = sorted(email_sources, key=email_rank)[0]
        result["email_source"] = email_sources[result["email"]]
    if phone_sources:
        result["phone"] = next(iter(phone_sources))
        result["phone_source"] = phone_sources[result["phone"]]
    result["social_links"] = ", ".join(sorted(socials)[:5])
    result["text"] = "\n".join(texts)[:60000]
    result["crawl_status"] = "ok" if result["pages"] else (result["errors"][0] if result["errors"] else "unreachable")
    return result
