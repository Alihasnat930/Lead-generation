"""Search adapters. A failed source is never reported as an empty successful search."""
from dataclasses import dataclass
from urllib.parse import urlencode
import requests
from bs4 import BeautifulSoup
from .config import config
from .websites import normalize_url, domain_from_url, excluded_domain, decode_bing_url, is_challenge


class ProviderError(Exception):
    def __init__(self, code, message, retryable=False):
        super().__init__(message)
        self.code, self.retryable = code, retryable


@dataclass
class SearchPage:
    items: list
    has_more: bool


def _candidate(title, url, snippet, query, provider, source_url):
    url = normalize_url(url)
    domain = domain_from_url(url)
    if not url or not domain or excluded_domain(domain) or not title:
        return None
    return {"company_name": str(title)[:250], "website": url, "domain": domain,
            "description": str(snippet or "")[:1200], "industry": query["niche"],
            "target_city": query["city"], "target_country": query["country"],
            "country_code": query["country_code"], "language": query["language"],
            "source": provider, "source_url": source_url, "discovered_url": url,
            "search_query": query["query"]}


def search_page(provider, query, page=1):
    try:
        if provider == "free":
            kind = query.get("source_kind", "bing")
            if kind == "osm":
                from .open_data import search_osm
                return search_osm(query)
            if kind == "duckduckgo":
                return search_duckduckgo(query, page)
            if kind != "bing":
                raise ProviderError("configuration", "Unknown free source.")
            provider = "bing"
        if provider == "serpapi":
            if not config.SERPAPI_API_KEY:
                raise ProviderError("credentials", "Add SERPAPI_API_KEY in Settings before starting this provider.")
            params = {"engine": "google", "q": query["query"], "gl": query["country_code"],
                      "hl": query["language"], "start": (page - 1) * 10, "api_key": config.SERPAPI_API_KEY}
            response = requests.get("https://serpapi.com/search.json", params=params, timeout=(8, 35))
            check_status(response.status_code)
            data = response.json()
            if data.get("error"):
                # Provider messages can contain request details; don't persist them with the key.
                if "hasn't returned any results" in str(data["error"]).lower():
                    return SearchPage([], False)
                raise ProviderError("provider_error", "Search provider rejected the request. Check account credits and provider dashboard.")
            if not isinstance(data.get("organic_results", []), list):
                raise ProviderError("invalid_response", "Search provider returned an unexpected response.")
            source = "https://www.google.com/search?" + urlencode({k: v for k, v in params.items() if k not in {"api_key", "engine"}})
            items = [item for row in data.get("organic_results", []) if isinstance(row, dict)
                     if (item := _candidate(row.get("title"), row.get("link"), row.get("snippet"), query, provider, source))]
            return SearchPage(items, bool(data.get("serpapi_pagination", {}).get("next")))
        if provider != "bing":
            raise ProviderError("configuration", "Unknown search provider.")
        url = "https://www.bing.com/search?" + urlencode({"q": query["query"], "first": (page - 1) * 10 + 1, "count": 10})
        response = requests.get(url, timeout=(8, 30), headers={"User-Agent": "Mozilla/5.0"})
        check_status(response.status_code)
        if is_challenge(response.text):
            raise ProviderError("captcha", "Bing requested CAPTCHA. Its queue is saved; retry after a cooldown.")
        soup = BeautifulSoup(response.text, "html.parser")
        blocks = soup.select("li.b_algo")
        if not blocks:
            if soup.select(".b_no"):
                return SearchPage([], False)
            raise ProviderError("parse_error", "Bing returned no readable results. It may have blocked this request or changed its page.")
        items = []
        for block in blocks:
            link, snippet = block.select_one("h2 a[href]"), block.select_one("p")
            if link:
                item = _candidate(link.get_text(" ", strip=True), decode_bing_url(link["href"]),
                                  snippet.get_text(" ", strip=True) if snippet else "", query, provider, url)
                if item:
                    items.append(item)
        return SearchPage(items, bool(soup.select("a.sb_pagN")))
    except requests.RequestException:
        raise ProviderError("network", "Search request timed out or the network is unavailable.", True) from None
    except (ValueError, TypeError, KeyError):
        raise ProviderError("invalid_response", "Search provider returned invalid data.", True) from None


def check_status(code):
    if code in (401, 403):
        raise ProviderError("access_denied", "Search access denied. The source may be blocking automated requests.")
    if code == 402:
        raise ProviderError("credits", "Search provider credits exhausted. Progress has been saved.")
    if code == 429:
        raise ProviderError("rate_limit", "Search provider rate limit reached. Retry after a delay.", True)
    if code >= 500:
        raise ProviderError("unavailable", "Search provider is temporarily unavailable.", True)
    if code >= 400:
        raise ProviderError("http_error", f"Search provider returned HTTP {code}.")


def search_duckduckgo(query, page):
    try:
        from ddgs import DDGS
        region = ("uk" if query["country_code"] == "gb" else query["country_code"]) + "-" + query["language"]
        # One explicit engine, one search at a time; no automatic proxy rotation or paid services.
        with DDGS(timeout=20) as client:
            client.threads = 1
            rows = client.text(query["query"], region=region, safesearch="moderate",
                               max_results=10, page=page, backend="duckduckgo")
        source = "https://duckduckgo.com/?" + urlencode({"q": query["query"], "s": (page - 1) * 10})
        items = [item for row in rows if (item := _candidate(row.get("title"), row.get("href"), row.get("body"), query, "DuckDuckGo / DDGS", source))]
        return SearchPage(items, len(rows) >= 10)
    except ImportError:
        raise ProviderError("dependency", "Install requirements.txt to enable the free DDGS search source.") from None
    except ProviderError:
        raise
    except Exception:
        # DDGS can signal both throttling and no-results with exceptions. Preserve the query
        # instead of incorrectly marking a blocked request as a successful empty page.
        raise ProviderError("ddgs_unavailable", "DuckDuckGo did not return usable results. Its queue will be retried after a cooldown.", True) from None
