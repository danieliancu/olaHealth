"""Map an Open Food Facts product object to the olaHealth canonical product data.

The canonical data is plain JSON (stored in immutable revisions). Rules:

* Unknown is never zero: a nutrient absent from OFF produces no fact at all.
* A nutrient explicitly marked by OFF as not on the label (modifier "-") becomes
  ``not_on_label`` with no value.
* An empty allergen/trace list from OFF means "nothing recorded", never "allergen free".
* Additives are an OFF analysis of the ingredient list and live in assessments, not here.
"""

from __future__ import annotations

import html
import re
from typing import Any

from catalog.nutrition import (
    DECLARED,
    NOT_ON_LABEL,
    NUTRIENTS,
    decimal_str,
    to_canonical_unit,
    to_decimal,
)
from off import basis as basis_rules
from off import images as off_images

DATA_FIELDS = (
    "name",
    "generic_name",
    "brand",
    "quantity",
    "serving_size",
    "categories",
    "countries",
    "ingredients",
    "allergens",
    "traces",
    "nutrition",
    "packaging_nutriscore",
)

_LIQUID = re.compile(r"\b\d+(?:[.,]\d+)?\s*(ml|cl|dl|l|fl\s?oz)\b", re.IGNORECASE)
_SOLID = re.compile(r"\b\d+(?:[.,]\d+)?\s*(g|kg|mg|oz|lb)\b", re.IGNORECASE)


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    # OFF sometimes stores HTML entities (Biscuits &quot;lotus&quot;): keep the real characters.
    value = " ".join(html.unescape(value).split())
    return value or None


