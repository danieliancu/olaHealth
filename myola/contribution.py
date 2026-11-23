"""What one declared portion of a product gives towards the user's daily targets.

Personal and separate: it never changes the product's Foodo Score or Nutri-Score, and it is
computed only from what the label states.

* The portion is the declared serving, else one unit of a multipack, in the unit of the
  per-100 basis (``myola.portions``); never the whole pack, never grams converted to ml.
* Values are per 100 g / 100 ml as sold, declared and exact (a "<" value is left out):
  value × portion / 100.
* Only the targets the user has set (Settings → Daily targets); energy only when calories are
  shown for this user (``Profile.energy_visible``); nothing when targets are switched off.
* A missing value or target is left out, never counted as 0.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from django.utils.translation import gettext_lazy as _

from catalog.nutrition import to_decimal
from catalog.presenters import format_number
from myola.portions import BASIS_UNIT, portions

TARGET_NUTRIENTS = (
    ("energy-kcal", _("Energy"), "kcal"),
    ("proteins", _("Protein"), "g"),
    ("carbohydrates", _("Carbohydrate"), "g"),
    ("fat", _("Fat"), "g"),
)


def _per_100(data: dict[str, Any]) -> tuple[str | None, dict[str, Decimal]]:
    facts = (data.get("nutrition") or {}).get("facts") or []
    for basis in BASIS_UNIT:
        values = {}
        for f in facts:
            if (
                f.get("basis") == basis
                and f.get("preparation") == "as_sold"
                and f.get("status") == "declared"
                and not f.get("modifier")
            ):
                number = to_decimal(f.get("value"))
                if number is not None:
                    values[f["nutrient"]] = number
        if values:
            return basis, values
    return None, {}


def portion_contribution(data: dict[str, Any], profile: Any) -> dict[str, Any] | None:
    """``{"portion": {...}, "items": [{nutrient, label, value, unit, target, percent}]}`` or
    ``None`` when there is no profile, no declared portion, no target or no usable value."""
    if profile is None or not getattr(profile, "show_targets", False):
        return None
    targets = profile.targets or {}
    basis, values = _per_100(data or {})
    if basis is None:
        return None
    option = next(
        (o for o in portions(data, basis)["options"] if o["key"] in ("serving", "unit")), None
    )
    if option is None:
        return None
    amount = to_decimal(option["amount"])
    if amount is None or amount <= 0:
        return None
    items = []
    for nutrient, label, unit in TARGET_NUTRIENTS:
        if nutrient == "energy-kcal" and not profile.energy_visible:
            continue
        target, per_100 = targets.get(nutrient), values.get(nutrient)
        if not target or per_100 is None:
            continue
        value = per_100 * amount / 100
        percent = (value * 100 / Decimal(target)).quantize(Decimal(1), rounding=ROUND_HALF_UP)
        items.append(
            {
                "nutrient": nutrient,
                "label": label,
                "value": format_number(value),
                "unit": unit,
                "target": target,
                "percent": int(percent),
            }
        )
    if not items:
        return None
    return {"portion": option, "items": items}
