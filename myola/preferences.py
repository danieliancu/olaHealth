"""Preference vocabulary shared by the picker and the restriction rules.

Three separate kinds, never mixed:

* goals: nutritional goals (their values are shown first on each product);
* diets: strict restrictions (a product is compatible, incompatible or unknown);
* allergies: strict restrictions per allergen (contains, may contain, unknown, not declared).

"Lactose-free" (a diet) and a milk allergy are different choices.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations

from django.utils.translation import gettext_lazy as _

GOALS = {
    "lower_sugar": (_("Lower sugar"), "candy"),
    "more_protein": (_("More protein"), "egg"),
}
DIETS = {
    "vegetarian": (_("Vegetarian"), "salad"),
    "vegan": (_("Vegan"), "vegan"),
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


def goal_key(goals: list[str] | tuple[str, ...] | set[str]) -> str:
    """Stable key of a goal combination: ``none``, ``lower_sugar``, ``lower_sugar+more_protein``."""
    active = [g for g in GOALS if g in set(goals)]
    return "+".join(active) or "none"


def goal_combinations() -> list[tuple[str, ...]]:
    keys = list(GOALS)
    return [c for n in range(len(keys) + 1) for c in combinations(keys, n)]


@dataclass(frozen=True)
class Preferences:
    goals: tuple[str, ...] = ()
    diets: tuple[str, ...] = ()
    allergens: tuple[str, ...] = ()
    avoid: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def build(cls, goals=(), diets=(), allergens=(), avoid=()) -> Preferences:
        return cls(
            tuple(g for g in GOALS if g in goals),
            tuple(d for d in DIETS if d in diets),
            tuple(a for a in ALLERGENS if a in allergens),
            frozenset(avoid),
        )

    @property
    def key(self) -> str:
        return goal_key(self.goals)


def picker_context() -> dict:
    """Options for the icon picker template."""
    return {
        "goals": [{"key": k, "label": label, "icon": icon} for k, (label, icon) in GOALS.items()],
        "diets": [{"key": k, "label": label, "icon": icon} for k, (label, icon) in DIETS.items()],
        "allergens": [{"key": k, "label": label} for k, label in ALLERGENS.items()],
    }
