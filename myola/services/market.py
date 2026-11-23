"""The country (market) for alternatives, worked out from the browser; the user never picks it.

1. The browser's time zone (``Intl.DateTimeFormat().resolvedOptions().timeZone``, sent by the
   alternatives request and kept in the session), mapped to its country with the IANA
   ``zone.tab`` table: ``Europe/London`` -> United Kingdom, ``Europe/Bucharest`` -> Romania.
   It follows where the device is set, so it comes first.
2. Otherwise the region of the browser language (``Accept-Language: en-GB`` -> United
   Kingdom, ``ro-RO`` -> Romania).
3. Otherwise unknown: alternatives are searched in every country and no country is named.

Nothing is asked of the user, no permission prompt, and no IP address is looked up or sent
anywhere. Countries are Open Food Facts country tags (``catalog.Country``); products are
"listed for this market on Open Food Facts", which says nothing about stock in shops.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from django.utils.translation import gettext as _

SESSION_TZ = "browser_tz"
ZONE_TABLES = (Path("/usr/share/zoneinfo/zone.tab"), Path("/usr/share/zoneinfo/zone1970.tab"))
# OFF's own country codes that differ from ISO 3166-1 alpha-2.
OFF_CODES = {"GB": "UK"}
# Countries whose English name takes "the" in a sentence ("Better alternatives in the UK").
SHORT_NAMES = {
    "en:united-kingdom": "the UK",
    "en:united-states": "the US",
    "en:netherlands": "the Netherlands",
    "en:czech-republic": "the Czech Republic",
    "en:philippines": "the Philippines",
    "en:united-arab-emirates": "the United Arab Emirates",
}
# Used when the country taxonomy has not been synced yet (fresh install, tests): ISO code -> tag.
FALLBACK = {
    "GB": ("en:united-kingdom", "United Kingdom"),
    "IE": ("en:ireland", "Ireland"),
    "RO": ("en:romania", "Romania"),
    "FR": ("en:france", "France"),
    "DE": ("en:germany", "Germany"),
    "ES": ("en:spain", "Spain"),
    "IT": ("en:italy", "Italy"),
    "US": ("en:united-states", "United States"),
}
_TZ = re.compile(r"^[A-Za-z_]+(?:/[A-Za-z0-9_+\-]+){1,2}$")
_LANG_REGION = re.compile(r"^[a-z]{2,3}[-_]([A-Za-z]{2})$")


@lru_cache(maxsize=1)
def _zones() -> dict[str, str]:
    """IANA time zone -> ISO country code, from the system tz database."""
    zones: dict[str, str] = {}
    for path in ZONE_TABLES:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            if line.startswith("#") or not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) >= 3:
                for code in parts[0].split(","):
                    zones.setdefault(parts[2], code)
    return zones


def country_from_timezone(tz: str) -> str | None:
    """ISO country code of an IANA time zone, or ``None`` (unknown, or a zone like UTC)."""
    if not tz or not _TZ.match(tz):
        return None
    return _zones().get(tz)


def country_from_language(header: str) -> str | None:
    """ISO country code of the first language with a region in ``Accept-Language``."""
    for part in (header or "").split(","):
        lang = part.split(";", 1)[0].strip()
        match = _LANG_REGION.match(lang)
        if match:
            return match.group(1).upper()
    return None


def tag_for(code: str | None) -> str | None:
    """The OFF country tag of an ISO country code, or ``None``."""
    from catalog.models import Country

    if not code:
        return None
    code = code.upper()
    off_code = OFF_CODES.get(code, code)
    if Country.objects.exists():
        tag = (
            Country.objects.filter(code__iexact=off_code).values_list("tag", flat=True).first()
            or Country.objects.filter(code__iexact=code).values_list("tag", flat=True).first()
        )
        if tag:
            return tag
    return FALLBACK.get(code, (None, ""))[0]


def remember_timezone(request, tz: str) -> None:
    """Keep the browser's time zone for later requests (session only)."""
    if tz and _TZ.match(tz) and hasattr(request, "session"):
        request.session[SESSION_TZ] = tz


def country_for(request) -> str | None:
    """The detected market (OFF tag), or ``None`` when the browser does not tell."""
    tz = request.session.get(SESSION_TZ, "") if hasattr(request, "session") else ""
    tag = tag_for(country_from_timezone(tz))
    if tag:
        return tag
    return tag_for(country_from_language(request.META.get("HTTP_ACCEPT_LANGUAGE", "")))


def name(tag: str) -> str:
    """The country as it reads after "in": "the UK", "Romania"..."""
    from catalog.models import Country

    if tag in SHORT_NAMES:
        return _(SHORT_NAMES[tag])
    row = Country.objects.filter(tag=tag).values_list("name", flat=True).first()
    fallback = {t: n for t, n in FALLBACK.values()}
    return row or fallback.get(tag) or tag.split(":", 1)[-1].replace("-", " ").title()
