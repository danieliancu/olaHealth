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


def clear_health(profile: Profile) -> None:
    """Delete health answers and withdraw the consent to store them."""
    profile.conditions = []
    profile.conditions_note = ""
    profile.health_consent_at = None
    profile.ai_use_health = False
    profile.save(
        update_fields=[
            "conditions", "conditions_note", "health_consent_at", "ai_use_health", "updated_at"
        ]
    )  # fmt: skip


# Answers that are not conditions: never sent.
_NOT_CONDITIONS = {"none", "prefer_not"}


def health_for_ai(profile: Profile | None) -> list[str]:
    """The health conditions the AI provider may see: only with the stored consent and the
    separate switch "Share my health conditions with Ask Foodo" (``ai_use_health``). Names in
    English, plus the user's own "Other" note (shortened)."""
    if profile is None or not profile.ai_use_health or not profile.health_consent_at:
        return []
    out = [
        str(P.CONDITIONS[c])
        for c in profile.conditions
        if c in P.CONDITIONS and c not in _NOT_CONDITIONS and c != "other"
    ]
    note = " ".join((profile.conditions_note or "").split())[:200]
    if "other" in profile.conditions:
        out.append(f"Other: {note}" if note else "Other")
    return out


def finish_onboarding(profile: Profile, selected_conditions: list[str]) -> None:
    """Mark onboarding done. A history of eating disorders starts with calorie figures and
    weight tracking hidden (changeable later in Settings), whether or not the answer itself was
    stored."""
    profile.onboarded = True
    fields = ["onboarded", "updated_at"]
    if "eating_disorder" in selected_conditions:
        profile.show_energy = False
        profile.track_weight = False
        fields += ["show_energy", "track_weight"]
    profile.save(update_fields=fields)


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


def profile_completeness(profile) -> int:
    """Share of the optional profile answers that are filled in (for the identity card)."""
    checks = [
        bool(profile.goal),
        bool(profile.gender),
        profile.age is not None,
        profile.height is not None,
        profile.weight is not None,
        bool(profile.restrictions or profile.restrictions_consent_at),
        any(profile.targets.values()),
    ]
    return round(100 * sum(checks) / len(checks))
