"""Short, deterministic explanations shown on tap next to each value.

They describe what a value or indicator is and the reference thresholds used; they never give
a health verdict. Relative statements ("less sugar") belong to comparisons with a comparable
reference product, not here.
"""

from __future__ import annotations

from decimal import Decimal

from django.utils.translation import gettext as _

from catalog.nutrition import BASIS_LABELS, to_decimal


def _fsa(nutrient: str, basis: str) -> str:
    from catalog.presenters import FSA_THRESHOLDS, format_number

    kind = "liquid" if basis == "per_100ml" else "solid"
    low, high = FSA_THRESHOLDS[kind][nutrient]
    return _("UK FSA front-of-pack levels (%(k)s): low up to %(l)s g, high above %(h)s g.") % {
        "k": _("drinks") if kind == "liquid" else _("foods"),
        "l": format_number(Decimal(str(low))),
        "h": format_number(Decimal(str(high))),
    }


def nutrient(key: str, basis: str, facts_by_key: dict | None = None) -> str:
    per = BASIS_LABELS.get(basis, "")
    facts_by_key = facts_by_key or {}
    if key in ("energy-kcal", "energy-kj"):
        return (
            _(
                "Energy %s: how much energy the food provides. It does not say whether the food "
                "suits you."
            )
            % per
        )
    if key == "fat":
        return _("Total fat %(p)s, including saturates. %(t)s") % {
            "p": per,
            "t": _fsa("fat", basis),
        }
    if key == "saturated-fat":
        return _("Saturated fat %(p)s, part of the total fat. %(t)s") % {
            "p": per,
            "t": _fsa("saturated-fat", basis),
        }
    if key == "carbohydrates":
        return _("Carbohydrate %s, including sugars and starch.") % per
    if key == "sugars":
        return _(
            "Total sugars %(p)s: sugars naturally present (for example in milk or fruit) and "
            "added sugars together; the label does not separate them. %(t)s"
        ) % {"p": per, "t": _fsa("sugars", basis)}
    if key == "fiber":
        return (
            _(
                "Fibre %s. In the UK a “source of fibre” claim needs at least 3 g per 100 g and "
                "“high fibre” at least 6 g."
            )
            % per
        )
    if key == "proteins":
        text = _("Protein %s.") % per
        protein = to_decimal((facts_by_key.get("proteins") or {}).get("value"))
        kcal = to_decimal((facts_by_key.get("energy-kcal") or {}).get("value"))
        if protein is not None and kcal:
            share = round(protein * 4 / kcal * 100)
            text += " " + _(
                "About %(s)s%% of the energy comes from protein (4 kcal per gram). A “source of "
                "protein” claim needs at least 12%%."
            ) % {"s": share}
        return text
    if key == "salt":
        return _("Salt %(p)s (sodium × 2.5). %(t)s") % {"p": per, "t": _fsa("salt", basis)}
    return ""


INDICATORS = {
    "nutriscore": (
        "Nutri-Score (A to E) is computed by Open Food Facts from the nutrition facts and some "
        "ingredients, per 100 g or 100 ml."
    ),
    "nova": (
        "How processed the food is, judged by Open Food Facts from the ingredient list. It says "
        "nothing about the amount of sugar, fat or salt."
    ),
    "ingredients": (
        "Ingredients are listed in the order printed on the label, largest amount first. "
        "Percentages appear only where the label gives them."
    ),
    "allergens": (
        "Allergens as recorded by the source. “May contain” means traces are possible. Missing "
        "information is not the same as “none”: the physical label is the reference."
    ),
    "additives": (
        "Additives (E numbers) identified in the ingredient list. Being listed says what is in "
        "the product, not whether it is harmful."
    ),
}


def indicator(key: str) -> str:
    return _(INDICATORS[key]) if key in INDICATORS else ""
