"""Foodo Score: AskFoodo's conversion of the official Nutri-Score points to a 0–100 scale.

Pure and deterministic (no Django). docs/assessment-rules.md, "Foodo Score (score.v3)".

1. The official Nutri-Score grade band (A–E) is found from the points, with the official
   thresholds of the algorithm version and calculation category (``OFFICIAL``).
2. AskFoodo display bands: E 0–19, D 20–39, C 40–59, B 60–79, A 80–100 (``FOODO_BANDS``).
3. Inside the band, inverse linear interpolation (fewer Nutri-Score points is better):
   ``foodo = L + (U − L) × (p_max − p) / (p_max − p_min)``, with ``p_min``/``p_max`` the
   lowest and highest official points of that band; a band with a single possible score
   (water, graded A by its category) gives the middle of the Foodo band.
4. One rounding at the end, to the nearest integer, halves up.

Official sources (thresholds and component maxima):

* 2023 foods: Scientific Committee of the Nutri-Score, "Update of the Nutri-Score algorithm",
  2022 report, annex 1.4 and §2.4.3 / annex 2.1 (fats, oils, nuts and seeds),
  https://mpc.gouvernement.lu/dam-assets/le-minist%C3%A8re/consodur/2022-main-algorithm-report-update-final.pdf
* 2023 beverages: Scientific Committee, 2023 beverages report §6.4, §6.7,
  https://edepot.wur.nl/629571
* 2023 both: French draft decree notified to the EU (TRIS 2023), sections 2-1 to 2-3,
  https://technical-regulation-information-system.ec.europa.eu/mt/notification/25002/text/D/EN
* Component maxima: Nutri-Score Q&A (Santé publique France, 26 June 2024), tables 1–10,
  https://www.blv.admin.ch/dam/fr/sd-web/xemdwgNevqQT/faq-nutri-score-en.pdf
* 2017 algorithm (OFF "2021"): annex of the French decree of 31 October 2017 (ministry press
  kit), https://agriculture.gouv.fr/telecharger/87510
* Implementation cross-check: Open Food Facts ``lib/ProductOpener/Nutriscore.pm``.

Not supported: 2017-algorithm beverages (the official 2017 table prints the B band as
"Min à −1", which leaves 0 and 1 without a grade; the corrected 2019 text could not be read),
and any other version or category.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from math import floor
from typing import Any

FORMULA = "foodo-interp.v1"

# AskFoodo display bands (inclusive).
FOODO_BANDS: dict[str, tuple[int, int]] = {
    "E": (0, 19),
    "D": (20, 39),
    "C": (40, 59),
    "B": (60, 79),
    "A": (80, 100),
}


@dataclass(frozen=True)
class Table:
    """Official grade bands of one (version, category): grade -> (lowest, highest) points,
    inclusive. ``p_min``/``p_max`` are the theoretical extremes (−max positive points,
    max negative points)."""

    bands: dict[str, tuple[int, int]]
    by_category: bool = False  # water: graded A by its category, not by the points


def _foods(p_min: int, p_max: int, a_max: int, b_min: int) -> Table:
    return Table(
        {
            "A": (p_min, a_max),
            "B": (b_min, 2),
            "C": (3, 10),
            "D": (11, 18),
            "E": (19, p_max),
        }
    )


WATER = Table({"A": (0, 0)}, by_category=True)

OFFICIAL: dict[tuple[str, str], Table] = {
    # 2023 algorithm. Foods: negative 0–55 (energy 10, sugars 15, saturates 10, salt 20),
    # positive 0–17 (protein 7, fibre 5, fruit/veg/legumes 5); red meat protein capped at 2.
    ("2023", "general"): _foods(-17, 55, 0, 1),
    ("2023", "cheese"): _foods(-17, 55, 0, 1),
    ("2023", "red_meat"): _foods(-12, 55, 0, 1),
    ("2023", "fats"): _foods(-17, 55, -6, -5),
    # Beverages: negative 0–54 (energy 10, sugars 10, saturates 10, salt 20, sweeteners 4),
    # positive 0–18 (protein 7, fibre 5, fruit/veg 6); only water can be A.
    ("2023", "beverage"): Table({"B": (-18, 2), "C": (3, 6), "D": (7, 9), "E": (10, 54)}),
    ("2023", "water"): WATER,
    # 2017 algorithm (OFF "2021"). Foods: negative 0–40, positive 0–15 ("de -15 à +40").
    ("2021", "general"): _foods(-15, 40, -1, 0),
    ("2021", "cheese"): _foods(-15, 40, -1, 0),
    ("2021", "fats"): _foods(-15, 40, -1, 0),
    ("2021", "water"): WATER,
}

# OFF ``nutriscore.<version>.data`` flags per calculation category.
FLAGS = {
    "2023": {
        "water": "is_water",
        "beverage": "is_beverage",
        "fats": "is_fat_oil_nuts_seeds",
        "cheese": "is_cheese",
        "red_meat": "is_red_meat_product",
    },
    "2021": {
        "water": "is_water",
        "beverage": "is_beverage",
        "fats": "is_fat",
        "cheese": "is_cheese",
    },
}


def _on(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value == 1
    return str(value).strip().lower() in ("1", "true")


def category_for(version: str, data: Any) -> str | None:
    """Calculation category from OFF's ``nutriscore.<version>.data`` flags, or ``None`` when
    the version is unknown, the flags are missing or they contradict each other.

    Water is a beverage; any other two categories at once (e.g. beverage and cheese) are a
    contradiction. No flag set means general foods.
    """
    flags = FLAGS.get(str(version))
    if flags is None or not isinstance(data, dict):
        return None
    if not all(name in data for name in flags.values()):
        return None
    on = {category for category, name in flags.items() if _on(data.get(name))}
    if "water" in on:
        return "water" if on <= {"water", "beverage"} else None
    if len(on) > 1:
        return None
    return next(iter(on), "general")


def official_grade(points: int, table: Table) -> str | None:
    for grade, (low, high) in table.bands.items():
        if low <= points <= high:
            return grade
    return None


def round_half_up(value: Fraction) -> int:
    return floor(value + Fraction(1, 2))


@dataclass(frozen=True)
class Conversion:
    value: int | None = None  # 0–100
    band: str | None = None  # official grade band the points fall in (A–E)
    error: str = ""  # reason code when no value


def convert(points: Any, version: str, category: str | None) -> Conversion:
    """Foodo value of official Nutri-Score ``points`` for ``(version, category)``."""
    table = OFFICIAL.get((str(version), category or ""))
    if table is None:
        return Conversion(error="category_version_unsupported")
    if table.by_category:  # water: A by its category, a single possible score -> middle of A
        low, high = FOODO_BANDS["A"]
        return Conversion(round_half_up(Fraction(low + high, 2)), "A")
    if isinstance(points, bool) or not isinstance(points, int | float):
        return Conversion(error="points_missing")
    if isinstance(points, float):
        if not points.is_integer():
            return Conversion(error="points_out_of_range")
        points = int(points)
    grade = official_grade(points, table)
    if grade is None:
        return Conversion(error="points_out_of_range")
    p_lo, p_hi = table.bands[grade]
    f_lo, f_hi = FOODO_BANDS[grade]
    if p_lo == p_hi:
        return Conversion(round_half_up(Fraction(f_lo + f_hi, 2)), grade)
    value = f_lo + Fraction(f_hi - f_lo) * Fraction(p_hi - points, p_hi - p_lo)
    return Conversion(round_half_up(value), grade)


def supported() -> list[tuple[str, str]]:
    return sorted(OFFICIAL)
