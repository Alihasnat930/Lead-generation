"""Compatibility helpers; new runs use core.campaigns with durable checkpoints."""
from .websites import domain_from_url, enrich_website, decode_bing_url as _decode_search_url
from .websites import excluded_domain as _is_excluded_domain
from .providers import search_page, ProviderError


def discover_places(search_query, location_query, max_results=20):
    query = {"query": f"{search_query} {location_query} contact", "niche": search_query,
             "city": location_query, "country": "", "country_code": "us", "language": "en"}
    try:
        results = []
        for page_number in range(1, min(3, (max_results + 9) // 10) + 1):
            page = search_page("bing", query, page_number)
            for row in page.items:
                results.append({**row, "title": row["company_name"], "categoryName": search_query, "city": location_query})
            if not page.has_more or len(results) >= max_results:
                break
        return results[:max_results]
    except ProviderError as exc:
        return {"error": str(exc)}
