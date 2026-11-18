import pytest

from assessments.rules import derive_from_off
from catalog.services.importer import import_off_product
from contributions.models import Contribution
from contributions.services import moderation

pytestmark = pytest.mark.django_db


def specs(payload):
    return {s.indicator: s for s in derive_from_off(payload)}


def test_nutriscore_available_with_version_and_warning(off_payload):
    s = specs(off_payload())["nutriscore"]
    assert s.data_status == "available"
    assert s.value["grade"] == "b"
    assert s.algorithm_version == "2023"
    assert s.value["other_versions"] == {"2021": "a"}
    assert any("estimated from the ingredient list" in w for w in s.warnings)


@pytest.mark.parametrize(
    ("grade", "status"),
    [("not-applicable", "not_applicable"), ("unknown", "missing_data"), (None, "missing_data")],
)
def test_nutriscore_statuses(off_payload, grade, status):
    assert specs(off_payload(nutriscore_grade=grade))["nutriscore"].data_status == status


def test_missing_field_is_never_not_applicable(off_payload):
    payload = off_payload()
    for key in ("nova_group", "nova_groups_tags", "nutrient_levels", "ingredients_analysis_tags"):
        payload.pop(key)
    s = specs(payload)
    assert s["nova"].data_status == "missing_data"
    assert s["nutrient_levels"].data_status == "missing_data"
    assert s["ingredients_analysis"].data_status == "missing_data"


def test_additives_need_ingredients(off_payload):
    assert specs(off_payload(ingredients_text=""))["additives"].data_status == "missing_data"
    s = specs(off_payload())["additives"]
    assert s.data_status == "available" and s.value["tags"] == []


def _approve(product, proposed, admin):
    c = Contribution.objects.create(
        product=product,
        base_revision=product.current_revision,
        problem_type="incorrect_data",
        proposed=proposed,
        status="submitted",
    )
    return moderation.approve(c.pk, admin, "ok")


def invalidated(product):
    product.refresh_from_db()
    return {
        s.indicator: s.invalidated_by_local_change
        for s in product.current_revision.assessments.filter(origin="off_computed")
    }


def _with_sugars(product, value):
    nutrition = dict(product.current_revision.data["nutrition"])
    nutrition["facts"] = [
        dict(f, value=value) if f["nutrient"] == "sugars" else f for f in nutrition["facts"]
    ]
    return nutrition


def test_nutrition_correction_invalidates_only_dependent_indicators(off_payload, admin_user):
    p = import_off_product(off_payload())
    _approve(p, {"nutrition": _with_sugars(p, "3.5")}, admin_user)
    m = invalidated(p)
    assert m["nutriscore"] is True and m["nutrient_levels"] is True
    assert m["nova"] is False and m["additives"] is False and m["ingredients_analysis"] is False
    assert p.nutriscore_grade == ""  # not presented or sorted as current


def test_ingredient_correction_invalidates_nova_and_ingredient_analyses(off_payload, admin_user):
    p = import_off_product(off_payload())
    _approve(p, {"ingredients": {"text": "Milk, sugar, starch.", "lang": "en"}}, admin_user)
    m = invalidated(p)
    assert m["nova"] is True and m["additives"] is True and m["ingredients_analysis"] is True
    assert m["nutrient_levels"] is False


def test_name_change_invalidates_nothing(off_payload, admin_user):
    p = import_off_product(off_payload())
    _approve(p, {"name": "Better name"}, admin_user)
    assert not any(invalidated(p).values())
    assert p.nutriscore_grade == "b"


def test_packaging_grade_is_separate_and_conflict_is_flagged(off_payload, admin_user):
    p = import_off_product(off_payload())
    _approve(p, {"packaging_nutriscore": {"grade": "c", "version": None}}, admin_user)
    p.refresh_from_db()
    snaps = {(s.indicator, s.origin): s for s in p.current_revision.assessments.all()}
    assert snaps[("nutriscore", "packaging_declared")].value["grade"] == "c"
    off = snaps[("nutriscore", "off_computed")]
    assert off.data_status == "conflicting" and off.value["grade"] == "b"
    assert off.invalidated_by_local_change is False


def test_reimport_after_correction_keeps_indicator_not_current(off_payload, admin_user):
    p = import_off_product(off_payload())
    _approve(p, {"nutrition": _with_sugars(p, "3.5")}, admin_user)
    import_off_product(off_payload(nutriscore_grade="c"))
    p.refresh_from_db()
    ns = p.current_revision.assessments.get(indicator="nutriscore", origin="off_computed")
    assert ns.value["grade"] == "c" and ns.invalidated_by_local_change is True
    assert p.current_revision.data["nutrition"] == _with_sugars(p, "3.5")
