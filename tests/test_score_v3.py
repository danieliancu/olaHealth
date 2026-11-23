"""Foodo Score ``score.v3`` (docs/assessment-rules.md, "Foodo Score (score.v3)").

The formula itself is tested in tests/test_foodo_formula.py. Here: statuses, the result,
real products, storage and recalculation, pages, the API and Ask Foodo.

* REAL products from Open Food Facts (tests/fixtures/off/real, ODbL). Expected Foodo values
  were calculated by hand from the published points, version and category (comments).
* SYNTHETIC indicators and products (marked as such) for cases real data cannot give.
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
    core,
    foodo_score,
    foodo_score_v1,
    is_better,
    score_for_product,
    score_for_revision,
    score_from_off,
    summary,
)
from catalog.models import Product, ProductRevision
from catalog.services.importer import import_off_product
from tests.nutri import flags, nutriscore_fields

REAL = Path(__file__).parent / "fixtures" / "off" / "real"


def real(code: str) -> dict:
    data = json.loads((REAL / f"{code}.json").read_text(encoding="utf-8"))
    data.pop("_fixture")
    return data


# --- Synthetic indicators (not real products) ----------------------------------------------


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


def score(items, version="2023", category="general"):
    """foodo_score with OFF's category flags for (version, category)."""
    data = {version: {"data": flags(version, category)}} if category else None
    return foodo_score(items, nutriscore_data=data)


def assert_no_value(result):
    assert result["foodo"]["value"] is None and result["foodo"]["band"] is None
    assert result["comparable_key"] is None
    assert "score" not in result


# --- Statuses (first match wins) --------------------------------------------------------------


def test_no_nutriscore_is_not_enough_data():
    for items in ([], [nova(1), additives()], [ns(status="missing_data", grade=None)]):
        result = score(items)
        assert result["status"] == "not_enough_data"
        assert result["reasons"][0]["code"] == "nutriscore_missing"
        assert_no_value(result)


def test_nova_1_alone_is_not_enough_data():
    """A product with only NOVA 1 and no valid Nutri-Score has no Foodo Score."""
    for items in ([nova(1)], [nova(1), ns(grade="unknown", score=None)]):
        result = score(items)
        assert result["status"] == "not_enough_data" and result["separate"]["nova"]["group"] == 1
        assert_no_value(result)


@pytest.mark.parametrize("grade", ["unknown", "", None, "f", "ab", "1"])
def test_invalid_grade_is_not_enough_data(grade):
    assert_no_value(score([ns(grade=grade)]))


def test_not_current_after_a_local_correction():
    result = score([ns(grade="a", score=-2, current=False)])
    assert (result["status"], result["reasons"][0]["code"]) == (
        "not_enough_data",
        "nutriscore_not_current",
    )
    assert_no_value(result)


def test_not_applicable():
    result = score([ns(grade=None, status="not_applicable", score=None)])
    assert result["status"] == "not_applicable"
    assert_no_value(result)


def test_conflict_with_the_package():
    result = score([ns(grade="b", status="conflicting", packaging_grade="c")])
    assert result["status"] == "conflicting" and result["nutriscore"]["packaging_grade"] == "c"
    assert_no_value(result)


def test_version_unknown():
    result = score([ns(grade="b", version="")])
    assert result["reasons"][0]["code"] == "nutriscore_version_unknown"
    assert_no_value(result)


def test_category_unknown_or_unsupported():
    unknown = score([ns()], category=None)  # no flags at all
    assert (unknown["status"], unknown["reasons"][0]["code"]) == ("unsupported", "category_unknown")
    contradictory = foodo_score(
        [ns()], nutriscore_data={"2023": {"data": {**flags(), "is_beverage": 1, "is_cheese": 1}}}
    )
    assert contradictory["status"] == "unsupported"
    drink_2021 = score([ns(version="2021", grade="b", score=1)], "2021", "beverage")
    assert drink_2021["reasons"][0]["code"] == "category_version_unsupported"
    for result in (unknown, contradictory, drink_2021):
        assert_no_value(result)


@pytest.mark.parametrize("points", [None, "7", True])
def test_missing_points(points):
    result = score([ns(grade="b", score=points)])
    assert result["reasons"][0]["code"] == "nutriscore_points_missing"
    assert "nutriscore_points" in result["missing"]
    assert_no_value(result)


@pytest.mark.parametrize("points", [1.5, 56, -18])
def test_points_outside_the_official_range_or_not_integers(points):
    result = score([ns(grade="b", score=points)])
    assert (result["status"], result["reasons"][0]["code"]) == (
        "inconsistent",
        "points_out_of_range",
    )
    assert_no_value(result)