def _tags(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return sorted({v.strip() for v in value if isinstance(v, str) and v.strip()})


def _ordered_tags(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    seen: dict[str, None] = {}
    for v in value:
        if isinstance(v, str) and v.strip():
            seen.setdefault(v.strip(), None)
    return list(seen)


def per_100_basis(product: dict[str, Any]) -> str:
    unit = str(product.get("product_quantity_unit") or "").lower()
    if unit in {"ml", "cl", "l", "dl"}:
        return "per_100ml"
    if unit in {"g", "kg"}:
        return "per_100g"
    quantity = str(product.get("quantity") or "")
    if _LIQUID.search(quantity) and not _SOLID.search(quantity):
        return "per_100ml"
    if _SOLID.search(quantity) and not _LIQUID.search(quantity):
        return "per_100g"
    return "per_100_unspecified"


def basis_with_source(product: dict[str, Any]) -> tuple[str, bool]:
    """The per-100 basis and whether it was worked out from the product type (``off.basis``)
    because the quantity does not say."""
    stated = per_100_basis(product)
    if stated != "per_100_unspecified":
        return stated, False
    name = _text(product.get("product_name")) or _text(product.get("product_name_en")) or ""
    inferred = basis_rules.infer(_ordered_tags(product.get("categories_tags")), name)
    return (inferred, True) if inferred else (stated, False)


def _facts(product: dict[str, Any]) -> list[dict[str, Any]]:
    nutriments = product.get("nutriments")
    if not isinstance(nutriments, dict):
        return []
    base, _inferred = basis_with_source(product)
    label_per_serving = product.get("nutrition_data_per") == "serving"
    variants = [("_100g", base, "as_sold"), ("_prepared_100g", base, "prepared")]
    if label_per_serving:
        variants += [
            ("_serving", "per_serving", "as_sold"),
            ("_prepared_serving", "per_serving", "prepared"),
        ]
    facts: list[dict[str, Any]] = []
    for nutrient in NUTRIENTS:
        for suffix, basis, preparation in variants:
            prefix = "_prepared" if preparation == "prepared" else ""
            modifier = nutriments.get(f"{nutrient.key}{prefix}_modifier")
            key = f"{nutrient.key}{suffix}"
            if modifier == "-" and preparation == "as_sold" and basis != "per_serving":
                facts.append(
                    _fact(nutrient.key, basis, preparation, NOT_ON_LABEL, None, nutrient.unit)
                )
                continue
            if key not in nutriments:
                continue
            value = to_decimal(nutriments[key])
            if value is None or value < 0:
                continue  # unparseable or impossible values are treated as unknown
            facts.append(
                _fact(
                    nutrient.key,
                    basis,
                    preparation,
                    DECLARED,
                    decimal_str(value),
                    nutrient.unit,
                    modifier if modifier in {"<", ">", "~", "<=", ">=", "≤", "≥"} else None,
                )
            )
    return sort_facts(facts)


# Current OFF product schema (``schema_version`` 1003+, as in the JSONL dump): the values live
# in ``nutrition.input_sets`` (one set per source, preparation and basis) and ``nutriments``
# is empty. Only values printed on the package or given by the manufacturer are declared
# values; OFF's estimates (``source: "estimate"``) are not. The product API (v2) still sends
# the older ``nutriments`` object, which is read first and keeps priority.
DECLARED_SOURCES = ("packaging", "manufacturer")  # in order of preference
_MODIFIERS = {"<", ">", "~", "<=", ">=", "≤", "≥"}


def _declared_sets(product: dict[str, Any]) -> list[dict[str, Any]]:
    nutrition = product.get("nutrition")
    if not isinstance(nutrition, dict) or not isinstance(nutrition.get("input_sets"), list):
        return []
    sets = [
        s
        for s in nutrition["input_sets"]
        if isinstance(s, dict) and s.get("source") in DECLARED_SOURCES
    ]
    return sorted(sets, key=lambda s: DECLARED_SOURCES.index(s["source"]))


def _facts_from_nutrition(product: dict[str, Any]) -> list[dict[str, Any]]:
    """Facts from ``nutrition.input_sets``, with the same basis rule as ``nutriments``."""
    base, _inferred = basis_with_source(product)
    facts: list[dict[str, Any]] = []
    taken: set[tuple[str, str]] = set()
    for input_set in _declared_sets(product):
        preparation = input_set.get("preparation")
        per = input_set.get("per")
        if preparation not in ("as_sold", "prepared"):
            continue
        if per in ("100g", "100ml"):
            basis = base
        elif per == "serving":
            basis = "per_serving"
        else:
            continue
        if (basis, preparation) in taken:
            continue  # a less preferred source for a basis already read
        taken.add((basis, preparation))
        nutrients = input_set.get("nutrients")
        nutrients = nutrients if isinstance(nutrients, dict) else {}
        unspecified = input_set.get("unspecified_nutrients")
        unspecified = set(unspecified) if isinstance(unspecified, list) else set()
        for nutrient in NUTRIENTS:
            entry = nutrients.get(nutrient.key)
            if isinstance(entry, dict) and "value" in entry:
                value = to_decimal(entry["value"])
                if value is None or value < 0:
                    continue  # unparseable or impossible values are treated as unknown
                try:
                    value = to_canonical_unit(
                        value, str(entry.get("unit") or nutrient.unit), nutrient
                    )
                except ValueError:
                    continue  # a unit we cannot convert: unknown, never guessed
                modifier = entry.get("modifier")
                facts.append(
                    _fact(
                        nutrient.key,
                        basis,
                        preparation,
                        DECLARED,
                        decimal_str(value),
                        nutrient.unit,
                        modifier if modifier in _MODIFIERS else None,
                    )
                )
            elif (
                nutrient.key in unspecified and preparation == "as_sold" and basis != "per_serving"
            ):
                facts.append(
                    _fact(nutrient.key, basis, preparation, NOT_ON_LABEL, None, nutrient.unit)
                )
    return sort_facts(facts)


def trim_nutrition(nutrition: Any) -> dict[str, Any] | None:
    """Only what :func:`_facts_from_nutrition` and ``map_product`` read (stored payload)."""
    if not isinstance(nutrition, dict):
        return None
    out: dict[str, Any] = {}
    if "no_nutrition_data_on_packaging" in nutrition:
        out["no_nutrition_data_on_packaging"] = nutrition["no_nutrition_data_on_packaging"]
    keys = {n.key for n in NUTRIENTS}
    sets = []
    for s in _declared_sets({"nutrition": nutrition}):
        kept = {
            k: s[k] for k in ("source", "preparation", "per", "unspecified_nutrients") if k in s
        }
        nutrients = s.get("nutrients") if isinstance(s.get("nutrients"), dict) else {}
        kept["nutrients"] = {
            k: {f: v[f] for f in ("value", "unit", "modifier") if f in v}
            for k, v in nutrients.items()
            if k in keys and isinstance(v, dict)
        }
        sets.append(kept)
    if sets:
        out["input_sets"] = sets
    return out


def _no_nutrition_data(product: dict[str, Any]) -> bool:
    if product.get("no_nutrition_data") in ("on", True, "1"):
        return True
    nutrition = product.get("nutrition")
    return isinstance(nutrition, dict) and nutrition.get("no_nutrition_data_on_packaging") in (
        "on",
        True,
        "1",
        1,
    )


def _fact(
    nutrient: str,
    basis: str,
    preparation: str,
    status: str,
    value: str | None,
    unit: str,
    modifier: str | None = None,
) -> dict[str, Any]:
    return {
        "nutrient": nutrient,
        "basis": basis,
        "preparation": preparation,
        "status": status,
        "value": value,
        "unit": unit,
        "modifier": modifier,
    }


def sort_facts(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    order = {n.key: i for i, n in enumerate(NUTRIENTS)}
    return sorted(facts, key=lambda f: (f["basis"], f["preparation"], order.get(f["nutrient"], 99)))


def _list_section(tags: list[str]) -> dict[str, Any]:
    """``en:none`` means the source recorded "none declared"; an empty list means unknown."""
    real = [t for t in tags if t != "en:none"]
    if real:
        return {"status": "listed", "items": real}
    if tags:
        return {"status": "none_declared", "items": []}
    return {"status": "unknown", "items": []}


def map_product(product: dict[str, Any]) -> dict[str, Any]:
    facts = _facts(product) or _facts_from_nutrition(product)
    if _no_nutrition_data(product):
        nutrition = {"status": "not_on_package", "facts": []}
    elif facts:
        nutrition = {"status": "available", "facts": facts}
        if basis_with_source(product)[1] and any(f["basis"] != "per_serving" for f in facts):
            nutrition["basis_inferred"] = True  # shown as "worked out from the product type"
    else:
        nutrition = {"status": "unknown", "facts": []}
    ingredients_text = _text(product.get("ingredients_text"))
    return {
        "name": _text(product.get("product_name")) or _text(product.get("product_name_en")),
        "generic_name": _text(product.get("generic_name")),
        "brand": _text(product.get("brands")),
        "quantity": _text(product.get("quantity")),
        "serving_size": _text(product.get("serving_size")),
        # OFF orders category tags from general to specific; keep that order.
        "categories": _ordered_tags(product.get("categories_tags")),
        "countries": _tags(product.get("countries_tags")),
        "ingredients": {
            "text": ingredients_text,
            "lang": (_text(product.get("ingredients_lang")) or _text(product.get("lang")))
            if ingredients_text
            else None,
        },
        "allergens": _list_section(_tags(product.get("allergens_tags"))),
        "traces": _list_section(_tags(product.get("traces_tags"))),
        "nutrition": nutrition,
        "packaging_nutriscore": None,
    }


def image_urls(product: dict[str, Any], allowed_hosts: list[str]) -> dict[str, str] | None:
    """Front/ingredients/nutrition image URLs (https, allow-listed hosts): the API's direct
    URLs, else built from the dump's ``images`` metadata; ``None`` when the payload carries
    no photo information at all (see ``off.images``)."""
    return off_images.image_urls(product, allowed_hosts)
