"""Foodo Score formula (assessments/foodo.py), independent of the interface and database.

Expected values are worked out by hand in the comments:
foodo = L + (U − L) × (p_hi − p) / (p_hi − p_lo), rounded once, halves up.
Foodo bands: E 0–19, D 20–39, C 40–59, B 60–79, A 80–100.
"""

from __future__ import annotations

from fractions import Fraction
from itertools import pairwise

import pytest

from assessments.foodo import (
    FOODO_BANDS,
    OFFICIAL,
    category_for,
    convert,
    official_grade,
    round_half_up,
)

SCORED = [(key, table) for key, table in sorted(OFFICIAL.items()) if not table.by_category]


def value(points, version="2023", category="general"):
    result = convert(points, version, category)
    assert not result.error, result.error
    return result.value


# --- Official thresholds -------------------------------------------------------------------


def test_official_tables_as_published():
    """The tables, written out again from the official sources (docs/assessment-rules.md)."""
    foods_2023 = {"A": (-17, 0), "B": (1, 2), "C": (3, 10), "D": (11, 18), "E": (19, 55)}
    assert OFFICIAL[("2023", "general")].bands == foods_2023
    assert OFFICIAL[("2023", "cheese")].bands == foods_2023
    assert OFFICIAL[("2023", "red_meat")].bands == {**foods_2023, "A": (-12, 0)}
    assert OFFICIAL[("2023", "fats")].bands == {**foods_2023, "A": (-17, -6), "B": (-5, 2)}
    assert OFFICIAL[("2023", "beverage")].bands == {
        "B": (-18, 2),
        "C": (3, 6),
        "D": (7, 9),
        "E": (10, 54),
    }
    foods_2017 = {"A": (-15, -1), "B": (0, 2), "C": (3, 10), "D": (11, 18), "E": (19, 40)}
    for category in ("general", "cheese", "fats"):
        assert OFFICIAL[("2021", category)].bands == foods_2017
    assert ("2021", "beverage") not in OFFICIAL  # unsupported: unresolved official B band


@pytest.mark.parametrize("key, table", SCORED, ids=[f"{v}-{c}" for (v, c), _ in SCORED])
def test_bands_cover_the_whole_range_without_gaps(key, table):
    ranges = sorted(table.bands.values())
    for (_, high), (low, _) in pairwise(ranges):
        assert low == high + 1
    p_min, p_max = ranges[0][0], ranges[-1][1]
    for p in range(p_min, p_max + 1):
        assert official_grade(p, table) is not None


# --- Ends of the scale -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "version, category, p_min, p_max, best",
    [
        ("2023", "general", -17, 55, 100),
        ("2023", "cheese", -17, 55, 100),
        ("2023", "red_meat", -12, 55, 100),
        ("2023", "fats", -17, 55, 100),
        ("2023", "beverage", -18, 54, 79),  # only water is A: beverages top out at B's 79
        ("2021", "general", -15, 40, 100),
        ("2021", "fats", -15, 40, 100),
    ],
)
def test_ends_of_the_scale(version, category, p_min, p_max, best):
    assert value(p_max, version, category) == 0  # 0/100 is a valid score
    assert value(p_min, version, category) == best
    assert convert(p_max + 1, version, category).error == "points_out_of_range"
    assert convert(p_min - 1, version, category).error == "points_out_of_range"


# --- Thresholds and their neighbours (2023 general foods) --------------------------------------


@pytest.mark.parametrize(
    "points, expected",
    [
        # A −17..0 -> 80..100: 80 + 20·(0 − p)/17
        (-17, 100),
        (-1, 81),  # 80 + 20/17 = 81.18
        (0, 80),  # threshold A/B
        # B 1..2 -> 60..79: 60 + 19·(2 − p)/1
        (1, 79),
        (2, 60),
        # C 3..10 -> 40..59: 40 + 19·(10 − p)/7
        (3, 59),
        (5, 54),  # worked example: 40 + 95/7 = 53.57
        (10, 40),
        # D 11..18 -> 20..39: 20 + 19·(18 − p)/7
        (11, 39),
        (18, 20),
        # E 19..55 -> 0..19: 19·(55 − p)/36
        (19, 19),
        (31, 13),  # 19·24/36 = 12.67 (Nutella)
        (55, 0),
    ],
)
def test_general_foods_2023_by_hand(points, expected):
    assert value(points) == expected


