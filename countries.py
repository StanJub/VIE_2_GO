"""Shared, deterministic country resolution for every source (no network/fuzzy matching)."""
import re
import unicodedata
from functools import lru_cache

import pycountry
from babel import Locale

# Add a source language here when integrating a new connector.
LANGUAGES = ("fr", "en", "de", "es", "it", "pt")
COUNTRIES = {c.alpha_2: c for c in pycountry.countries}
FRENCH = Locale.parse("fr").territories
ALIASES = {
    "uk": "GB", "usa": "US", "viet nam": "VN",
    "tchequie / republique tcheque": "CZ", "republique tcheque": "CZ",
    "south korea": "KR", "north korea": "KP", "turkey": "TR",
    "russia": "RU", "republique du congo": "CG",
    "republique democratique du congo": "CD", "congo brazzaville": "CG",
    "congo kinshasa": "CD",
}
# Only add overrides when the source's contract removes an ambiguity.
SOURCE_ALIASES = {}
# Exact city context only, never a global alias for the ambiguous country.
# https://www.diplomatie.gouv.fr/fr/information-par-pays/congo/conseils-aux-voyageurs-contacts-utiles
CITY_HINTS = {"congo": {"pointe noire": "CG", "brazzaville": "CG", "kinshasa": "CD"}}
AMBIGUOUS = {"congo", "korea", "coree"}


def key(value):
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^\w]+", " ", text).strip()


INDEX = {}
def _add(name, code):
    INDEX.setdefault(key(name), set()).add(code)

for code, country in COUNTRIES.items():
    for field in ("alpha_2", "alpha_3", "name", "official_name", "common_name"):
        name = getattr(country, field, None)
        if name:
            _add(name, code)
for language in LANGUAGES:
    for code, name in Locale.parse(language).territories.items():
        if code in COUNTRIES:
            _add(name, code)
ALIASES = {key(name): code for name, code in ALIASES.items()}


@lru_cache(maxsize=2048)
def resolve_country(raw, source="", country_code=None, city=""):
    """Return ISO alpha-2 or None; explicit validated source codes take precedence."""
    if country_code and str(country_code).upper() in COUNTRIES:
        return str(country_code).upper()
    normalized = key(raw)
    override = SOURCE_ALIASES.get(source, {}).get(normalized)
    if override in COUNTRIES:
        return override
    if normalized in AMBIGUOUS:
        return CITY_HINTS.get(normalized, {}).get(key(city))
    if normalized in ALIASES:
        return ALIASES[normalized]
    candidates = INDEX.get(normalized, set())
    return next(iter(candidates)) if len(candidates) == 1 else None


def normalize_offer(offer):
    """Return a copy, retaining the exact original value and all other offer fields."""
    result = dict(offer)
    raw = result.get("country_raw", result.get("country", "")) or ""
    code = resolve_country(raw, result.get("source", ""), result.get("country_code"), result.get("city", ""))
    result.update(country_raw=raw, country_code=code,
                  country=FRENCH[code] if code else raw,
                  country_status="resolved" if code else "unresolved")
    return result


def country_identity(offer):
    return offer.get("country_code") or "raw:" + key(offer.get("country_raw", offer.get("country")))


def normalize_cache(data):
    """Normalize legacy caches without modifying freshness dates or losing offers."""
    result = dict(data)
    result["offers"] = [normalize_offer(o) for o in data["offers"]]
    unresolved = sorted({(o.get("source", ""), o["country_raw"]) for o in result["offers"]
                         if o["country_status"] == "unresolved"})
    result["metadata"] = dict(data["metadata"], country_schema_version=1,
                               unresolved_countries=[{"source": s, "value": v} for s, v in unresolved])
    return result
