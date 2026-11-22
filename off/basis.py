"""Per 100 g or per 100 ml, worked out from the kind of product when the quantity does not say.

Open Food Facts keeps every value under "per 100 g" keys, also for drinks; only the quantity
("330 ml", "250 g") tells them apart. When it is missing, the product type usually settles it:
a cheese is weighed, a juice is poured. Only clear cases are decided; anything that may be
labelled either way (oils, syrups, ice cream, powders made up into drinks) stays unknown.
The result is marked as worked out (``nutrition.basis_inferred``) and shown that way.
"""

from __future__ import annotations

import re

LIQUID_CATEGORIES = frozenset(
    {
        "en:beverages",
        "en:waters",
        "en:mineral-waters",
        "en:spring-waters",
        "en:flavoured-waters",
        "en:milks",
        "en:plant-based-milks",
        "en:plant-milks",
        "en:juices",
        "en:fruit-juices",
        "en:vegetable-juices",
        "en:nectars",
        "en:smoothies",
        "en:sodas",
        "en:soft-drinks",
        "en:carbonated-drinks",
        "en:colas",
        "en:lemonades",
        "en:energy-drinks",
        "en:iced-teas",
        "en:milkshakes",
        "en:kombuchas",
        "en:alcoholic-beverages",
        "en:beers",
        "en:wines",
        "en:ciders",
        "en:soups",
        "en:broths",
    }
)

SOLID_CATEGORIES = frozenset(
    {
        "en:cheeses",
        "en:yogurts",
        "en:biscuits",
        "en:biscuits-and-cakes",
        "en:cakes",
        "en:pastries",
        "en:chocolates",
        "en:confectioneries",
        "en:candies",
        "en:chewing-gum",
        "en:sweet-snacks",
        "en:salty-snacks",
        "en:crisps",
        "en:chips-and-fries",
        "en:popcorn",
        "en:nuts",
        "en:seeds",
        "en:dried-fruits",
        "en:cereal-bars",
        "en:bars",
        "en:breakfast-cereals",
        "en:cereals-and-potatoes",
        "en:breads",
        "en:pastas",
        "en:rices",
        "en:flours",
        "en:legumes",
        "en:meats",
        "en:sausages",
        "en:hams",
        "en:poultries",
        "en:fishes",
        "en:seafood",
        "en:eggs",
        "en:butters",
        "en:spreadable-fats",
        "en:jams",
        "en:honeys",
        "en:sugars",
        "en:pizzas",
        "en:sandwiches",
        "en:meals",
        "en:fresh-vegetables",
        "en:fresh-fruits",
        "en:frozen-vegetables",
    }
)

# Labelled per 100 g by some makers and per 100 ml by others, or made up with water or milk:
# never decided from the type.
AMBIGUOUS_CATEGORIES = frozenset(
    {
        "en:vegetable-oils",
        "en:oils",
        "en:olive-oils",
        "en:syrups",
        "en:sauces",
        "en:condiments",
        "en:vinegars",
        "en:ice-creams",
        "en:frozen-desserts",
        "en:creams",
        "en:drinkable-yogurts",
        "en:fermented-milk-drinks",
        "en:dried-products-to-be-rehydrated",
        "en:instant-beverages",
        "en:beverage-preparations",
        "en:milk-powders",
        "en:powdered-milks",
        "en:teas",
        "en:coffees",
        "en:cordials",
        "en:squashes",
        "en:baby-foods",
    }
)

# Names, only when the categories say nothing either way. A name with words of both kinds
# ("milk chocolate", "water biscuits") stays unknown.
_LIQUID_NAME = re.compile(
    r"\b(drinks?|juices?|waters?|smoothies?|sodas?|colas?|lemonades?|milkshakes?|milks?|"
    r"soups?|broths?|beers?|wines?|ciders?|kombucha|latte|iced tea)\b",
    re.IGNORECASE,
)
_SOLID_NAME = re.compile(
    r"\b(chocolates?|biscuits?|cookies?|cakes?|bars?|cheeses?|crisps|breads?|sweets|gums|"
    r"drops|clusters?|cereals?|nuts|granola|flapjacks?|crackers?|wafers?|muffins?|brownies?|"
    r"buns?|rolls?|pies?|pizzas?|sandwich(?:es)?|sausages?|butter|jam|honey|pasta|rice)\b",
    re.IGNORECASE,
)

PER_100G, PER_100ML = "per_100g", "per_100ml"


def from_categories(categories: list[str]) -> str | None:
    tags = {str(c).strip().lower() for c in categories or []}
    if tags & AMBIGUOUS_CATEGORIES:
        return None
    liquid, solid = bool(tags & LIQUID_CATEGORIES), bool(tags & SOLID_CATEGORIES)
    if liquid != solid:
        return PER_100ML if liquid else PER_100G
    return None


def from_name(name: str) -> str | None:
    liquid, solid = bool(_LIQUID_NAME.search(name or "")), bool(_SOLID_NAME.search(name or ""))
    if liquid != solid:
        return PER_100ML if liquid else PER_100G
    return None


def infer(categories: list[str], name: str) -> str | None:
    """per_100g, per_100ml or None (not clear). Categories first; the name only when none of
    the categories is known here, so a mixed or ambiguous type is never overruled by a word."""
    tags = {str(c).strip().lower() for c in categories or []}
    if tags & (LIQUID_CATEGORIES | SOLID_CATEGORIES | AMBIGUOUS_CATEGORIES):
        return from_categories(categories)
    return from_name(name)