def test_letter_that_contradicts_its_points():
    # 1 point is B under the 2023 general-foods thresholds, never A or C.
    for grade in ("a", "c"):
        result = score([ns(grade=grade, score=1)])
        assert (result["status"], result["reasons"][0]["code"]) == (
            "inconsistent",
            "grade_mismatch",
        )
        assert_no_value(result)


@pytest.mark.parametrize(
    "grade, points, expected",
    [
        ("a", -2, 82),  # 80 + 20·(0 + 2)/17 = 82.35
        ("b", 1, 79),  # 60 + 19·(2 − 1)/1
        ("c", 5, 54),  # 40 + 19·(10 − 5)/7 = 53.57
        ("d", 14, 31),  # 20 + 19·(18 − 14)/7 = 30.86
        ("e", 25, 16),  # 19·(55 − 25)/36 = 15.83
        ("E", 55, 0),  # 0/100 is a valid score
        ("A", -17, 100),
    ],
)
def test_ok_gives_the_value_and_keeps_the_originals(grade, points, expected):
    result = score([ns(grade=grade, score=points)])
    assert result["status"] == "ok" and result["version"] == VERSION == "score.v3"
    assert result["foodo"] == {
        "value": expected,
        "band": grade.upper(),
        "formula": "foodo-interp.v1",
    }
    assert result["nutriscore"]["grade"] == grade.lower()  # original letter, kept
    assert result["nutriscore"]["points"] == points  # original points, kept
    assert (result["nutriscore"]["version"], result["nutriscore"]["category"]) == (
        "2023",
        "general",
    )
    assert result["comparable_key"] == "nutriscore:2023:general"


def test_water_is_90_from_its_category():
    result = score([ns(grade="a", score=0)], "2023", "water")
    assert (result["status"], result["foodo"]["value"]) == ("ok", 90)


def test_packaging_declared_snapshot_is_never_an_input():
    printed = SimpleNamespace(**{**vars(ns(grade="a", score=-2)), "origin": "packaging_declared"})
    assert score([printed])["status"] == "not_enough_data"
    for order in ([printed, ns(grade="d", score=14)], [ns(grade="d", score=14), printed]):
        assert score(order)["foodo"]["value"] == 31


# --- Separate indicators never enter the formula ------------------------------------------------


def test_nova_additives_and_levels_never_change_the_score():
    """No double penalty: sugars, salt and saturates are already in the Nutri-Score points;
    NOVA, additives and OFF's nutrient levels do not move the Foodo Score."""
    levels = SimpleNamespace(
        indicator="nutrient_levels",
        data_status="available",
        value={"levels": {"sugars": "high", "salt": "high", "saturated-fat": "high"}},
        algorithm_version="",
        warnings=[],
        origin="off_computed",
        invalidated_by_local_change=False,
    )
    base = score([ns(grade="c", score=5), nova(1), additives()])
    for items in (
        [ns(grade="c", score=5), nova(4), additives()],
        [ns(grade="c", score=5), nova(1), additives([f"en:e{i}" for i in range(100, 110)])],
        [ns(grade="c", score=5), nova(1), additives(), levels],
        [ns(grade="c", score=5)],
    ):
        other = score(items)
        assert (
            other["foodo"]
            == base["foodo"]
            == {
                "value": 54,
                "band": "C",
                "formula": "foodo-interp.v1",
            }
        )


def test_separate_indicators_and_no_personal_data_in_the_result():
    result = score([ns(), nova(4), additives(["en:e330"])])
    assert set(result) == {
        "version",
        "status",
        "foodo",
        "nutriscore",
        "comparable_key",
        "separate",
        "missing",
        "reasons",
        "inputs_hash",
    }
    assert result["separate"]["nova"]["group"] == 4
    assert result["separate"]["additives"] == {
        "status": "available",
        "tags": ["en:e330"],
        "count": 1,
        "source": "off_computed",
        "assessed": False,
    }


# --- Comparing -----------------------------------------------------------------------------------


def test_better_only_within_the_same_version_and_category_and_strictly_higher():
    b79 = score([ns(grade="b", score=1)])
    c54 = score([ns(grade="c", score=5)])
    c54_again = score([ns(grade="c", score=5)])
    drink_b = score([ns(grade="b", score=-2)], "2023", "beverage")  # 60 + 19·4/20 = 63.8 -> 64
    old_a = score([ns(grade="a", score=-3, version="2021")], "2021", "general")
    assert is_better(b79, c54) and not is_better(c54, b79)
    assert not is_better(c54_again, c54)  # equal after rounding: not better
    assert not is_better(drink_b, c54)  # another calculation category
    assert not is_better(old_a, c54)  # another version
    assert not is_better(score([]), c54) and not is_better(b79, score([]))
    assert not is_better({"version": "score.v1", "status": "ok", "score": 100}, c54)


