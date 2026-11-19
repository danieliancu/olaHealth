"""Portions a user can pick when adding a product to the food diary.

Only declared amounts are offered, and only in the unit of the product's per-100 basis
(grams for per 100 g, millilitres for per 100 ml). Grams are never converted to millilitres.
A multipack ("4 x 125 g") offers "1 unit" only when the unit's amount is stated.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from django.utils.translation import gettext as _

from catalog.presenters import format_number

# amount + unit, e.g. "125 g", "0,5 l", "250ml", "1.5 kg"
_AMOUNT = re.compile(r"(\d+(?:[.,]\d+)?)\s*(kg|g|gr|grams?|ml|cl|dl|l|litres?|liters?)\b", re.I)
# multipack, e.g. "4 x 125 g", "6×330ml"
_MULTI = re.compile(
    r"(\d+)\s*[x×]\s*(\d+(?:[.,]\d+)?)\s*(kg|g|gr|grams?|ml|cl|dl|l|litres?|liters?)\b", re.I
)
_TO_BASE = {
    "kg": ("g", Decimal(1000)),
    "g": ("g", Decimal(1)),
    "gr": ("g", Decimal(1)),
    "gram": ("g", Decimal(1)),
    "grams": ("g", Decimal(1)),
    "l": ("ml", Decimal(1000)),
    "litre": ("ml", Decimal(1000)),
    "litres": ("ml", Decimal(1000)),
    "liter": ("ml", Decimal(1000)),
    "liters": ("ml", Decimal(1000)),
    "dl": ("ml", Decimal(100)),
    "cl": ("ml", Decimal(10)),
    "ml": ("ml", Decimal(1)),
}
BASIS_UNIT = {"per_100g": "g", "per_100ml": "ml"}


def _amount(number: str, unit: str) -> tuple[Decimal, str] | None:
    try:
        value = Decimal(number.replace(",", "."))
    except InvalidOperation:
        return None
    base, factor = _TO_BASE[unit.lower()]
    value = value * factor
    if value <= 0 or value > 10000:
        return None
    return value, base


def parse_amount(text: str | None) -> tuple[Decimal, str] | None:
    """First amount in the text, in g or ml (mass and volume are never mixed)."""
    m = _AMOUNT.search(text or "")
    return _amount(m.group(1), m.group(2)) if m else None


def parse_multipack(text: str | None) -> tuple[int, Decimal, str] | None:
    m = _MULTI.search(text or "")
    if not m:
        return None
    unit = _amount(m.group(2), m.group(3))
    count = int(m.group(1))
    return (count, *unit) if unit and 1 < count <= 100 else None


def portions(data: dict, basis: str | None) -> dict:
    """Diary options for a product: the unit to type in, and declared portions in that unit."""
    unit = BASIS_UNIT.get(basis or "")
    options = []
    if unit:
        serving = parse_amount(data.get("serving_size"))
        if serving and serving[1] == unit:
            options.append(
                {
                    "key": "serving",
                    "label": _("1 serving (%(a)s %(u)s)")
                    % {"a": format_number(serving[0]), "u": unit},
                    "amount": format_number(serving[0]),
                }
            )
        multi = parse_multipack(data.get("quantity"))
        if multi and multi[2] == unit:
            label = _("1 unit (%(a)s %(u)s)") % {"a": format_number(multi[1]), "u": unit}
            if not any(o["amount"] == format_number(multi[1]) for o in options):
                options.append({"key": "unit", "label": label, "amount": format_number(multi[1])})
        elif not multi:
            pack = parse_amount(data.get("quantity"))
            if pack and pack[1] == unit and pack[0] <= 1000:
                options.append(
                    {
                        "key": "pack",
                        "label": _("Whole pack (%(a)s %(u)s)")
                        % {"a": format_number(pack[0]), "u": unit},
                        "amount": format_number(pack[0]),
                    }
                )
    return {
        # "g" or "ml" from a verified basis; None when the source does not say which.
        "unit": unit,
        "options": options,
    }
