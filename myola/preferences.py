"""Profile and preference vocabulary (onboarding, My Track and the restriction rules).

Kinds that are never mixed:

* goal: one of four general goals (stored, never turned into calorie targets in the MVP);
* diets: strict restrictions (a product is compatible, incompatible or unknown);
* allergies: strict restrictions per allergen (contains, may contain, unknown, not declared).

Restrictions chosen in onboarding map to diets and allergens (see ``RESTRICTIONS`` and
static/js/ola/store.js ``derivePrefs``). "Religious restriction" and "Other" keep a free note
only: free text never creates a compatibility claim. "Lactose intolerant" maps to the
lactose-free diet, which is not the same as a milk allergy.
"""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

GOALS = {
    "lose_weight": (_("Losing weight"), "scale"),
    "muscle_and_fat": (_("Gaining muscle and losing fat"), "dumbbell"),
    "muscle_first": (_("Gaining muscle, losing fat is secondary"), "biceps-flexed"),
    "eat_healthier": (_("Eating healthier without losing weight"), "apple"),
}
GENDERS = {
    "male": _("Male"),
    "female": _("Female"),
    "non_binary": _("Non-binary"),
    "prefer_not": _("Prefer not to say"),
    "other": _("Other"),
}
DIETS = {
    "vegetarian": (_("Vegetarian"), "salad"),
    "vegan": (_("Vegan"), "vegan"),
    "pescatarian": (_("Pescetarian"), "fish"),
    "gluten_free": (_("Gluten-free"), "wheat-off"),
    "lactose_free": (_("Lactose-free"), "milk-off"),
    "pork_free": (_("Pork-free"), "ham"),
}
# The 14 allergens UK/EU law requires on labels, as Open Food Facts allergen tags
# (the allergen taxonomy the app reads in ``allergens``/``traces``).
ALLERGENS = {
    "en:gluten": _("Gluten (cereals)"),
    "en:crustaceans": _("Crustaceans"),
    "en:eggs": _("Eggs"),
    "en:fish": _("Fish"),
    "en:peanuts": _("Peanuts"),
    "en:soybeans": _("Soya"),
    "en:milk": _("Milk"),
    "en:nuts": _("Tree nuts"),
    "en:celery": _("Celery"),
    "en:mustard": _("Mustard"),
    "en:sesame-seeds": _("Sesame"),
    "en:sulphur-dioxide-and-sulphites": _("Sulphites"),
    "en:lupin": _("Lupin"),
    "en:molluscs": _("Molluscs"),
}
# Onboarding step 3: key -> (label, icon, diets, allergens)
RESTRICTIONS = {
    "vegan": (_("Veganism"), "vegan", ["vegan"], []),
    # Soft hyphens: long words break as "Vegetarian-ism" only when the card is narrow.
    "vegetarian": (_("Vegetarian­ism"), "salad", ["vegetarian"], []),
    "pescatarian": (_("Pescetarian­ism"), "fish", ["pescatarian"], []),
    "gluten_free": (_("Gluten-free"), "wheat-off", ["gluten_free"], []),
    "lactose": (_("Lactose intolerant"), "milk-off", ["lactose_free"], []),
    "nuts": (_("Nut allergy"), "nut", [], ["en:peanuts", "en:nuts"]),
    "seafood": (
        _("Seafood or shellfish"),
        "shell",
        [],
        ["en:fish", "en:crustaceans", "en:molluscs"],
    ),
    "religious": (_("Religious restriction"), "hand-heart", [], []),
    "other": (_("Other"), "ellipsis", [], []),
    "none": (_("None"), "circle-off", [], []),
}
NUT_TYPES = {"en:peanuts": _("Peanuts"), "en:nuts": _("Tree nuts")}
SEAFOOD_TYPES = {
    "en:fish": _("Fish"),
    "en:crustaceans": _("Crustaceans"),
    "en:molluscs": _("Molluscs"),
}
CONDITIONS = {
    "hypertension": _("Hypertension or high blood pressure"),
    "diabetes": _("Diabetes"),
    "depression": _("Depression"),
    "eating_disorder": _("Eating disorders (anorexia, bulimia)"),
    "other": _("Other"),
    "none": _("None"),
    "prefer_not": _("Prefer not to answer"),
}
ACTIVITIES = {
    "walking": (_("Walking"), "footprints"),
    "running": (_("Running"), "person-standing"),
    "gym": (_("Gym"), "dumbbell"),
    "cycling": (_("Cycling"), "bike"),
    "swimming": (_("Swimming"), "waves"),
    "other": (_("Other"), "activity"),
}

# Daily activity for estimating energy needs: (label, description, physical activity level).
# PAL values follow the FAO/WHO/UNU and SACN (2011) approach: energy = resting energy x PAL.
ACTIVITY_LEVELS = {
    "sedentary": (_("Mostly sitting"), _("Desk job, little exercise"), 1.4),
    "light": (
        _("Lightly active"),
        _("On your feet some of the day, or exercise 1-3 days a week"),
        1.6,
    ),
    "active": (_("Active"), _("Active job, or exercise 3-5 days a week"), 1.8),
    "very_active": (_("Very active"), _("Physical job, or hard exercise most days"), 2.0),
}


def picker_context() -> dict:
    """Options for the onboarding steps, the restriction picker and My Track."""
    return {
        "goals": [{"key": k, "label": label, "icon": icon} for k, (label, icon) in GOALS.items()],
        "genders": [{"key": k, "label": label} for k, label in GENDERS.items()],
        "restrictions": [{"key": k, "label": v[0], "icon": v[1]} for k, v in RESTRICTIONS.items()],
        "nut_types": [{"key": k, "label": label} for k, label in NUT_TYPES.items()],
        "seafood_types": [{"key": k, "label": label} for k, label in SEAFOOD_TYPES.items()],
        "conditions": [{"key": k, "label": label} for k, label in CONDITIONS.items()],
        "activities": [
            {"key": k, "label": label, "icon": icon} for k, (label, icon) in ACTIVITIES.items()
        ],
    }
