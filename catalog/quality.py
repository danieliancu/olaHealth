"""Product data quality: what is missing or doubtful, and whether a product may be listed.

Used when a revision is applied (stored on ``Product.quality_issues`` / ``Product.listable``)
and on the fly for Open Food Facts search results (alternatives).

* Listed in search, suggestions and alternatives only with a usable name and nutrition data
  (or "no nutrition table on the package"). Others stay reachable by barcode, link, lists and
  the diary, and are marked "Incomplete data".
* Never a guess: a product is not hidden for a missing photo or category, only marked.
"""

from __future__ import annotations

import re
from typing import Any

NAME_MISSING = "name_missing"
NAME_INCOMPLETE = "name_incomplete"
NUTRITION_MISSING = "nutrition_missing"
CATEGORY_MISSING = "category_missing"
PHOTO_MISSING = "photo_missing"
# Issues that keep a product out of listings (search, suggestions, alternatives).
BLOCKING = frozenset({NAME_MISSING, NAME_INCOMPLETE, NUTRITION_MISSING})

# Open Food Facts categories too broad to say what kind of food something is: peanut butter,
# garlic and passata all sit under "plant-based foods". Never used to match alternatives.
BROAD_CATEGORIES = frozenset(
    {
        "en:foods",
        "en:food",
        "en:groceries",
        "en:plant-based-foods-and-beverages",
        "en:plant-based-foods",
        "en:plant-based-beverages",
        "en:beverages",
        "en:beverages-and-beverages-preparations",
        "en:non-alcoholic-beverages",
        "en:alcoholic-beverages",
        "en:snacks",
        "en:sweet-snacks",
        "en:salty-snacks",
        "en:condiments",
        "en:sauces",
        "en:spreads",
        "en:dairies",
        "en:fermented-foods",
        "en:fermented-milk-products",
        "en:meats-and-their-products",
        "en:fishes-and-their-products",
        "en:seafood",
        "en:fruits-and-vegetables-based-foods",
        "en:vegetables-based-foods",
        "en:fruits-based-foods",
        "en:cereals-and-potatoes",
        "en:cereals-and-their-products",
        "en:legumes-and-their-products",
        "en:nuts-and-their-products",
        "en:frozen-foods",
        "en:canned-foods",
        "en:dried-products",
        "en:breakfasts",
        "en:desserts",
        "en:meals",
        "en:dietary-supplements",
        "en:baby-foods",
        "en:farming-products",
        "en:simple-sugars",
        "en:sweeteners",
        "en:cooking-helpers",
        "en:dried-products-to-be-rehydrated",
    }
)

_LETTERS = re.compile(r"[^\W\d_]", re.UNICODE)


def is_broad(tag: str) -> bool:
    return tag in BROAD_CATEGORIES


def name_issue(name: str | None, brand: str | None = "") -> str:
    """ "" for a usable name, else ``name_missing`` or ``name_incomplete`` (fewer than three
    letters, only digits or a barcode, or nothing more than the brand)."""
    name = (name or "").strip()
    if not name:
        return NAME_MISSING
    letters = len(_LETTERS.findall(name))
    if letters < 3:
        return NAME_INCOMPLETE
    brand = (brand or "").strip()
    if brand and name.casefold() == brand.casefold():
        return NAME_INCOMPLETE
    return ""


def nutrition_known(data: dict[str, Any]) -> bool:
    nutrition = (data or {}).get("nutrition") or {}
    if nutrition.get("status") == "not_on_package":
        return True
    return nutrition.get("status") == "available" and bool(nutrition.get("facts"))


def issues(data: dict[str, Any], *, has_front_image: bool) -> list[str]:
    """The quality issues of a product's data (``revision.data`` or a mapped OFF product)."""
    data = data or {}
    found = []
    if problem := name_issue(data.get("name"), data.get("brand")):
        found.append(problem)
    if not nutrition_known(data):
        found.append(NUTRITION_MISSING)
    if not [t for t in data.get("categories") or [] if not is_broad(t)]:
        found.append(CATEGORY_MISSING)
    if not has_front_image:
        found.append(PHOTO_MISSING)
    return found


def listable(found: list[str]) -> bool:
    return not (set(found) & BLOCKING)


LABELS = {
    NAME_MISSING: "Name missing",
    NAME_INCOMPLETE: "Name incomplete",
    NUTRITION_MISSING: "Nutrition values missing",
    CATEGORY_MISSING: "Category unclear",
    PHOTO_MISSING: "No photo",
}
