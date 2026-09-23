"""Editable niche and geographic search coverage. No claimed company facts here."""
import re
import unicodedata
import json
from pathlib import Path
from functools import lru_cache


def normalized(text):
    return " ".join(unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode().lower().split())


def contains(text, phrase):
    return bool(re.search(r"(?<!\w)" + re.escape(normalized(phrase)) + r"(?!\w)", normalized(text)))


# English query variants, observed industry signals, a suggested service (not a claimed problem).
NICHES = {
    "Healthcare / Clinics": (["private clinic", "medical practice", "healthcare clinic"],
        ["clinic", "medical practice", "healthcare", "clinique", "arzt", "praxis", "clinica"], "Patient intake and appointment automation"),
    "Legal / Law Firms": (["law firm", "solicitors", "legal practice"],
        ["law firm", "solicitor", "attorney", "legal services", "avocat", "rechtsanwalt", "abogado", "avvocato", "advocaat"], "Client intake and document workflow automation"),
    "Real Estate Agencies": (["real estate agency", "estate agents", "property agency"],
        ["real estate", "estate agent", "property agency", "immobilier", "immobilien", "inmobiliaria", "immobiliare", "makelaar"], "Property enquiries and CRM automation"),
    "Accounting / Bookkeeping": (["accounting firm", "bookkeeping services", "accountants"],
        ["accountant", "accounting", "bookkeeping", "comptable", "steuerberater", "asesoria", "commercialista", "boekhouder"], "Invoice processing and data entry automation"),
    "E-commerce / Shopify": (["online store", "Shopify store", "ecommerce shop"],
        ["shopify", "add to cart", "shopping cart", "online store", "online shop", "ajouter au panier", "warenkorb", "anadir al carrito", "aggiungi al carrello"], "Customer support and order workflow automation"),
    "HVAC / Home Services": (["HVAC contractor", "plumbing company", "heating air conditioning"],
        ["hvac", "plumbing", "heating", "air conditioning", "chauffage", "plombier", "heizung", "fontanero", "idraulico", "loodgieter"], "Missed-call follow-up and appointment booking"),
    "Dental Clinics": (["dental clinic", "dental practice", "dentist"],
        ["dental", "dentist", "dentiste", "zahnarzt", "dentista", "tandarts", "stomatolog"], "Patient intake and reminder automation"),
    "Recruitment / Staffing": (["recruitment agency", "staffing agency", "recruitment company"],
        ["recruitment", "staffing", "recruiting", "recrutement", "personalvermittlung", "seleccion de personal", "recrutamento", "werving"], "Candidate intake and matching workflows"),
    "Marketing Agencies": (["digital marketing agency", "marketing agency", "advertising agency"],
        ["marketing agency", "digital marketing", "advertising agency", "agence marketing", "werbeagentur", "agencia de marketing", "agenzia marketing"], "White-label AI automation for agency clients"),
    "Restaurants / Salons": (["restaurant", "hair salon", "beauty salon"],
        ["restaurant", "salon", "hairdresser", "ristorante", "peluqueria", "friseur", "coiffure", "restaurante"], "Booking, enquiry and reminder automation"),
}

