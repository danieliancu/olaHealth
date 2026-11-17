"""Countries offered in the optional country selector (Open Food Facts country tags).

The country never restricts the global lookup. It is only used to scope store-internal
(restricted-circulation) codes and as a default for contributions.
"""

COUNTRY_CHOICES: list[tuple[str, str]] = [
    ("", "Not specified"),
    ("en:austria", "Austria"),
    ("en:belgium", "Belgium"),
    ("en:bulgaria", "Bulgaria"),
    ("en:canada", "Canada"),
    ("en:croatia", "Croatia"),
    ("en:czech-republic", "Czechia"),
    ("en:denmark", "Denmark"),
    ("en:finland", "Finland"),
    ("en:france", "France"),
    ("en:germany", "Germany"),
    ("en:greece", "Greece"),
    ("en:hungary", "Hungary"),
    ("en:ireland", "Ireland"),
    ("en:italy", "Italy"),
    ("en:moldova", "Moldova"),
    ("en:netherlands", "Netherlands"),
    ("en:norway", "Norway"),
    ("en:poland", "Poland"),
    ("en:portugal", "Portugal"),
    ("en:romania", "Romania"),
    ("en:serbia", "Serbia"),
    ("en:slovakia", "Slovakia"),
    ("en:slovenia", "Slovenia"),
    ("en:spain", "Spain"),
    ("en:sweden", "Sweden"),
    ("en:switzerland", "Switzerland"),
    ("en:united-kingdom", "United Kingdom"),
    ("en:united-states", "United States"),
]
COUNTRY_TAGS = {tag for tag, _label in COUNTRY_CHOICES if tag}

REGION_TO_TAG = {
    "AT": "en:austria",
    "BE": "en:belgium",
    "BG": "en:bulgaria",
    "CA": "en:canada",
    "HR": "en:croatia",
    "CZ": "en:czech-republic",
    "DK": "en:denmark",
    "FI": "en:finland",
    "FR": "en:france",
    "DE": "en:germany",
    "GR": "en:greece",
    "HU": "en:hungary",
    "IE": "en:ireland",
    "IT": "en:italy",
    "MD": "en:moldova",
    "NL": "en:netherlands",
    "NO": "en:norway",
    "PL": "en:poland",
    "PT": "en:portugal",
    "RO": "en:romania",
    "RS": "en:serbia",
    "SK": "en:slovakia",
    "SI": "en:slovenia",
    "ES": "en:spain",
    "SE": "en:sweden",
    "CH": "en:switzerland",
    "GB": "en:united-kingdom",
    "US": "en:united-states",
}
# Languages spoken mainly in one listed country (used when no region is given).
LANGUAGE_TO_TAG = {
    "ro": "en:romania",
    "bg": "en:bulgaria",
    "hu": "en:hungary",
    "pl": "en:poland",
    "cs": "en:czech-republic",
    "sk": "en:slovakia",
    "sl": "en:slovenia",
    "hr": "en:croatia",
    "sr": "en:serbia",
    "da": "en:denmark",
    "fi": "en:finland",
    "el": "en:greece",
    "it": "en:italy",
    "nl": "en:netherlands",
    "sv": "en:sweden",
    "nb": "en:norway",
    "pt": "en:portugal",
}


def country_from_accept_language(header: str) -> str:
    """Best-effort country from the browser's Accept-Language (no geolocation).

    "ro-RO,ro;q=0.9,en;q=0.8" -> "en:romania". Returns "" when unsure.
    """
    for part in header.split(",")[:5]:
        tag = part.split(";")[0].strip().replace("_", "-")
        if not tag or tag == "*":
            continue
        pieces = tag.split("-")
        if len(pieces) >= 2 and pieces[-1].upper() in REGION_TO_TAG:
            return REGION_TO_TAG[pieces[-1].upper()]
        if pieces[0].lower() in LANGUAGE_TO_TAG:
            return LANGUAGE_TO_TAG[pieces[0].lower()]
    return ""
