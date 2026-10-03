"""
Free-text country references -> ISO 3166-1 alpha-2 codes.

The intake form captures countries as free text
(Assessment.countries_jurisdictions), so "Nigeria", "NIGERIA", "NG",
"NGA" and "Federal Republic of Nigeria" all have to reach the same
lookup key before any jurisdiction rule can work. Substring keyword
matching cannot do this reliably, which is why the risk engine's
existing keyword approach misses most real intake text.

Two properties this module is built around:

* Unresolved input is returned, never swallowed. If an analyst types
  "our West Africa rollout", that is not a country and the caller must
  be able to say so rather than silently concluding "no high-risk
  jurisdictions involved" -- a false negative here is the dangerous
  direction.
* No fuzzy matching. A near-miss that silently resolves to the wrong
  country is worse than an explicit "unrecognised" the analyst can fix.
  Matching is exact against a known set of names, codes and aliases.
"""

import re
import unicodedata

# ISO 3166-1 alpha-2 -> canonical display name, for every jurisdiction
# that currently appears on a list we load, plus the commonly-referenced
# countries most likely to show up in intake text. A country absent from
# here is reported as unresolved rather than guessed at.
ISO_NAMES: dict[str, str] = {
    "AE": "United Arab Emirates",
    "AF": "Afghanistan",
    "AO": "Angola",
    "AR": "Argentina",
    "AU": "Australia",
    "BA": "Bosnia and Herzegovina",
    "BD": "Bangladesh",
    "BG": "Bulgaria",
    "BO": "Bolivia",
    "BR": "Brazil",
    "CA": "Canada",
    "CD": "Congo, Democratic Republic of the",
    "CH": "Switzerland",
    "CI": "Cote d'Ivoire",
    "CM": "Cameroon",
    "CN": "China",
    "DE": "Germany",
    "DZ": "Algeria",
    "EG": "Egypt",
    "ES": "Spain",
    "FR": "France",
    "GB": "United Kingdom",
    "GH": "Ghana",
    "HK": "Hong Kong",
    "HT": "Haiti",
    "ID": "Indonesia",
    "IE": "Ireland",
    "IN": "India",
    "IQ": "Iraq",
    "IR": "Iran",
    "IT": "Italy",
    "JP": "Japan",
    "KE": "Kenya",
    "KP": "Korea, Democratic People's Republic of",
    "KR": "Korea, Republic of",
    "KW": "Kuwait",
    "LA": "Lao People's Democratic Republic",
    "LB": "Lebanon",
    "LU": "Luxembourg",
    "MC": "Monaco",
    "MM": "Myanmar",
    "MX": "Mexico",
    "MY": "Malaysia",
    "NA": "Namibia",
    "NG": "Nigeria",
    "NL": "Netherlands",
    "NP": "Nepal",
    "NZ": "New Zealand",
    "PG": "Papua New Guinea",
    "PH": "Philippines",
    "PK": "Pakistan",
    "PL": "Poland",
    "PT": "Portugal",
    "QA": "Qatar",
    "RU": "Russian Federation",
    "SA": "Saudi Arabia",
    "SE": "Sweden",
    "SG": "Singapore",
    "SS": "South Sudan",
    "SY": "Syrian Arab Republic",
    "TH": "Thailand",
    "TR": "Turkiye",
    "TT": "Trinidad and Tobago",
    "TZ": "Tanzania",
    "UA": "Ukraine",
    "UG": "Uganda",
    "US": "United States",
    "VE": "Venezuela",
    "VG": "Virgin Islands, British",
    "VU": "Vanuatu",
    "VN": "Viet Nam",
    "YE": "Yemen",
    "ZA": "South Africa",
}

# alpha-3 -> alpha-2, for the same set.
ALPHA3_TO_ALPHA2: dict[str, str] = {
    "ARE": "AE", "AFG": "AF", "AGO": "AO", "ARG": "AR", "AUS": "AU", "BIH": "BA",
    "BGD": "BD", "BGR": "BG", "BOL": "BO", "BRA": "BR", "CAN": "CA",
    "COD": "CD", "CHE": "CH", "CIV": "CI", "CMR": "CM", "CHN": "CN",
    "DEU": "DE", "DZA": "DZ", "EGY": "EG", "ESP": "ES", "FRA": "FR", "GBR": "GB",
    "GHA": "GH", "HKG": "HK", "HTI": "HT", "IDN": "ID", "IRL": "IE",
    "IND": "IN", "IRQ": "IQ", "IRN": "IR", "ITA": "IT", "JPN": "JP",
    "KEN": "KE", "PRK": "KP", "KOR": "KR", "KWT": "KW", "LAO": "LA",
    "LBN": "LB", "LUX": "LU", "MCO": "MC", "MMR": "MM", "MEX": "MX",
    "MYS": "MY", "NAM": "NA", "NGA": "NG", "NLD": "NL", "NPL": "NP", "NZL": "NZ",
    "PNG": "PG", "PHL": "PH", "PAK": "PK", "POL": "PL", "PRT": "PT",
    "QAT": "QA", "RUS": "RU", "SAU": "SA", "SWE": "SE", "SGP": "SG",
    "SSD": "SS", "SYR": "SY", "THA": "TH", "TUR": "TR", "TTO": "TT", "TZA": "TZ",
    "UKR": "UA", "UGA": "UG", "USA": "US", "VEN": "VE", "VGB": "VG", "VUT": "VU",
    "VNM": "VN", "YEM": "YE", "ZAF": "ZA",
}

