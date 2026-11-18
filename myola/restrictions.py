"""Strict restrictions: diets and allergies.

Conservative and deterministic:

* evidence of incompatibility wins over a compatibility claim;
* ``Contains`` and ``May contain`` are separate states;
* an empty field or a missing tag never confirms compatibility (state ``unknown``);
* no state is ever called "safe": the physical label stays the reference.
"""

from __future__ import annotations

import re

from django.utils.translation import gettext as _

from myola.facts import ProductFacts
from myola.preferences import ALLERGENS, DIETS

# Diet states
COMPATIBLE, INCOMPATIBLE, MAY_CONTAIN, UNKNOWN = (
    "compatible",
    "incompatible",
    "may_contain",
    "unknown",
)
# Allergen states
CONTAINS, NOT_DECLARED = "contains", "not_declared"

DIET_OK = {COMPATIBLE}
ALLERGEN_OK = {NOT_DECLARED}

ANIMAL_ALLERGENS = {"en:milk", "en:eggs", "en:fish", "en:crustaceans", "en:molluscs"}
MEAT_FISH_ALLERGENS = {"en:fish", "en:crustaceans", "en:molluscs"}
PORK_WORDS = re.compile(
    r"\b(pork|ham|bacon|lard|gammon|prosciutto|pancetta|chorizo|salami|pepperoni)\b"
)
LACTOSE_WORD = re.compile(r"\blactose\b")


def _state(status: str, reason: str) -> dict[str, str]:
    return {"status": status, "reason": reason}


def _names(tags) -> str:
    return ", ".join(str(ALLERGENS.get(t, t.split(":")[-1].replace("-", " "))) for t in tags)


def diet_status(p: ProductFacts, diet: str) -> dict[str, str]:
    labels, analysis = set(p.labels), set(p.analysis)
    allergens, traces = p.section("allergens"), p.section("traces")
    listed = set(allergens["items"]) if allergens["status"] == "listed" else set()
    trace_items = set(traces["items"]) if traces["status"] == "listed" else set()

    if diet == "vegan":
        animal = listed & ANIMAL_ALLERGENS
        if animal:
            return _state(INCOMPATIBLE, _("Contains %s.") % _names(sorted(animal)))
        if "en:non-vegan" in analysis:
            return _state(INCOMPATIBLE, _("The source's ingredient analysis says: not vegan."))
        if "en:vegan" in labels:
            return _state(COMPATIBLE, _("Labelled vegan (source)."))
        if "en:vegan" in analysis:
            return _state(COMPATIBLE, _("Vegan according to the source's ingredient analysis."))
        return _state(UNKNOWN, _("Not confirmed as vegan by the source."))

    if diet == "vegetarian":
        fish = listed & MEAT_FISH_ALLERGENS
        if fish:
            return _state(INCOMPATIBLE, _("Contains %s.") % _names(sorted(fish)))
        if "en:non-vegetarian" in analysis:
            return _state(INCOMPATIBLE, _("The source's ingredient analysis says: not vegetarian."))
        if labels & {"en:vegetarian", "en:vegan"}:
            return _state(COMPATIBLE, _("Labelled vegetarian (source)."))
        if analysis & {"en:vegetarian", "en:vegan"}:
            return _state(
                COMPATIBLE, _("Vegetarian according to the source's ingredient analysis.")
            )
        return _state(UNKNOWN, _("Not confirmed as vegetarian by the source."))

    if diet == "gluten_free":
        if "en:gluten" in listed:
            return _state(INCOMPATIBLE, _("Contains gluten."))
        if "en:gluten" in trace_items:
            return _state(MAY_CONTAIN, _("May contain gluten (traces)."))
        if labels & {"en:gluten-free", "en:no-gluten"}:
            return _state(COMPATIBLE, _("Labelled gluten-free (source)."))
        return _state(UNKNOWN, _("Not labelled gluten-free according to the source."))

    if diet == "lactose_free":
        # A lactose-free label is about lactose, not about milk: a lactose-free yogurt still
        # contains milk (see the milk allergy, which is a separate choice).
        if labels & {"en:lactose-free", "en:no-lactose"}:
            return _state(COMPATIBLE, _("Labelled lactose-free (source)."))
        if LACTOSE_WORD.search(p.ingredients_text):
            return _state(INCOMPATIBLE, _("Lactose is in the ingredient list."))
        if "en:milk" in listed:
            return _state(INCOMPATIBLE, _("Contains milk and is not labelled lactose-free."))
        if "en:milk" in trace_items:
            return _state(MAY_CONTAIN, _("May contain milk (traces)."))
        return _state(UNKNOWN, _("Not labelled lactose-free according to the source."))

    if diet == "pork_free":
        if PORK_WORDS.search(p.ingredients_text):
            return _state(INCOMPATIBLE, _("The ingredient list mentions pork."))
        if labels & {"en:no-pork", "en:halal"}:
            return _state(COMPATIBLE, _("Labelled without pork (source)."))
        vegetarian = diet_status(p, "vegetarian")
        if vegetarian["status"] == COMPATIBLE:
            return _state(COMPATIBLE, _("Vegetarian according to the source, so no pork."))
        return _state(UNKNOWN, _("The source does not confirm that this product has no pork."))

    raise ValueError(f"Unknown diet {diet!r}")


def allergen_status(p: ProductFacts, tag: str) -> dict[str, str]:
    allergens, traces = p.section("allergens"), p.section("traces")
    name = _names([tag])
    if allergens["status"] == "listed" and tag in allergens["items"]:
        return _state(CONTAINS, _("Contains %s (declared).") % name)
    if traces["status"] == "listed" and tag in traces["items"]:
        return _state(MAY_CONTAIN, _("May contain %s (traces).") % name)
    if allergens["status"] == "unknown":
        return _state(
            UNKNOWN, _("No allergen information from the source. Check the physical label.")
        )
    if traces["status"] == "unknown":
        return _state(
            NOT_DECLARED,
            _(
                "%s is not declared as an ingredient; the source has no “may contain” "
                "information. Check the physical label."
            )
            % name,
        )
    return _state(
        NOT_DECLARED,
        _("%s is not declared by the source. Check the physical label.") % name,
    )


def all_statuses(p: ProductFacts) -> dict[str, dict[str, dict[str, str]]]:
    return {
        "diets": {d: diet_status(p, d) for d in DIETS},
        "allergens": {a: allergen_status(p, a) for a in ALLERGENS},
    }


def blocking(statuses: dict, diets, allergens) -> list[dict[str, str]]:
    """The strict restrictions that are not confirmed as respected (for the active choices)."""
    out = []
    for d in diets:
        s = statuses["diets"][d]
        if s["status"] not in DIET_OK:
            out.append({"kind": "diet", "key": d, **s})
    for a in allergens:
        s = statuses["allergens"][a]
        if s["status"] not in ALLERGEN_OK:
            out.append({"kind": "allergen", "key": a, **s})
    return out
