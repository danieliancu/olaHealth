"""Map an Open Food Facts product object to the olaHealth canonical product data.

The canonical data is plain JSON (stored in immutable revisions). Rules:

* Unknown is never zero: a nutrient absent from OFF produces no fact at all.
* A nutrient explicitly marked by OFF as not on the label (modifier "-") becomes
  ``not_on_label`` with no value.
* An empty allergen/trace list from OFF means "nothing recorded", never "allergen free".
* Additives are an OFF analysis of the ingredient list and live in assessments, not here.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from catalog.nutrition import DECLARED, NOT_ON_LABEL, NUTRIENTS, decimal_str, to_decimal

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
    value = " ".join(value.split())
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


def _facts(product: dict[str, Any]) -> list[dict[str, Any]]:
    nutriments = product.get("nutriments")
    if not isinstance(nutriments, dict):
        return []
    base = per_100_basis(product)
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
    facts = _facts(product)
    if product.get("no_nutrition_data") in ("on", True, "1"):
        nutrition = {"status": "not_on_package", "facts": []}
    elif facts:
        nutrition = {"status": "available", "facts": facts}
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


def image_urls(product: dict[str, Any], allowed_hosts: list[str]) -> dict[str, str]:
    """Front/ingredients/nutrition image URLs, only https URLs on allow-listed hosts."""
    result: dict[str, str] = {}
    for kind in ("front", "ingredients", "nutrition"):
        url = product.get(f"image_{kind}_url")
        if not isinstance(url, str):
            continue
        parts = urlsplit(url)
        if parts.scheme == "https" and parts.hostname in allowed_hosts:
            result[kind] = url
    return result