# --- Determinism and same result from every source ---------------------------------------


def test_same_inputs_same_result_in_any_order():
    items = [ns(grade="d", score=12), nova(4), additives(["en:e330"])]
    first = score(items)
    assert score(list(reversed(items))) == first and len(first["inputs_hash"]) == 64
    assert score([ns(grade="d", score=13), *items[1:]])["inputs_hash"] != first["inputs_hash"]
    assert score(items, category="cheese")["inputs_hash"] != first["inputs_hash"]


def test_off_object_and_stored_revision_give_the_same_result(db):
    payload = real("20994945")
    product = import_off_product(payload)
    assert core(score_from_off(payload)) == core(score_for_product(product))
    raw = foodo_score(derive_from_off(payload), nutriscore_data=payload["nutriscore"])
    assert raw == core(score_from_off(payload))
    assert all(isinstance(s, IndicatorSpec) for s in derive_from_off(payload))


# --- Real products (Open Food Facts, ODbL) -------------------------------------------------------

# code: (status, category, original grade, points, Foodo, nova group, additives count)
EXPECTED = {
    "3033490004743": ("ok", "cheese", "a", -3, 84, 3, 0),  # skyr: 80 + 20·3/17 = 83.53
    "5010029000016": ("ok", "general", "a", -3, 84, 3, 0),  # cereal: 80 + 20·3/17
    "5000157024671": ("ok", "general", "a", -1, 81, 4, 0),  # beans: 80 + 20·1/17 = 81.18
    "20994945": ("ok", "cheese", "b", 1, 79, 3, 0),  # cottage cheese: 60 + 19·1/1
    "8002470031739": ("ok", "fats", "c", 4, 56, 2, None),  # oil: 40 + 19·6/7 = 56.29
    "3175680011480": ("ok", "general", "c", 9, 43, 4, 4),  # bread: 40 + 19·1/7 = 42.71
    "5449000131805": ("ok", "beverage", "c", 4, 53, 4, 6),  # zero cola: 40 + 19·2/3 = 52.67
    "7622201693916": ("ok", "cheese", "d", 14, 31, 4, 2),  # soft cheese: 20 + 19·4/7 = 30.86
    "3329770063341": ("ok", "beverage", "d", 9, 20, 4, 0),  # yogurt drink: 20 + 19·0/2
    "5449000054227": ("ok", "beverage", "e", 12, 18, 4, 3),  # cola: 19·42/44 = 18.14
    "3017624010701": ("ok", "general", "e", 31, 13, None, 0),  # spread: 19·24/36 = 12.67
    "3068320114453": ("ok", "water", "a", 0, 90, 1, 0),  # water: middle of A
    "0051500241639": ("ok", "fats", "d", 12, 36, 4, 1),  # peanut butter: 20 + 19·6/7 = 36.29
    "8710532469535": ("ok", "fats", "a", -9, 85, 1, 0),  # peanut butter: 80 + 20·3/11 = 85.45
    "75041670": ("not_applicable", "beverage", None, None, None, 3, 0),  # beer
    "10614594": ("not_enough_data", "cheese", None, None, None, None, None),  # no Nutri-Score
}


@pytest.mark.parametrize("code", sorted(EXPECTED))
def test_real_products(code):
    status, category, grade, points, value, group, count = EXPECTED[code]
    result = score_from_off(real(code))
    assert result["status"] == status
    assert result["nutriscore"]["category"] == category
    assert result["nutriscore"]["version"] == "2023"
    assert result["nutriscore"]["grade"] == grade
    assert result["nutriscore"]["points"] == points
    assert result["foodo"]["value"] == value
    assert result["separate"]["nova"]["group"] == group
    assert result["separate"]["additives"]["count"] == count


def test_real_fixtures_are_all_covered_and_carry_their_source():
    assert {p.stem for p in REAL.glob("*.json")} == set(EXPECTED)
    for code in EXPECTED:
        meta = json.loads((REAL / f"{code}.json").read_text(encoding="utf-8"))["_fixture"]
        assert meta["license"].startswith("ODbL") and code in meta["source"]


