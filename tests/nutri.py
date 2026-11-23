"""Synthetic OFF Nutri-Score fields for tests (not real products).

OFF publishes the grade, the points, the version and per-version category flags; a test
product needs all of them, consistent with the official thresholds, to get a Foodo Score.
"""

from __future__ import annotations

from typing import Any

# Points inside each official band (2023 general foods / cheese; 2017 the same except A, B).
POINTS = {
    ("2023", "general"): {"a": -2, "b": 1, "c": 5, "d": 14, "e": 25},
    ("2023", "cheese"): {"a": -2, "b": 1, "c": 5, "d": 14, "e": 25},
    ("2023", "fats"): {"a": -8, "b": 0, "c": 5, "d": 14, "e": 25},
    ("2023", "beverage"): {"b": 0, "c": 4, "d": 8, "e": 12},
    ("2021", "general"): {"a": -3, "b": 1, "c": 5, "d": 14, "e": 25},
}
FLAGS = {
    "2023": (
        "is_beverage",
        "is_cheese",
        "is_fat_oil_nuts_seeds",
        "is_red_meat_product",
        "is_water",
    ),
    "2021": ("is_beverage", "is_cheese", "is_fat", "is_water"),
}
ON = {
    ("2023", "cheese"): ("is_cheese",),
    ("2023", "fats"): ("is_fat_oil_nuts_seeds",),
    ("2023", "beverage"): ("is_beverage",),
    ("2023", "red_meat"): ("is_red_meat_product",),
    ("2023", "water"): ("is_beverage", "is_water"),
    ("2021", "fats"): ("is_fat",),
    ("2021", "beverage"): ("is_beverage",),
}


def flags(version: str = "2023", category: str = "general") -> dict[str, int]:
    on = ON.get((version, category), ())
    return {name: int(name in on) for name in FLAGS[version]}


def nutriscore_fields(
    grade: str | None,
    *,
    points: int | None = None,
    version: str = "2023",
    category: str = "general",
) -> dict[str, Any]:
    """OFF product fields for a Nutri-Score ``grade`` (``None``: no Nutri-Score)."""
    if grade is None:
        return {
            "nutriscore_grade": "unknown",
            "nutriscore_score": None,
            "nutriscore_version": version,
            "nutriscore": {version: {"grade": "unknown", "data": flags(version, category)}},
        }
    if points is None:
        points = POINTS[(version, category)][grade]
    return {
        "nutriscore_grade": grade,
        "nutriscore_score": points,
        "nutriscore_version": version,
        "nutriscore": {
            version: {"grade": grade, "score": points, "data": flags(version, category)}
        },
    }
