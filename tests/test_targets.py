"""Settings → Daily targets → Calculate automatically."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.urls import reverse

from myola.models import Profile
from myola.services import targets

HX = {"HTTP_HX_REQUEST": "true"}


def est(**kw):
    values = {
        "age": 34,
        "sex": "female",
        "height": 168,
        "weight": 70,
        "activity_level": "light",
        "goal": "lose_weight",
    }
    return targets.estimate(**{**values, **kw})


def test_estimate_hand_checked_examples():
    # Resting: 700 + 1050 - 170 - 161 = 1419; x 1.6 = 2270; - 600 = 1670.
    e = est()
    assert (e.kcal, e.protein, e.fat, e.carbs, e.resting) == (1670, 55, 55, 240, 1420)
    # Men, active, muscle first: 800 + 1125 - 150 + 5 = 1780; x 1.8 + 250 = 3454.
    e = est(age=30, sex="male", height=180, weight=80, activity_level="active", goal="muscle_first")
    assert (e.kcal, e.protein, e.fat, e.carbs) == (3450, 130, 115, 475)
    # "In between": the average of the two equations (-78).
    assert est(sex="between").kcal == 1670 + 130  # (161 - 78) x 1.6 = 132.8, rounded
    assert est(goal="eat_healthier").kcal == 2270 and est(goal="muscle_and_fat").kcal == 1970


def test_estimate_guardrails():
    # BMI under 18.5: no deficit, and a note.
    e = est(age=25, height=170, weight=50, activity_level="sedentary")
    assert e.kcal == 1790 and any("18.5" in n for n in e.notes)
    # Never below resting energy.
    assert est(age=60, height=150, weight=45, activity_level="sedentary").kcal == 930
    # BMI 30 or more: protein from the weight at BMI 25.
    assert est(height=170, weight=100, goal="eat_healthier").protein == 55
    # Adults only.
    assert est(age=16).refused
    # Health answers: check with a GP or dietitian.
    assert any("GP" in n for n in est(conditions=["diabetes"]).notes)
    assert not est(conditions=["none"]).notes


def _profile(member, **fields):
    return Profile.objects.update_or_create(user=member, defaults=fields)[0]


COMPLETE = {
    "age": 34,
    "gender": "female",
    "height": Decimal("168"),
    "weight": Decimal("70"),
    "activity_level": "light",
    "goal": "lose_weight",
}


@pytest.mark.django_db
def test_complete_profile_shows_the_four_values_and_saves_only_on_save(member_client, member):
    p = _profile(member, **COMPLETE, target_kcal=2000)
    html = member_client.get(reverse("targets-calculate"), **HX).content.decode()
    assert 'value="1670"' in html and 'value="55"' in html and 'value="240"' in html
    assert "Save targets" in html and "Your current targets:" in html
    p.refresh_from_db()
    assert p.target_kcal == 2000  # nothing changes until Save
    r = member_client.post(
        reverse("targets-save"),
        {"t-target_kcal": "1700", "t-target_protein": "60", "t-target_carbs": "230",
         "t-target_fat": "55", "d-sex": "between"},
        **HX,
    )  # fmt: skip
    # A different URL from the open Settings page, so the browser reloads it (not just scrolls)
    assert r.status_code == 204 and r["HX-Redirect"].endswith("?saved=targets#targets")
    p.refresh_from_db()
    assert (p.target_kcal, p.target_protein, p.target_carbs, p.target_fat) == (1700, 60, 230, 55)


@pytest.mark.django_db
def test_only_missing_details_are_asked_and_saved_with_the_targets(member_client, member):
    _profile(member, age=34, gender="female", height=Decimal("168"), goal="lose_weight")
    html = member_client.get(reverse("targets-calculate"), **HX).content.decode()
    assert 'name="d-weight"' in html and 'name="d-activity_level"' in html
    assert 'name="d-age"' not in html and 'name="d-goal"' not in html
    details = {"asked": ["weight", "activity_level"], "d-weight": "70", "d-activity_level": "light"}
    html = member_client.post(reverse("targets-calculate"), details, **HX).content.decode()
    assert 'value="1670"' in html
    p = Profile.objects.get(user=member)
    assert p.weight is None and p.activity_level == ""  # not saved before Save
    member_client.post(
        reverse("targets-save"),
        {**details, "t-target_kcal": "1670", "t-target_protein": "55", "t-target_carbs": "240",
         "t-target_fat": "55"},
        **HX,
    )  # fmt: skip
    p.refresh_from_db()
    assert p.weight == Decimal("70") and p.activity_level == "light" and p.target_kcal == 1670


@pytest.mark.django_db
def test_other_genders_choose_the_equation(member_client, member):
    _profile(member, **{**COMPLETE, "gender": "non_binary"})
    html = member_client.get(reverse("targets-calculate"), **HX).content.decode()
    assert 'name="d-sex"' in html and 'value="1800"' in html  # in between by default
    html = member_client.post(
        reverse("targets-calculate"), {"d-sex": "female"}, **HX
    ).content.decode()
    assert 'value="1670"' in html


@pytest.mark.django_db
def test_no_calculator_for_eating_disorders(member_client, member):
    _profile(member, **COMPLETE, conditions=["eating_disorder"])
    assert member_client.get(reverse("targets-calculate")).status_code == 404
    assert (
        "Calculate automatically"
        not in member_client.get(reverse("settings-edit")).content.decode()
    )


@pytest.mark.django_db
def test_settings_button_and_profile_card(member_client, member):
    _profile(member, **COMPLETE, target_kcal=1800)
    html = member_client.get(reverse("settings-edit")).content.decode()
    assert "Calculate automatically" in html and 'id="targets-dialog"' in html
    page = member_client.get(reverse("targets-calculate"))  # without JavaScript: a page
    assert page.status_code == 200 and "Save targets" in page.content.decode()
    overview = member_client.get(reverse("ask-foodo") + "?tab=profile").content.decode()
    assert "Daily targets and display" not in overview and "<span>Daily targets</span>" in overview
    assert "Calculate automatically" not in overview