def test_real_drinks_and_foods_are_never_compared():
    cola, zero = score_from_off(real("5449000054227")), score_from_off(real("5449000131805"))
    assert is_better(zero, cola) and not is_better(cola, zero)  # both drinks: 53 > 18
    bread = score_from_off(real("3175680011480"))  # 43, general foods
    assert not is_better(zero, bread) and not is_better(bread, cola)


# --- Storage and recalculation -------------------------------------------------------------------


def test_publish_stores_the_score_and_the_product_value(db, off_payload):
    product = import_off_product(off_payload())  # synthetic: B, 1 point, 2023 general -> 79
    revision = product.current_revision
    stored = revision.foodo_score
    assert stored["version"] == VERSION and stored["foodo"]["value"] == 79
    assert core(stored) == foodo_score(
        revision.assessments.all(), nutriscore_data=revision.off_payload["nutriscore"]
    )
    assert (stored["source"], stored["data_id"]) == ("off_api", revision.content_hash)
    assert product.foodo_value == 79 and score_for_product(product) == stored


def test_reading_uses_the_stored_result(db, off_payload, django_assert_num_queries):
    product = import_off_product(off_payload())
    revision = ProductRevision.objects.get(pk=product.current_revision_id)
    with django_assert_num_queries(0):
        assert score_for_revision(revision)["version"] == VERSION


def test_older_results_are_recomputed_on_read_never_shown(db, off_payload):
    product = import_off_product(off_payload())
    expected = product.current_revision.foodo_score
    v2 = {**expected, "version": "score.v2"}
    for old in (None, foodo_score_v1({"nutriscore_grade": "b", "nova_group": 1}), v2):
        ProductRevision.objects.filter(pk=product.current_revision_id).update(foodo_score=old)
        product.refresh_from_db()
        assert score_for_product(product) == expected
        assert ProductRevision.objects.get(pk=product.current_revision_id).foodo_score == old


def test_recompute_from_v2_to_v3_without_new_revisions(db, off_payload):
    from django.core.management import call_command

    from assessments.tasks import recompute_foodo_scores

    product = import_off_product(off_payload())
    revision_id = product.current_revision_id
    data_before = ProductRevision.objects.get(pk=revision_id).data
    assert recompute_foodo_scores() == 0  # already current
    for old in (None, {"version": "score.v2", "status": "ok"}, {"version": VERSION}):
        ProductRevision.objects.filter(pk=revision_id).update(foodo_score=old)
        Product.objects.filter(pk=product.pk).update(foodo_value=None)
        assert recompute_foodo_scores() == 1
        assert ProductRevision.objects.get(pk=revision_id).foodo_score["foodo"]["value"] == 79
        assert Product.objects.get(pk=product.pk).foodo_value == 79
    assert ProductRevision.objects.get(pk=revision_id).data == data_before
    assert product.revisions.count() == 1  # history untouched
    call_command("recompute_foodo_scores", "--all")


def test_v3_migration_stores_results_and_product_values(db, off_payload):
    import importlib

    from django.apps import apps

    store = importlib.import_module("catalog.migrations.0012_store_foodo_score_v3").store
    product = import_off_product(off_payload())
    ProductRevision.objects.filter(pk=product.current_revision_id).update(foodo_score=None)
    Product.objects.filter(pk=product.pk).update(foodo_value=None)
    store(apps, None)
    assert Product.objects.get(pk=product.pk).foodo_value == 79


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


def test_local_corrections_and_conflicts_are_recomputed(db, off_payload, admin_user):
    product = import_off_product(off_payload())
    nutrition = dict(product.current_revision.data["nutrition"])
    nutrition["facts"] = [
        dict(f, value="3.5") if f["nutrient"] == "sugars" else f for f in nutrition["facts"]
    ]
    _approve(product, {"nutrition": nutrition}, admin_user)
    product.refresh_from_db()
    assert product.current_revision.foodo_score["reasons"][0]["code"] == "nutriscore_not_current"
    assert product.foodo_value is None
    other = import_off_product(off_payload(code="5000000000999"))
    _approve(other, {"packaging_nutriscore": {"grade": "d", "version": None}}, admin_user)
    other.refresh_from_db()
    assert other.current_revision.foodo_score["status"] == "conflicting"
    assert other.foodo_value is None


# --- Pages, API and Ask Foodo -------------------------------------------------------------


def product_page(client, product):
    return client.get(reverse("product", args=[product.pk])).content.decode()


