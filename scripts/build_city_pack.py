"""Rebuild the small bundled city-coordinate pack from GeoNames (CC BY 4.0)."""
import io
import json
from pathlib import Path
import sys
import zipfile
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.markets import locations_for, normalized, CITY_ALIASES

URL = "https://download.geonames.org/export/dump/cities15000.zip"

if __name__ == "__main__":
    response = requests.get(URL, timeout=60)
    response.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        rows = [line.split("\t") for line in archive.read("cities15000.txt").decode("utf-8").splitlines()]
    index = {}
    for row in rows:
        if row[6] != "P":
            continue
        for alias in [row[1], row[2]] + row[3].split(","):
            key = (row[8].lower(), normalized(alias))
            if key not in index or int(row[14] or 0) > int(index[key][14] or 0):
                index[key] = row
    entries, missing = {}, []
    for loc in locations_for(["US", "UK", "EU"]):
        aliases = [loc["city"]] + CITY_ALIASES.get(loc["city"], [])
        if loc["city"] == "Luxembourg City":
            aliases.append("Luxembourg")
        matches = [index[(loc["country_code"], normalized(alias))] for alias in aliases
                   if (loc["country_code"], normalized(alias)) in index]
        if not matches:
            missing.append(loc["city"])
            continue
        row = max(matches, key=lambda r: int(r[14] or 0))
        entries[f'{loc["country_code"]}:{loc["city"]}'] = {
            "lat": float(row[4]), "lon": float(row[5]), "geonames_id": row[0], "source_name": row[1]}
    if missing:
        raise SystemExit("Unmatched cities: " + ", ".join(missing))
    output = ROOT / "assets" / "city_coordinates.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"source": URL, "attribution": "GeoNames, CC BY 4.0", "cities": entries}, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Saved {len(entries)} city coordinates; no geocoding API required.")
