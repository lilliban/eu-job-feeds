"""City and country extraction from free-text location strings.

`location` in the contract is whatever the company wrote, kept verbatim. `city`
and `country_code` are derived from it, and stay None when the string is
something like `Remote - EMEA` that names no place.
"""

from __future__ import annotations

import re

# Countries these feeds actually surface, with the spellings observed. The list
# is intentionally not exhaustive: an unrecognised country yields None, which is
# honest, rather than a wrong guess.
_COUNTRY_NAMES: dict[str, str] = {
    "italy": "IT", "italia": "IT", "italien": "IT", "italie": "IT",
    "germany": "DE", "deutschland": "DE", "germania": "DE", "allemagne": "DE",
    "france": "FR", "francia": "FR", "frankreich": "FR",
    "spain": "ES", "españa": "ES", "espana": "ES", "spagna": "ES", "spanien": "ES",
    "portugal": "PT", "portogallo": "PT",
    "netherlands": "NL", "the netherlands": "NL", "nederland": "NL",
    "holland": "NL", "olanda": "NL", "niederlande": "NL", "pays-bas": "NL",
    "belgium": "BE", "belgië": "BE", "belgique": "BE", "belgien": "BE", "belgio": "BE",
    "luxembourg": "LU", "luxemburg": "LU", "lussemburgo": "LU",
    "switzerland": "CH", "schweiz": "CH", "svizzera": "CH", "suisse": "CH",
    "austria": "AT", "österreich": "AT", "oesterreich": "AT", "autriche": "AT",
    "united kingdom": "GB", "uk": "GB", "u.k.": "GB", "great britain": "GB",
    "england": "GB", "scotland": "GB", "wales": "GB", "regno unito": "GB",
    "grossbritannien": "GB", "großbritannien": "GB",
    "ireland": "IE", "irlanda": "IE", "irland": "IE",
    "poland": "PL", "polska": "PL", "polen": "PL", "polonia": "PL",
    "czech republic": "CZ", "czechia": "CZ", "tschechien": "CZ",
    "slovakia": "SK", "hungary": "HU", "ungarn": "HU", "ungheria": "HU",
    "romania": "RO", "rumänien": "RO", "bulgaria": "BG", "greece": "GR",
    "grecia": "GR", "griechenland": "GR", "croatia": "HR", "slovenia": "SI",
    "serbia": "RS", "estonia": "EE", "latvia": "LV", "lithuania": "LT",
    "sweden": "SE", "sverige": "SE", "schweden": "SE", "svezia": "SE",
    "norway": "NO", "norge": "NO", "norwegen": "NO", "norvegia": "NO",
    "denmark": "DK", "danmark": "DK", "dänemark": "DK", "danimarca": "DK",
    "finland": "FI", "suomi": "FI", "finnland": "FI", "finlandia": "FI",
    "iceland": "IS", "malta": "MT", "cyprus": "CY", "ukraine": "UA",
    "united states": "US", "usa": "US", "u.s.": "US", "u.s.a.": "US",
    "united states of america": "US", "america": "US", "stati uniti": "US",
    "canada": "CA", "kanada": "CA",
    "israel": "IL", "india": "IN", "australia": "AU", "singapore": "SG",
    "japan": "JP", "brazil": "BR", "brasil": "BR", "mexico": "MX",
    "united arab emirates": "AE", "uae": "AE", "turkey": "TR", "türkiye": "TR",
    "south africa": "ZA", "new zealand": "NZ", "china": "CN", "hong kong": "HK",
    "argentina": "AR", "chile": "CL", "colombia": "CO", "peru": "PE",
}

