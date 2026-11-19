"""Profile access and the preferences derived from it (diets and allergens)."""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.utils import timezone

from myola import preferences as P
from myola.models import Profile

DIET_ORDER = list(P.DIETS)
ALLERGEN_ORDER = list(P.ALLERGENS)


def get_profile(user) -> Profile:
    profile, _ = Profile.objects.get_or_create(user=user)
    return profile


def derive_prefs(profile: Profile | None) -> dict[str, list[str]]:
    """Diets and allergens used on product pages, from the onboarding restrictions.

    "Religious restriction" and "Other" are notes only: free text never creates a rule.
    "Lactose intolerant" maps to the lactose-free diet, not to a milk allergy.
    """
    if profile is None:
        return {"goals": [], "diets": [], "allergens": []}
    diets: set[str] = set()
    allergens: set[str] = set()
    for key in profile.restrictions:
        if key not in P.RESTRICTIONS:
            continue
        _label, _icon, r_diets, r_allergens = P.RESTRICTIONS[key]
        diets.update(r_diets)
        if key == "nuts":
            allergens.update(profile.nut_types or r_allergens)
        elif key == "seafood":
            allergens.update(profile.seafood_types or r_allergens)
        else:
            allergens.update(r_allergens)
    return {
        "goals": [],
        "diets": [d for d in DIET_ORDER if d in diets],
        "allergens": [a for a in ALLERGEN_ORDER if a in allergens],
    }


def user_tz(profile: Profile | None) -> dt.tzinfo:
    try:
        return ZoneInfo(profile.timezone) if profile else timezone.get_current_timezone()
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("Europe/London")


def local_today(profile: Profile | None) -> dt.date:
    return timezone.now().astimezone(user_tz(profile)).date()


def parse_day(value: str | None, profile: Profile | None) -> dt.date:
    """A ``?date=YYYY-MM-DD`` value, never in the future; today when missing or invalid."""
    today = local_today(profile)
    try:
        day = dt.date.fromisoformat(value or "")
    except ValueError:
        return today
    return min(day, today)