def test_band_changes_exactly_at_the_official_threshold():
    for version, category in [key for key, _ in SCORED]:
        table = OFFICIAL[(version, category)]
        for grade, (low, high) in table.bands.items():
            f_low, f_high = FOODO_BANDS[grade]
            assert f_low <= value(high, version, category) <= f_high
            assert f_low <= value(low, version, category) <= f_high
            assert convert(low, version, category).band == grade
            assert convert(high, version, category).band == grade


@pytest.mark.parametrize(
    "version, category, points, expected",
    [
        # 2023 fats: A −17..−6, B −5..2
        ("2023", "fats", -6, 80),
        ("2023", "fats", -5, 79),
        ("2023", "fats", -9, 85),  # 80 + 20·3/11 = 85.45 (100% peanut butter)
        ("2023", "fats", 12, 36),  # 20 + 19·6/7 = 36.29 (crunchy peanut butter)
        ("2023", "fats", 21, 18),  # 19·34/36 = 17.94 (blended spread)
        # 2023 beverages: B −18..2, C 3..6, D 7..9, E 10..54
        ("2023", "beverage", 2, 60),
        ("2023", "beverage", 3, 59),
        ("2023", "beverage", 4, 53),  # 40 + 19·2/3 = 52.67 (sugar-free cola)
        ("2023", "beverage", 9, 20),  # D band bottom (drinkable yogurt)
        ("2023", "beverage", 10, 19),
        ("2023", "beverage", 12, 18),  # 19·42/44 = 18.14 (cola)
        # 2023 red meat: A −12..0
        ("2023", "red_meat", -6, 90),  # 80 + 20·6/12 = 90
        # 2023 cheese = general table
        ("2023", "cheese", 1, 79),  # cottage cheese
        ("2023", "cheese", 14, 31),  # 20 + 19·4/7 = 30.86 (soft cheese)
        # 2017 algorithm: A −15..−1, B 0..2
        ("2021", "general", -1, 80),
        ("2021", "general", 0, 79),  # 60 + 19·2/2
        ("2021", "general", -8, 90),  # 80 + 20·7/14 = 90
        ("2021", "general", 40, 0),
    ],
)
def test_other_categories_by_hand(version, category, points, expected):
    assert value(points, version, category) == expected


@pytest.mark.parametrize("key, table", SCORED, ids=[f"{v}-{c}" for (v, c), _ in SCORED])
def test_within_each_band_from_top_to_bottom_never_rising(key, table):
    version, category = key
    for grade, (low, high) in table.bands.items():
        values = [value(p, version, category) for p in range(low, high + 1)]
        assert values == sorted(values, reverse=True)  # never better with more points
        assert values[0] == FOODO_BANDS[grade][1]  # best points of the band -> top
        assert values[-1] == FOODO_BANDS[grade][0]  # worst points of the band -> bottom


@pytest.mark.parametrize("key, table", SCORED, ids=[f"{v}-{c}" for (v, c), _ in SCORED])
def test_monotonic_over_the_whole_range(key, table):
    version, category = key
    p_min = min(low for low, _ in table.bands.values())
    p_max = max(high for _, high in table.bands.values())
    values = [value(p, version, category) for p in range(p_min, p_max + 1)]
    assert all(a >= b for a, b in pairwise(values))
    # Across a threshold the score always drops (bands never overlap).
    for p in range(p_min, p_max):
        if convert(p, version, category).band != convert(p + 1, version, category).band:
            assert values[p - p_min] > values[p - p_min + 1]