# Cities distinctive enough to imply their country when the string omits it.
# Only unambiguous ones: no "Cambridge", no "Frankfurt (Oder)" vs "Frankfurt".
_CITY_COUNTRY: dict[str, str] = {
    "milan": "IT", "milano": "IT", "rome": "IT", "roma": "IT", "turin": "IT",
    "torino": "IT", "naples": "IT", "napoli": "IT", "bologna": "IT",
    "florence": "IT", "firenze": "IT", "venice": "IT", "venezia": "IT",
    "berlin": "DE", "munich": "DE", "münchen": "DE", "muenchen": "DE",
    "hamburg": "DE", "cologne": "DE", "köln": "DE", "frankfurt": "DE",
    "stuttgart": "DE", "düsseldorf": "DE", "dusseldorf": "DE", "leipzig": "DE",
    "paris": "FR", "lyon": "FR", "marseille": "FR", "toulouse": "FR",
    "bordeaux": "FR", "nantes": "FR", "lille": "FR",
    "madrid": "ES", "barcelona": "ES", "valencia": "ES", "seville": "ES",
    "sevilla": "ES", "bilbao": "ES", "malaga": "ES", "málaga": "ES",
    "lisbon": "PT", "lisboa": "PT", "porto": "PT",
    "amsterdam": "NL", "rotterdam": "NL", "utrecht": "NL", "eindhoven": "NL",
    "the hague": "NL", "den haag": "NL", "groningen": "NL",
    "brussels": "BE", "bruxelles": "BE", "brussel": "BE", "antwerp": "BE",
    "antwerpen": "BE", "ghent": "BE", "gent": "BE", "leuven": "BE",
    "zurich": "CH", "zürich": "CH", "geneva": "CH", "genève": "CH",
    "basel": "CH", "lausanne": "CH", "bern": "CH", "lugano": "CH", "zug": "CH",
    "vienna": "AT", "wien": "AT", "graz": "AT", "salzburg": "AT", "linz": "AT",
    "london": "GB", "manchester": "GB", "birmingham": "GB", "edinburgh": "GB",
    "glasgow": "GB", "bristol": "GB", "leeds": "GB", "liverpool": "GB",
    "dublin": "IE", "cork": "IE", "galway": "IE",
    "warsaw": "PL", "warszawa": "PL", "krakow": "PL", "kraków": "PL",
    "cracow": "PL", "wroclaw": "PL", "wrocław": "PL", "gdansk": "PL", "poznan": "PL",
    "prague": "CZ", "praha": "CZ", "brno": "CZ",
    "budapest": "HU", "bucharest": "RO", "bucuresti": "RO", "cluj": "RO",
    "cluj-napoca": "RO", "sofia": "BG", "athens": "GR", "thessaloniki": "GR",
    "zagreb": "HR", "ljubljana": "SI", "belgrade": "RS", "tallinn": "EE",
    "riga": "LV", "vilnius": "LT",
    "stockholm": "SE", "gothenburg": "SE", "göteborg": "SE", "malmo": "SE",
    "malmö": "SE", "oslo": "NO", "bergen": "NO", "trondheim": "NO",
    "copenhagen": "DK", "københavn": "DK", "kobenhavn": "DK", "aarhus": "DK",
    "helsinki": "FI", "espoo": "FI", "tampere": "FI",
    "luxembourg city": "LU", "reykjavik": "IS", "valletta": "MT",
    "new york": "US", "new york city": "US", "nyc": "US", "san francisco": "US",
    "boston": "US", "chicago": "US", "seattle": "US", "austin": "US",
    "los angeles": "US", "denver": "US", "atlanta": "US", "miami": "US",
    "toronto": "CA", "vancouver": "CA", "montreal": "CA", "montréal": "CA",
    "tel aviv": "IL", "bangalore": "IN", "bengaluru": "IN", "mumbai": "IN",
    "sydney": "AU", "melbourne": "AU", "tokyo": "JP", "são paulo": "BR",
    "sao paulo": "BR", "mexico city": "MX", "dubai": "AE", "istanbul": "TR",
}

# Fragments that are not places.
_NON_PLACE = re.compile(
    r"^(?:remote|remoto|anywhere|worldwide|global|flexible|distributed|"
    r"emea|apac|amer|americas|latam|nam|europe|europa|eu|worldwide\s+remote|"
    r"multiple\s+locations|various|home\s?office|hybrid|onsite|on-site|"
    r"n/?a|tbd|unspecified|-{1,3})$",
    re.IGNORECASE,
)

_ISO2 = re.compile(r"^[A-Z]{2}$")
_SPLIT = re.compile(r"\s*[,;/|·•]\s*|\s+[-–—]\s+")


def _lookup_country(token: str) -> str | None:
    key = token.strip().lower().rstrip(".")
    if key in _COUNTRY_NAMES:
        return _COUNTRY_NAMES[key]
    if len(key) == 3 and key.upper() in {"USA", "GBR", "DEU", "ITA", "FRA", "ESP", "NLD", "CHE", "AUT", "BEL", "PRT", "POL", "SWE", "DNK", "NOR", "FIN", "IRL", "CZE", "GRC", "ROU", "HUN"}:
        return {
            "USA": "US", "GBR": "GB", "DEU": "DE", "ITA": "IT", "FRA": "FR",
            "ESP": "ES", "NLD": "NL", "CHE": "CH", "AUT": "AT", "BEL": "BE",
            "PRT": "PT", "POL": "PL", "SWE": "SE", "DNK": "DK", "NOR": "NO",
            "FIN": "FI", "IRL": "IE", "CZE": "CZ", "GRC": "GR", "ROU": "RO",
            "HUN": "HU",
        }[key.upper()]
    return None


def normalize_country_code(raw: str | None) -> str | None:
    """Turn a country name or code into ISO 3166-1 alpha-2.

    Providers are inconsistent: SmartRecruiters sends `pl`, Ashby sends `USA`,
    Recruitee sends `NL`, Workable sends `United States`.
    """
    if not raw:
        return None
    value = raw.strip()
    if not value:
        return None
    if _ISO2.match(value.upper()) and value.upper() not in {"UK", "EU"}:
        return value.upper()
    if value.upper() == "UK":
        return "GB"
    return _lookup_country(value)


def split_location(raw: str | None) -> tuple[str | None, str | None]:
    """Return `(city, country_code)` from a free-text location string.

    The city is the first component, as the contract specifies. The country is
    looked for in the last components first, then inferred from a known city.
    """
    if not raw:
        return None, None
    value = raw.strip()
    if not value or _NON_PLACE.match(value):
        return None, None

    parts = [p.strip() for p in _SPLIT.split(value) if p.strip()]
    if not parts:
        return None, None

    country = None
    for part in reversed(parts):
        country = normalize_country_code(part) if len(part) <= 3 else _lookup_country(part)
        if country:
            break

    city = None
    for part in parts:
        cleaned = re.sub(r"\s*\((?:remote|hybrid|onsite|hq|office)\)\s*", "", part, flags=re.I).strip()
        if not cleaned or _NON_PLACE.match(cleaned):
            continue
        if _lookup_country(cleaned) and len(parts) > 1:
            continue  # this component is the country, not the city
        city = cleaned
        break

    if country is None and city:
        country = _CITY_COUNTRY.get(city.lower())
    if country is None:
        for part in parts:
            hit = _CITY_COUNTRY.get(part.strip().lower())
            if hit:
                country = hit
                break

    # A single token that is only a country name is not a city.
    if city and len(parts) == 1 and _lookup_country(city):
        city = None

    return city, country