def test_everyone_sees_one_score_out_of_100_and_no_letter(db, client, off_payload):
    """No setting and no A–E scale: the Foodo Score is the product's only grade."""
    product = import_off_product(off_payload())
    page = product_page(client, product)
    assert 'aria-label="Foodo Score 79 out of 100"' in page
    assert '<strong aria-hidden="true">79</strong>' in page
    assert "ns-scale" not in page and "ns-letters" not in page
    assert "processing-1" in page and "Unprocessed or minimally processed" in page
    assert "Staff details" not in page
    body = client.get(reverse("api-product", args=[product.pk])).json()
    assert body["foodo_score"]["foodo"]["value"] == 79 and "foodo_score_public" not in body
    assert body["nutriscore"] == {"grade": "b", "version": "2023"}  # source field kept
    assert 'id="foodo-score"' in client.get(reverse("sources")).content.decode()


def test_staff_also_see_the_details(db, client, admin_user, off_payload):
    product = import_off_product(off_payload())
    client.force_login(admin_user)
    page = product_page(client, product)
    assert "Staff details" in page and "Foodo Score 79 out of 100" in page


def test_no_nutriscore_letter_on_the_product_page(db, client, off_payload, admin_user):
    from contributions.models import Contribution
    from contributions.services import moderation

    product = import_off_product(off_payload())
    c = Contribution.objects.create(
        product=product,
        base_revision=product.current_revision,
        problem_type="incorrect_data",
        proposed={"packaging_nutriscore": {"grade": "b", "version": "2023"}},
        status="submitted",
    )
    moderation.approve(c.pk, admin_user, "ok")
    page = product_page(client, product)
    assert "Nutri-Score printed on the package" not in page
    assert "About these indicators" not in page or ">Nutri-Score</h3>" not in page


@pytest.mark.parametrize(
    "fields, shown",
    [
        (nutriscore_fields("e", points=55), "0"),  # 0/100 is a score, not a message
        ({"nutriscore_grade": "not-applicable", "nutriscore_score": None}, None),
        (nutriscore_fields(None), None),
    ],
)
def test_page_shows_a_value_or_a_short_message(db, client, off_payload, fields, shown):
    product = import_off_product(off_payload(**fields))
    page = product_page(client, product)
    if shown is None:
        assert "foodo-message" in page and "foodo-value" not in page
    else:
        assert f'<strong aria-hidden="true">{shown}</strong>' in page


def test_lists_and_comparison_show_x_out_of_100(db, client, off_payload):
    a = import_off_product(off_payload())
    b = import_off_product(off_payload(code="5000000000777", **nutriscore_fields("c", points=5)))
    listing = client.get(reverse("products")).content.decode()
    assert "/100" not in listing  # the list cards show no score, only processing
    assert "grade-sm" not in listing  # no letter badges
    client.post(reverse("compare-add", args=[a.pk]))
    client.post(reverse("compare-add", args=[b.pk]))
    compare = client.get(reverse("compare")).content.decode()
    assert "Foodo Score 79 out of 100" in compare and "Foodo Score 54 out of 100" in compare
    assert ">Nutri-Score</th>" not in compare


def test_comparison_flags_other_versions_or_categories(db, off_payload):
    from assessments.services.compare import compare

    a = import_off_product(off_payload())
    b = import_off_product(
        off_payload(code="5000000000777", **nutriscore_fields("c", category="beverage"))
    )
    c = import_off_product(off_payload(code="5000000000778", **nutriscore_fields(None)))
    result = compare([a, b, c])
    assert [n["foodo"] for n in result.nutriscore] == [79, 53, None]
    assert result.nutriscore[2]["message"] == "Not enough data"
    assert "not directly comparable" in result.foodo_note


def test_ask_foodo_answers_with_the_foodo_score(db, off_payload, member):
    from myola.services.chat import ai_facts_for, build_facts, rule_answer

    product = import_off_product(off_payload())
    facts = build_facts(member, "product", product)
    assert facts["product"]["foodo"]["foodo_score"] == "79/100"
    answer = rule_answer("indicators", facts)
    assert "Foodo Score 79/100" in answer and "Nutri-Score B" not in answer
    ai = ai_facts_for(facts, product, with_preferences=False)
    assert ai["foodo_score"]["foodo_score"] == "79/100" and "nutriscore" not in ai["product"]


def test_ai_summary_has_no_letter_and_no_raw_points():
    ok = summary(score([ns(grade="b", score=1)]))
    assert ok["foodo_score"] == "79/100" and "nutriscore_grade" not in ok
    none = summary(score([]))
    assert none["foodo_score"] is None and none["message"] == "Not enough data"