# A local-language term is added alongside English variants in major EU markets.
LOCAL_TERMS = {
    "de": ["Privatklinik", "Rechtsanwalt", "Immobilienmakler", "Steuerberater", "Onlineshop", "Heizungsinstallateur", "Zahnarzt", "Personalvermittlung", "Werbeagentur", "Friseur"],
    "fr": ["clinique privee", "cabinet avocat", "agence immobiliere", "expert comptable", "boutique en ligne", "plombier chauffagiste", "cabinet dentaire", "cabinet recrutement", "agence marketing", "salon coiffure"],
    "es": ["clinica privada", "abogados", "agencia inmobiliaria", "asesoria contable", "tienda online", "fontanero", "clinica dental", "agencia seleccion personal", "agencia marketing", "peluqueria"],
    "it": ["clinica privata", "studio legale", "agenzia immobiliare", "commercialista", "negozio online", "idraulico", "studio dentistico", "agenzia lavoro", "agenzia marketing", "parrucchiere"],
    "nl": ["privekliniek", "advocatenkantoor", "makelaar", "boekhouder", "webwinkel", "loodgieter", "tandarts", "uitzendbureau", "marketingbureau", "kapsalon"],
    "pt": ["clinica privada", "advogados", "agencia imobiliaria", "contabilidade", "loja online", "canalizador", "clinica dentaria", "recrutamento", "agencia marketing", "cabeleireiro"],
    "pl": ["klinika", "kancelaria prawna", "agencja nieruchomosci", "biuro rachunkowe", "sklep internetowy", "hydraulik", "stomatolog", "agencja rekrutacyjna", "agencja marketingowa", "fryzjer"],
}

# code: (country label, region, search language, cities)
COUNTRIES = {
    "us": ("United States", "US", "en", "New York|Los Angeles|Chicago|Houston|Phoenix|Philadelphia|San Antonio|San Diego|Dallas|Austin|Jacksonville|Fort Worth|Columbus|Charlotte|Indianapolis|Seattle|Denver|Boston|Nashville|Las Vegas|Portland|Detroit|Memphis|Louisville|Baltimore|Milwaukee|Albuquerque|Tucson|Fresno|Sacramento|Atlanta|Miami|Tampa|Orlando|Raleigh|Omaha|Minneapolis|Cleveland|Pittsburgh|San Francisco"),
    "gb": ("United Kingdom", "UK", "en", "London|Manchester|Birmingham|Leeds|Glasgow|Liverpool|Bristol|Edinburgh|Sheffield|Cardiff|Belfast|Nottingham|Leicester|Newcastle|Southampton|Brighton|Cambridge|Oxford|Reading|Coventry"),
    "de": ("Germany", "EU", "de", "Berlin|Hamburg|Munich|Cologne|Frankfurt|Stuttgart|Dusseldorf|Leipzig"),
    "fr": ("France", "EU", "fr", "Paris|Lyon|Marseille|Toulouse|Nice|Nantes|Bordeaux|Lille"),
    "es": ("Spain", "EU", "es", "Madrid|Barcelona|Valencia|Seville|Malaga|Bilbao"),
    "it": ("Italy", "EU", "it", "Rome|Milan|Naples|Turin|Bologna|Florence"),
    "nl": ("Netherlands", "EU", "nl", "Amsterdam|Rotterdam|Utrecht|The Hague|Eindhoven"),
    "ie": ("Ireland", "EU", "en", "Dublin|Cork|Galway|Limerick"),
    "be": ("Belgium", "EU", "fr", "Brussels|Antwerp|Ghent|Bruges"),
    "at": ("Austria", "EU", "de", "Vienna|Graz|Salzburg|Linz"),
    "pt": ("Portugal", "EU", "pt", "Lisbon|Porto|Braga"),
    "pl": ("Poland", "EU", "pl", "Warsaw|Krakow|Wroclaw|Gdansk|Poznan"),
    "se": ("Sweden", "EU", "sv", "Stockholm|Gothenburg|Malmo"),
    "dk": ("Denmark", "EU", "da", "Copenhagen|Aarhus|Odense"),
    "fi": ("Finland", "EU", "fi", "Helsinki|Tampere|Turku"),
    "cz": ("Czechia", "EU", "cs", "Prague|Brno|Ostrava"),
    "gr": ("Greece", "EU", "el", "Athens|Thessaloniki"),
    "ro": ("Romania", "EU", "ro", "Bucharest|Cluj-Napoca|Timisoara"),
    "hu": ("Hungary", "EU", "hu", "Budapest|Debrecen"),
    "bg": ("Bulgaria", "EU", "bg", "Sofia|Plovdiv"),
    "hr": ("Croatia", "EU", "hr", "Zagreb|Split"),
    "sk": ("Slovakia", "EU", "sk", "Bratislava|Kosice"),
    "si": ("Slovenia", "EU", "sl", "Ljubljana|Maribor"),
    "lt": ("Lithuania", "EU", "lt", "Vilnius|Kaunas"),
    "lv": ("Latvia", "EU", "lv", "Riga"),
    "ee": ("Estonia", "EU", "et", "Tallinn|Tartu"),
    "lu": ("Luxembourg", "EU", "fr", "Luxembourg City"),
    "mt": ("Malta", "EU", "en", "Valletta|Sliema"),
    "cy": ("Cyprus", "EU", "en", "Nicosia|Limassol"),
}

