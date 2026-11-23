"""Foodo Score ``score.v2`` (docs/assessment-rules.md, "Foodo Score (score.v2)").

Two kinds of examples:

* REAL products from Open Food Facts (tests/fixtures/off/real, ODbL). Their expected results
  below were written by hand from the fields OFF published, not from this code's output.
* SYNTHETIC indicators (marked as such): cases real data cannot give on demand. They test the
  rules, they are not validation on real products.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.urls import reverse

from assessments.rules import IndicatorSpec, derive_from_off
from assessments.score import (
    VERSION,
    foodo_score,
    foodo_score_v1,
    is_better,
    score_for_product,
    score_for_revision,
    score_from_off,
    summary,
)
from catalog.models import ProductRevision
from catalog.services.importer import import_off_product

REAL = Path(__file__).parent / "fixtures" / "off" / "real"


def real(code: str) -> dict:
    data = json.loads((REAL / f"{code}.json").read_text(encoding="utf-8"))
    data.pop("_fixture")
    return data


# --- Synthetic indicator builders (not real products) -------------------------------------


def ns(grade="b", version="2023", status="available", score=1, current=True, **value):
    v = {"grade": grade, "other_versions": {}, **value}
    if score is not None:
        v["score"] = score
    return SimpleNamespace(
        indicator="nutriscore",
        data_status=status,
        value=v,
        algorithm_version=version,
        warnings=[],
        origin="off_computed",
        invalidated_by_local_change=not current,
    )


def nova(group=3, status="available", current=True):
    return SimpleNamespace(
        indicator="nova",
        data_status=status,
        value={"group": group} if group is not None else {},
        algorithm_version="",
        warnings=[],
        origin="off_computed",
        invalidated_by_local_change=not current,
    )


def additives(tags=(), status="available", current=True):
    return SimpleNamespace(
        indicator="additives",
        data_status=status,
        value={"tags": list(tags)} if status == "available" else {},
        algorithm_version="",
        warnings=[],
        origin="off_computed",
        invalidated_by_local_change=not current,
    )


def levels(**lv):
    return SimpleNamespace(
        indicator="nutrient_levels",
        data_status="available",
        value={"levels": lv},
        algorithm_version="",
        warnings=[],
        origin="off_computed",
        invalidated_by_local_change=False,
    )


def assert_no_number(result):
    assert result["nutriscore"]["grade"] is None
    assert result["nutriscore"]["points"] is None
    assert result["comparable_key"] is None
    assert "score" not in result


# --- Status rules (one test per row of the table) ----------------------------------------


def test_row1_no_nutriscore_is_not_enough_data():
    for indicators in ([], [nova(1), additives()], [ns(status="missing_data", grade=None)]):
        result = foodo_score(indicators)
        assert result["status"] == "not_enough_data"
        assert result["reasons"][0]["code"] == "nutriscore_missing"
        assert "nutriscore" in result["missing"]
        assert_no_number(result)


@pytest.mark.parametrize("grade", ["unknown", "", None, "f", "ab", "1"])
def test_row1_invalid_grade_is_not_enough_data(grade):
    result = foodo_score([ns(grade=grade)])
    assert result["status"] == "not_enough_data" and result["missing"][0] == "nutriscore"
    assert_no_number(result)


def test_row1_unknown_data_status_is_not_enough_data():
    result = foodo_score([ns(status="something_new")])
    assert result["status"] == "not_enough_data"
    assert_no_number(result)


def test_row2_not_current_after_local_correction():
    result = foodo_score([ns(grade="a", current=False)])
    assert result["status"] == "not_enough_data"
    assert result["reasons"][0]["code"] == "nutriscore_not_current"
    assert result["nutriscore"]["status"] == "not_current"
    assert_no_number(result)


def test_row3_not_applicable():
    result = foodo_score([ns(grade=None, status="not_applicable", score=None)])
    assert result["status"] == "not_applicable"
    assert result["reasons"][0]["code"] == "nutriscore_not_applicable"
    assert "nutriscore" not in result["missing"]
    assert_no_number(result)


def test_row4_conflict_with_the_package_shows_no_number():
    result = foodo_score([ns(grade="b", status="conflicting", packaging_grade="c")])
    assert result["status"] == "conflicting"
    assert result["nutriscore"]["packaging_grade"] == "c"
    assert "(C)" in result["reasons"][0]["text"] and "(B)" in result["reasons"][0]["text"]
    assert_no_number(result)


def test_row5_version_unknown_is_not_enough_data():
    result = foodo_score([ns(grade="a", version="")])
    assert result["status"] == "not_enough_data"
    assert result["reasons"][0]["code"] == "nutriscore_version_unknown"
    assert_no_number(result)


@pytest.mark.parametrize("grade", ["a", "b", "c", "d", "e", "C"])
def test_row6_ok_is_the_official_grade_and_points(grade):
    result = foodo_score([ns(grade=grade, score=7)])
    assert result["status"] == "ok" and result["version"] == VERSION == "score.v2"
    assert result["nutriscore"]["grade"] == grade.lower()
    assert result["nutriscore"]["points"] == 7  # unchanged
    assert result["nutriscore"]["version"] == "2023"
    assert result["comparable_key"] == "nutriscore:2023"
    assert result["reasons"][0]["code"] == "nutriscore_official"


@pytest.mark.parametrize("points", [None, "7", True, 1.5])
def test_ok_without_usable_points_lists_them_as_missing(points):
    result = foodo_score([ns(grade="b", score=points)])
    assert result["status"] == "ok" and result["nutriscore"]["points"] is None
    assert "nutriscore_points" in result["missing"]
    assert result["reasons"][1]["code"] == "nutriscore_points_missing"


def test_points_edge_values_are_kept_as_published():
    assert foodo_score([ns(grade="a", score=-15)])["nutriscore"]["points"] == -15
    assert foodo_score([ns(grade="a", score=0)])["nutriscore"]["points"] == 0
    assert foodo_score([ns(grade="e", score=40.0)])["nutriscore"]["points"] == 40


def test_packaging_declared_snapshot_is_never_an_input():
    printed = SimpleNamespace(**{**vars(ns(grade="a")), "origin": "packaging_declared"})
    assert foodo_score([printed])["status"] == "not_enough_data"
    # Listed first or last, OFF's own grade is the one used.
    for order in ([printed, ns(grade="d")], [ns(grade="d"), printed]):
        assert foodo_score(order)["nutriscore"]["grade"] == "d"


# --- NOVA, additives, levels: separate, never in the score -------------------------------


def test_nova_additives_and_levels_never_change_the_score():
    base = foodo_score([ns(grade="c", score=5), nova(1), additives()])
    variants = [
        [ns(grade="c", score=5), nova(4), additives()],
        [ns(grade="c", score=5), nova(1), additives([f"en:e{i}" for i in range(100, 110)])],
        [ns(grade="c", score=5), nova(1), additives(), levels(sugars="high", salt="high")],
        [ns(grade="c", score=5)],  # NOVA and additives unknown
    ]
    for indicators in variants:
        other = foodo_score(indicators)
        assert other["status"] == base["status"] == "ok"
        assert other["nutriscore"] == base["nutriscore"]


def test_nova_is_shown_separately_with_its_status():
    def group(item):
        return foodo_score([ns(), item])["separate"]["nova"]

    assert group(nova(4)) == {"status": "available", "group": 4, "source": "off_computed"}
    assert group(nova("2"))["group"] == 2
    assert group(nova(5, status="available"))["status"] == "missing_data"
    assert group(nova(True))["status"] == "missing_data"
    assert group(nova(None, status="not_applicable"))["status"] == "not_applicable"
    assert group(nova(4, current=False)) == {
        "status": "not_current",
        "group": None,
        "source": "off_computed",
    }
    assert foodo_score([ns()])["separate"]["nova"]["status"] == "missing_data"
    assert "nova" in foodo_score([ns()])["missing"]
    assert "nova" not in foodo_score([ns(), nova(None, status="not_applicable")])["missing"]


def test_additives_are_listed_not_assessed():
    shown = foodo_score([ns(), additives(["en:e330", "en:e150d", "en:e330"])])
    block = shown["separate"]["additives"]
    assert block["tags"] == ["en:e150d", "en:e330"] and block["count"] == 2
    assert block["assessed"] is False
    none_found = foodo_score([ns(), additives([])])["separate"]["additives"]
    assert none_found["status"] == "available" and none_found["count"] == 0
    # Unknown is never "none".
    for item in (None, additives(status="missing_data"), additives(["en:e330"], current=False)):
        block = foodo_score([ns()] + ([item] if item else []))["separate"]["additives"]
        assert block["tags"] is None and block["count"] is None
        assert block["status"] in ("missing_data", "not_current")


def test_personal_data_is_not_part_of_the_result():
    result = foodo_score([ns(), nova(), additives()])
    assert set(result) == {
        "version",
        "status",
        "nutriscore",
        "comparable_key",
        "separate",
        "missing",
        "reasons",
        "inputs_hash",
    }
    assert set(result["separate"]) == {"nova", "additives"}


# --- Versions -----------------------------------------------------------------------------


def test_versions_are_never_compared():
    v2021_a = foodo_score([ns(grade="a", version="2021")])
    v2023_c = foodo_score([ns(grade="c", version="2023")])
    v2023_b = foodo_score([ns(grade="b", version="2023")])
    assert not is_better(v2021_a, v2023_c)
    assert is_better(v2023_b, v2023_c) and not is_better(v2023_c, v2023_b)
    assert not is_better(v2023_b, v2023_b)  # equal is not better
    assert not is_better(foodo_score([]), v2023_c)
    assert not is_better(v2023_b, foodo_score([]))


def test_other_versions_are_details_only():
    result = foodo_score([ns(grade="c", other_versions={"2021": "b"})])
    assert result["nutriscore"]["grade"] == "c"
    assert result["nutriscore"]["other_versions"] == {"2021": "b"}
    assert result["comparable_key"] == "nutriscore:2023"


def test_v1_results_are_never_better_than_v2():
    legacy = {"version": "score.v1", "status": "ok", "score": 100}
    assert not is_better(legacy, foodo_score([ns(grade="e")]))


# --- Determinism --------------------------------------------------------------------------


def test_same_inputs_same_result_in_any_order():
    items = [ns(grade="d", score=12), nova(4), additives(["en:e330"]), levels(sugars="high")]
    first = foodo_score(items)
    assert foodo_score(list(reversed(items))) == first
    assert foodo_score([*items]) == first
    assert len(first["inputs_hash"]) == 64
    assert (
        foodo_score([ns(grade="d", score=13), *items[1:]])["inputs_hash"] != (first["inputs_hash"])
    )


def test_off_object_and_snapshots_give_the_same_result(db):
    payload = real("20994945")
    product = import_off_product(payload)
    assert score_from_off(payload) == score_for_product(product)
    assert foodo_score(derive_from_off(payload)) == score_from_off(payload)
    assert all(isinstance(s, IndicatorSpec) for s in derive_from_off(payload))


# --- Real products (Open Food Facts, ODbL) -------------------------------------------------

# code: (status, grade, points, other_versions, nova status, nova group, additives count)
EXPECTED = {
    "3033490004743": ("ok", "a", -3, {"2021": "a"}, "available", 3, 0),
    "5010029000016": ("ok", "a", -3, {"2021": "a"}, "available", 3, 0),
    "5000157024671": ("ok", "a", -1, {"2021": "a"}, "available", 4, 0),
    "20994945": ("ok", "b", 1, {"2021": "a"}, "available", 3, 0),
    "8002470031739": ("ok", "c", 4, {"2021": "d"}, "available", 2, None),
    "3175680011480": ("ok", "c", 9, {"2021": "b"}, "available", 4, 4),
    "5449000131805": ("ok", "c", 4, {"2021": "b"}, "available", 4, 6),
    "7622201693916": ("ok", "d", 14, {"2021": "d"}, "available", 4, 2),
    "3329770063341": ("ok", "d", 9, {"2021": "e"}, "available", 4, 0),
    "5449000054227": ("ok", "e", 12, {"2021": "e"}, "available", 4, 3),
    "3017624010701": ("ok", "e", 31, {"2021": "e"}, "missing_data", None, 0),
    "3068320114453": ("ok", "a", 0, {"2021": "a"}, "available", 1, 0),
    "75041670": ("not_applicable", None, None, {}, "available", 3, 0),
    "10614594": ("not_enough_data", None, None, {}, "missing_data", None, None),
}


@pytest.mark.parametrize("code", sorted(EXPECTED))
def test_real_products(code):
    status, grade, points, others, nova_status, group, count = EXPECTED[code]
    result = score_from_off(real(code))
    assert result["status"] == status
    assert result["nutriscore"]["grade"] == grade
    assert result["nutriscore"]["points"] == points
    assert result["nutriscore"]["other_versions"] == others
    assert result["separate"]["nova"]["status"] == nova_status
    assert result["separate"]["nova"]["group"] == group
    assert result["separate"]["additives"]["count"] == count
    if status == "ok":
        assert result["nutriscore"]["version"] == "2023"
    else:
        assert_no_number(result)


def test_real_fixtures_are_all_covered_and_carry_their_source():
    files = {p.stem for p in REAL.glob("*.json")}
    assert files == set(EXPECTED)
    for code in files:
        meta = json.loads((REAL / f"{code}.json").read_text(encoding="utf-8"))["_fixture"]
        assert meta["license"].startswith("ODbL") and code in meta["source"]


def test_real_cola_and_sugar_free_cola_differ_only_by_grade():
    """Same NOVA group: the additives (3 vs 6) do not decide anything."""
    cola, zero = score_from_off(real("5449000054227")), score_from_off(real("5449000131805"))
    assert is_better(zero, cola) and not is_better(cola, zero)


def test_real_2021_grades_are_never_used_to_rank():
    # Cottage cheese: A in 2021 but B in 2023; skyr is A in 2023. Only 2023 counts.
    assert is_better(score_from_off(real("3033490004743")), score_from_off(real("20994945")))


# --- Storage and recalculation ------------------------------------------------------------


def test_publish_stores_the_score_on_the_revision(db, off_payload):
    product = import_off_product(off_payload())
    stored = product.current_revision.foodo_score
    assert stored["version"] == VERSION and stored["status"] == "ok"
    assert stored == foodo_score(product.current_revision.assessments.all())
    assert score_for_product(product) == stored


def test_reading_uses_the_stored_result(db, off_payload, django_assert_num_queries):
    product = import_off_product(off_payload())
    revision = ProductRevision.objects.get(pk=product.current_revision_id)
    with django_assert_num_queries(0):
        assert score_for_revision(revision)["version"] == VERSION


def test_revisions_without_a_v2_result_are_computed_never_shown_as_v1(db, off_payload):
    """Transition: rows published before score.v2 (null) or holding another version."""
    product = import_off_product(off_payload())
    expected = product.current_revision.foodo_score
    legacy = foodo_score_v1({"nutriscore_grade": "b", "nova_group": 1})
    for old in (None, legacy):
        ProductRevision.objects.filter(pk=product.current_revision_id).update(foodo_score=old)
        product.refresh_from_db()
        result = score_for_product(product)
        assert result == expected and result["version"] == VERSION and "score" not in result
        # Reading does not write.
        assert ProductRevision.objects.get(pk=product.current_revision_id).foodo_score == old


def test_backfill_migration_fills_current_revisions(db, off_payload):
    import importlib

    from django.apps import apps

    backfill = importlib.import_module("catalog.migrations.0009_backfill_foodo_score").backfill
    product = import_off_product(off_payload())
    ProductRevision.objects.filter(pk=product.current_revision_id).update(foodo_score=None)
    backfill(apps, None)
    stored = ProductRevision.objects.get(pk=product.current_revision_id).foodo_score
    assert stored["version"] == VERSION and stored["status"] == "ok"


def _approve(product, proposed, admin):
    from contributions.models import Contribution
    from contributions.services import moderation

    c = Contribution.objects.create(
        product=product,
        base_revision=product.current_revision,
        problem_type="incorrect_data",
        proposed=proposed,
        status="submitted",
    )
    return moderation.approve(c.pk, admin, "ok")


def test_local_nutrition_correction_recomputes_to_not_current(db, off_payload, admin_user):
    product = import_off_product(off_payload())
    assert score_for_product(product)["status"] == "ok"
    nutrition = dict(product.current_revision.data["nutrition"])
    nutrition["facts"] = [
        dict(f, value="3.5") if f["nutrient"] == "sugars" else f for f in nutrition["facts"]
    ]
    _approve(product, {"nutrition": nutrition}, admin_user)
    product.refresh_from_db()
    stored = product.current_revision.foodo_score
    assert stored["status"] == "not_enough_data"
    assert stored["reasons"][0]["code"] == "nutriscore_not_current"
    assert_no_number(stored)


def test_package_grade_conflict_recomputes_to_conflicting(db, off_payload, admin_user):
    product = import_off_product(off_payload())  # OFF: B (2023)
    _approve(product, {"packaging_nutriscore": {"grade": "d", "version": None}}, admin_user)
    product.refresh_from_db()
    stored = product.current_revision.foodo_score
    assert stored["status"] == "conflicting" and stored["nutriscore"]["packaging_grade"] == "d"
    assert_no_number(stored)


def test_name_change_keeps_the_score(db, off_payload, admin_user):
    product = import_off_product(off_payload())
    before = product.current_revision.foodo_score
    _approve(product, {"name": "Another name"}, admin_user)
    product.refresh_from_db()
    assert product.current_revision.foodo_score == before


# --- API and pages: no number without enough data -----------------------------------------


def test_api_exposes_the_score(db, client, off_payload):
    graded = import_off_product(off_payload())
    body = client.get(reverse("api-product", args=[graded.pk])).json()
    assert body["foodo_score"]["status"] == "ok"
    assert body["foodo_score"]["nutriscore"]["grade"] == "b"
    bare = import_off_product(
        off_payload(code="3017620422003", nutriscore_grade="", nutriscore={}, nutriscore_score=None)
    )
    score = client.get(reverse("api-product", args=[bare.pk])).json()["foodo_score"]
    assert score["status"] == "not_enough_data"
    assert score["nutriscore"]["grade"] is None and score["nutriscore"]["points"] is None
    assert "score" not in score


@pytest.mark.parametrize(
    "changes, status",
    [
        ({"nutriscore_grade": "", "nutriscore": {}}, "not_enough_data"),
        ({"nutriscore_grade": "not-applicable", "nutriscore": {}}, "not_applicable"),
        ({"nutriscore_version": ""}, "not_enough_data"),
    ],
)
def test_page_shows_no_grade_without_enough_data(db, client, off_payload, changes, status):
    product = import_off_product(off_payload(**changes))
    assert score_for_product(product)["status"] == status
    html = client.get(
        reverse("product-alternatives", args=[product.pk]), HTTP_HX_REQUEST="true"
    ).content.decode()
    assert f'data-status="{status}"' in html
    assert "score-value" not in html and "/100" not in html
    assert "Better alternatives" not in html


def test_page_shows_the_official_grade_and_separate_indicators(db, client, off_payload):
    product = import_off_product(off_payload(additives_tags=["en:e330"]))
    html = client.get(
        reverse("product-alternatives", args=[product.pk]), HTTP_HX_REQUEST="true"
    ).content.decode()
    assert "<strong>B</strong>" in html and "2023 algorithm" in html and "1 point" in html
    assert "/100" not in html
    assert "not part of the score" in html and "group 1" in html and "E330" in html
    assert "Data completeness" in html


def test_ai_facts_carry_no_number_without_enough_data():
    assert summary(foodo_score([])) == {
        "version": VERSION,
        "status": "not_enough_data",
        "nutriscore_grade": None,
        "nutriscore_points": None,
        "nutriscore_version": None,
        "reasons": [foodo_score([])["reasons"][0]["text"]],
    }
    ok = summary(foodo_score([ns(grade="b", score=1)]))
    assert ok["nutriscore_grade"] == "b" and ok["nutriscore_points"] == 1
