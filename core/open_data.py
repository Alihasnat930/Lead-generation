"""Keyless business discovery from public OpenStreetMap data via Overpass."""
import json
import requests
from .providers import SearchPage, ProviderError, _candidate

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OSM_FILTERS = {
    "Healthcare / Clinics": [("amenity", "clinic|doctors"), ("healthcare", "clinic|doctor")],
    "Legal / Law Firms": [("office", "lawyer")],
    "Real Estate Agencies": [("office", "estate_agent")],
    "Accounting / Bookkeeping": [("office", "accountant|tax_advisor")],
    # Physical stores are candidates only: website evidence must still establish e-commerce.
    "E-commerce / Shopify": [("shop", "clothes|shoes|jewelry|furniture|electronics|sports|cosmetics|gift|books")],
    "HVAC / Home Services": [("craft", "hvac|plumber|electrician|roofer|carpenter|heating_engineer")],
    "Dental Clinics": [("amenity", "dentist"), ("healthcare", "dentist")],
    "Recruitment / Staffing": [("office", "employment_agency")],
    "Marketing Agencies": [("office", "advertising_agency|marketing")],
    "Restaurants / Salons": [("amenity", "restaurant|cafe"), ("shop", "hairdresser|beauty")],
}


def build_query(query):
    lat, lon = float(query["lat"]), float(query["lon"])
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ProviderError("configuration", "Invalid city coordinates.")
    filters = {}
    for niche in query["niches"]:
        for tag, values in OSM_FILTERS.get(niche, []):
            filters.setdefault(tag, set()).update(values.split("|"))
    if not filters:
        return ""
    selectors = [f'nwr["{tag}"~"^({"|".join(sorted(values))})$"](around:12000,{lat},{lon})[~"^(website|contact:website)$"~"."]["name"];'
                 for tag, values in sorted(filters.items())]
    return "[out:json][timeout:45][maxsize:67108864];(" + "".join(selectors) + ");out tags center;"


def parse_elements(data, query):
    if data.get("remark"):
        raise ProviderError("overpass_busy", "OpenStreetMap query was incomplete or timed out. Cooling down before retrying.", True)
    items = []
    for element in data.get("elements", []):
        tags = element.get("tags", {})
        niche = next((n for n in query["niches"] if any(tags.get(key) in values.split("|") for key, values in OSM_FILTERS.get(n, []))), None)
        website = tags.get("website") or tags.get("contact:website")
        if not niche or not website or not tags.get("name") or element.get("type") not in {"node", "way", "relation"}:
            continue
        url = f'https://www.openstreetmap.org/{element["type"]}/{int(element["id"])}'
        item = _candidate(tags["name"], website, tags.get("description", ""), {**query, "niche": niche}, "OpenStreetMap", url)
        if item:
            item.update(source_license="ODbL-1.0", source_attribution="© OpenStreetMap contributors",
                        source_address=" ".join(tags.get(key, "") for key in ("addr:housenumber", "addr:street", "addr:city", "addr:postcode", "addr:country")).strip(),
                        listed_email=tags.get("contact:email", tags.get("email", "")),
                        listed_phone=tags.get("contact:phone", tags.get("phone", "")))
            # Listed contacts are retained as raw public-source data, not automatically qualified.
            # Qualification still checks the corresponding business website.
            items.append(item)
    # Spread candidates over the requested niches instead of restaurant-heavy OSM ordering.
    groups = {}
    for item in items:
        groups.setdefault(item["industry"], []).append(item)
    balanced = [group[index] for index in range(max(map(len, groups.values()), default=0))
                for group in groups.values() if index < len(group)]
    return SearchPage(balanced, False)


def search_osm(query):
    body = build_query(query)
    if not body:
        return SearchPage([], False)
    try:
        with requests.post(OVERPASS_URL, data={"data": body}, timeout=(10, 60), stream=True,
                           headers={"User-Agent": "ProspectStudio/1.0 (small-scale public business research)"}) as response:
            if response.status_code in (429, 502, 503, 504):
                raise ProviderError("overpass_busy", "The free OpenStreetMap endpoint is busy. This source will cool down.", True)
            if response.status_code != 200:
                raise ProviderError("overpass_error", f"OpenStreetMap returned HTTP {response.status_code}.", True)
            chunks, size = [], 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > 8_000_000:
                    raise ProviderError("response_limit", "OpenStreetMap response exceeded the download limit. Use a smaller city coverage.")
                chunks.append(chunk)
            return parse_elements(json.loads(b"".join(chunks)), query)
    except requests.RequestException:
        raise ProviderError("network", "OpenStreetMap is temporarily unreachable.", True) from None
    except (ValueError, TypeError, KeyError):
        raise ProviderError("invalid_response", "OpenStreetMap returned invalid data.", True) from None