CITY_ALIASES = {
    "Munich": ["Munchen"], "Cologne": ["Koln"], "Vienna": ["Wien"], "Rome": ["Roma"],
    "Milan": ["Milano"], "Naples": ["Napoli"], "Turin": ["Torino"], "Florence": ["Firenze"],
    "Lisbon": ["Lisboa"], "Warsaw": ["Warszawa"], "Prague": ["Praha"], "Brussels": ["Bruxelles", "Brussel"],
    "Antwerp": ["Antwerpen"], "Ghent": ["Gent"], "The Hague": ["Den Haag"], "Copenhagen": ["Kobenhavn"],
    "Gothenburg": ["Goteborg"], "Seville": ["Sevilla"], "Bucharest": ["Bucuresti"],
}


def locations_for(regions, country_codes=None):
    return [dict(city=city, country=name, country_code=code, region=region, language=language)
            for code, (name, region, language, cities) in COUNTRIES.items()
            if region in regions and (not country_codes or code in country_codes)
            for city in cities.split("|")]


def niche_terms(niche, language="en", custom_keywords=None):
    queries, signals, service = NICHES.get(niche, ([niche], [niche], "AI automation and web development"))
    queries, signals = list(queries), list(signals)
    if language in LOCAL_TERMS and niche in NICHES:
        local = LOCAL_TERMS[language][list(NICHES).index(niche)]
        queries.insert(0, local)
        signals.append(local)
    if custom_keywords:
        queries.extend(custom_keywords)
        signals.extend(custom_keywords)
    return list(dict.fromkeys(queries)), list(dict.fromkeys(signals)), service


@lru_cache(maxsize=1)
def city_coordinates():
    path = Path(__file__).resolve().parent.parent / "assets" / "city_coordinates.json"
    return json.loads(path.read_text(encoding="utf-8"))["cities"] if path.exists() else {}


def plan_queries(settings):
    """Round-robin countries, niches and cities before requesting deeper pages."""
    locations = settings["locations"]
    groups = {}
    for location in locations:
        groups.setdefault(location["country_code"], []).append(location)
    ordered = [items[i] for i in range(max(map(len, groups.values()), default=0))
               for items in groups.values() if i < len(items)]
    queries = []
    for variant in range(8):
        for loc in ordered:
            for niche in settings["niches"]:
                terms, _, _ = niche_terms(niche, loc["language"], settings.get("keywords"))
                if variant >= len(terms):
                    continue
                queries.append({"keyword": terms[variant], "niche": niche, **loc,
                                "query": f'{terms[variant]} {loc["city"]} {loc["country"]} contact'})
    if settings.get("provider") == "free":
        osm = []
        for loc in ordered:
            coordinates = city_coordinates().get(f'{loc["country_code"]}:{loc["city"]}')
            if coordinates:
                osm.append({**loc, **coordinates, "niches": settings["niches"], "source_kind": "osm",
                            "keyword": "OpenStreetMap businesses", "niche": "Multiple niches",
                            "query": f'Business listings around {loc["city"]}, {loc["country"]}'})
        # Both engines receive independently checkpointed, paginated tasks.
        web = [{**query, "source_kind": engine} for query in queries for engine in ("bing", "duckduckgo")]
        return osm + web
    return queries