# Everything else people actually write. Keys here are already
# normalised (see _normalise): uppercased, de-accented, punctuation
# stripped, whitespace collapsed.
ALIASES: dict[str, str] = {
    "UAE": "AE",
    "EMIRATES": "AE",
    "DUBAI": "AE",
    "ABU DHABI": "AE",
    "BOSNIA": "BA",
    "BOSNIA HERZEGOVINA": "BA",
    "DRC": "CD",
    "DR CONGO": "CD",
    "DEMOCRATIC REPUBLIC OF CONGO": "CD",
    "DEMOCRATIC REPUBLIC OF THE CONGO": "CD",
    "CONGO KINSHASA": "CD",
    "IVORY COAST": "CI",
    "COTE DIVOIRE": "CI",
    "REPUBLIC OF COTE DIVOIRE": "CI",
    "NORTH KOREA": "KP",
    "DPRK": "KP",
    "KOREA DPR": "KP",
    "SOUTH KOREA": "KR",
    "REPUBLIC OF KOREA": "KR",
    "LAOS": "LA",
    "BURMA": "MM",
    "VIETNAM": "VN",
    "SOCIALIST REPUBLIC OF VIETNAM": "VN",
    "BVI": "VG",
    "TRINIDAD": "TT",
    "TRINIDAD AND TOBAGO": "TT",
    "TRINIDAD TOBAGO": "TT",
    "BRITISH VIRGIN ISLANDS": "VG",
    "VIRGIN ISLANDS UK": "VG",
    "SYRIA": "SY",
    "RUSSIA": "RU",
    "TURKEY": "TR",
    "UK": "GB",
    "GREAT BRITAIN": "GB",
    "UNITED KINGDOM OF GREAT BRITAIN AND NORTHERN IRELAND": "GB",
    "ENGLAND": "GB",
    "USA": "US",
    "UNITED STATES OF AMERICA": "US",
    "FEDERAL REPUBLIC OF NIGERIA": "NG",
    "IRAN ISLAMIC REPUBLIC OF": "IR",
    "ISLAMIC REPUBLIC OF IRAN": "IR",
    "TANZANIA UNITED REPUBLIC OF": "TZ",
    "HONG KONG SAR": "HK",
    "PRC": "CN",
    "PEOPLES REPUBLIC OF CHINA": "CN",
}

# Common prefixes/suffixes that carry no identifying information.
_NOISE = re.compile(
    r"^(THE|REPUBLIC OF|STATE OF|KINGDOM OF|FEDERATION OF)\s+|"
    r"\s+(REPUBLIC|FEDERATION)$"
)


def _normalise(value: str) -> str:
    """Uppercase, strip accents and punctuation, collapse whitespace."""

    decomposed = unicodedata.normalize("NFKD", value)
    without_accents = "".join(c for c in decomposed if not unicodedata.combining(c))
    cleaned = re.sub(r"[^A-Za-z\s]", " ", without_accents)
    return re.sub(r"\s+", " ", cleaned).strip().upper()


# Normalised canonical name -> alpha-2, built once from ISO_NAMES.
_NAME_TO_ALPHA2 = {_normalise(name): code for code, name in ISO_NAMES.items()}


def resolve_country(value: str) -> str | None:
    """
    One free-text country reference -> alpha-2, or None if it is not a
    country this module knows. Never guesses.
    """

    if not value or not value.strip():
        return None

    normalised = _normalise(value)

    if not normalised:
        return None

    # Bare codes first: "NG", "NGA".
    if len(normalised) == 2 and normalised in ISO_NAMES:
        return normalised
    if len(normalised) == 3 and normalised in ALPHA3_TO_ALPHA2:
        return ALPHA3_TO_ALPHA2[normalised]

    for candidate in (normalised, _NOISE.sub("", normalised).strip()):
        if candidate in ALIASES:
            return ALIASES[candidate]
        if candidate in _NAME_TO_ALPHA2:
            return _NAME_TO_ALPHA2[candidate]

    return None


def resolve_countries(values: list[str]) -> tuple[dict[str, str], list[str]]:
    """
    A list of free-text references -> ({alpha-2: the text that matched},
    [text that could not be resolved]).

    The caller is expected to surface the unresolved list. Treating it as
    "nothing to see here" turns unrecognised input into a silent
    false negative on jurisdiction risk.
    """

    resolved: dict[str, str] = {}
    unresolved: list[str] = []

    for value in values:
        text = (value or "").strip()
        if not text:
            continue

        code = resolve_country(text)
        if code:
            resolved.setdefault(code, text)
        else:
            unresolved.append(text)

    return resolved, unresolved