# --- Rounding and special bands ----------------------------------------------------------------


def test_round_half_up_once():
    assert round_half_up(Fraction(1, 2)) == 1
    assert round_half_up(Fraction(3, 2)) == 2  # not banker's rounding
    assert round_half_up(Fraction(5, 2)) == 3
    assert round_half_up(Fraction(249, 100)) == 2
    # 2023 beverage B band −18..2, p = −8: 60 + 19·10/20 = 69.5 -> 70 (half up).
    assert value(-8, "2023", "beverage") == 70
    # 2023 fats A band, p = −11.5 is not a Nutri-Score (points are integers).
    assert convert(-11.5, "2023", "fats").error == "points_out_of_range"
    assert value(4.0, "2023", "beverage") == 53  # 4.0 is the integer 4


def test_water_is_a_by_category_middle_of_the_band():
    for version in ("2021", "2023"):
        result = convert(0, version, "water")
        assert (result.value, result.band) == (90, "A")  # (80 + 100) / 2
        assert convert(None, version, "water").value == 90  # points not used


def test_single_value_band_uses_the_middle():
    from assessments.foodo import Table

    table = Table({"A": (-1, -1), "B": (0, 2)})
    OFFICIAL[("test", "single")] = table
    try:
        assert convert(-1, "test", "single").value == 90  # (80 + 100) / 2
    finally:
        del OFFICIAL[("test", "single")]


# --- Unsupported and invalid input --------------------------------------------------------------


@pytest.mark.parametrize(
    "points, version, category, error",
    [
        (5, "2021", "beverage", "category_version_unsupported"),
        (5, "2021", "red_meat", "category_version_unsupported"),
        (5, "2025", "general", "category_version_unsupported"),
        (5, "2023", None, "category_version_unsupported"),
        (None, "2023", "general", "points_missing"),
        ("5", "2023", "general", "points_missing"),
        (True, "2023", "general", "points_missing"),
        (2.5, "2023", "general", "points_out_of_range"),
        (56, "2023", "general", "points_out_of_range"),
    ],
)
def test_invalid_input_gives_a_reason_never_a_value(points, version, category, error):
    result = convert(points, version, category)
    assert result.value is None and result.error == error


# --- Calculation category from OFF flags ---------------------------------------------------------

F23 = ("is_water", "is_beverage", "is_fat_oil_nuts_seeds", "is_cheese", "is_red_meat_product")
F21 = ("is_water", "is_beverage", "is_fat", "is_cheese")


def flags(names, on=()):
    return {n: (1 if n in on else 0) for n in names}


@pytest.mark.parametrize(
    "version, on, expected",
    [
        ("2023", (), "general"),
        ("2023", ("is_cheese",), "cheese"),
        ("2023", ("is_red_meat_product",), "red_meat"),
        ("2023", ("is_fat_oil_nuts_seeds",), "fats"),
        ("2023", ("is_beverage",), "beverage"),
        ("2023", ("is_beverage", "is_water"), "water"),
        ("2023", ("is_beverage", "is_cheese"), None),  # contradictory
        ("2023", ("is_fat_oil_nuts_seeds", "is_red_meat_product"), None),
        ("2023", ("is_water", "is_cheese"), None),
        ("2021", (), "general"),
        ("2021", ("is_fat",), "fats"),
        ("2021", ("is_beverage",), "beverage"),
    ],
)
def test_category_from_flags(version, on, expected):
    names = F23 if version == "2023" else F21
    assert category_for(version, flags(names, on)) == expected


def test_category_needs_every_flag_and_a_known_version():
    assert category_for("2023", {"is_cheese": 1}) is None  # flags missing
    assert category_for("2023", None) is None
    assert category_for("2030", flags(F23)) is None
    assert category_for("2023", {**flags(F23), "is_cheese": True}) == "cheese"  # OFF uses True
