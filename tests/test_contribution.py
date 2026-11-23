"""One declared portion towards the user's daily targets: personal, never part of the score."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.urls import reverse

from assessments.score import score_for_product
from catalog.services.importer import import_off_product
from myola.contribution import portion_contribution

pytestmark = pytest.mark.django_db

TARGETS = {"energy-kcal": 2000, "proteins": 50, "carbohydrates": 250, "fat": 70}


def profile(**overrides):
    base = {"show_targets": True, "energy_visible": True, "targets": dict(TARGETS)}
    return SimpleNamespace(**{**base, **overrides})


@pytest.fixture
def yogurt(off_payload):
    # Per 100 g: 97 kcal, protein 9.2 g, carbohydrate 3.6 g, fat 5 g. Serving 125 g.
    return import_off_product(off_payload(serving_size="125 g"))


def by_nutrient(result):
    return {i["nutrient"]: i for i in result["items"]}


def test_serving_against_each_target(yogurt):
    result = portion_contribution(yogurt.current_revision.data, profile())
    assert result["portion"]["key"] == "serving" and result["portion"]["amount"] == "125"
    items = by_nutrient(result)
    # 97 x 1.25 = 121.25 kcal of 2000 -> 6 %; 9.2 x 1.25 = 11.5 g of 50 -> 23 %;
    # 3.6 x 1.25 = 4.5 g of 250 -> 2 %; 5 x 1.25 = 6.25 g of 70 -> 9 %.
    assert (items["energy-kcal"]["value"], items["energy-kcal"]["percent"]) == ("121.25", 6)
    assert (items["proteins"]["value"], items["proteins"]["percent"]) == ("11.5", 23)
    assert (items["carbohydrates"]["value"], items["carbohydrates"]["percent"]) == ("4.5", 2)
    assert (items["fat"]["value"], items["fat"]["percent"]) == ("6.25", 9)


def test_energy_hidden_when_calories_are_not_shown(yogurt):
    items = by_nutrient(
        portion_contribution(yogurt.current_revision.data, profile(energy_visible=False))
    )
    assert "energy-kcal" not in items and "proteins" in items


def test_only_targets_that_are_set(yogurt):
    result = portion_contribution(
        yogurt.current_revision.data, profile(targets={"proteins": 60, "fat": None})
    )
    assert list(by_nutrient(result)) == ["proteins"]


@pytest.mark.parametrize(
    "prof",
    [None, profile(show_targets=False), profile(targets={})],
)
def test_nothing_without_profile_or_targets(yogurt, prof):
    assert portion_contribution(yogurt.current_revision.data, prof) is None


def test_nothing_without_a_declared_portion(off_payload):
    product = import_off_product(off_payload(serving_size="", quantity="500 g"))
    # The whole pack is never used as a portion.
    assert portion_contribution(product.current_revision.data, profile()) is None


def test_missing_value_is_left_out_never_zero(off_payload):
    payload = off_payload(serving_size="125 g")
    payload["nutriments"] = {k: v for k, v in payload["nutriments"].items() if "fat" not in k}
    product = import_off_product(payload)
    assert "fat" not in by_nutrient(portion_contribution(product.current_revision.data, profile()))


def test_portion_and_profile_never_change_the_score(
    client, member, off_payload, django_user_model, monkeypatch
):
    from myola.services.profiles import get_profile

    monkeypatch.setattr("catalog.services.alternatives.alternatives", lambda *a, **k: [])

    product = import_off_product(off_payload(serving_size="125 g"))
    before = score_for_product(product)
    other = django_user_model.objects.create_user("other", "o@example.com", "a-long-test-pw-2")
    fragments = []
    for user, targets in ((member, (2000, 50)), (other, (1400, 120))):
        p = get_profile(user)
        p.target_kcal, p.target_protein = targets
        p.save()
        client.force_login(user)
        page = client.get(reverse("product", args=[product.pk])).content.decode()
        assert "of your daily targets" in page
        assert "do not change the product&#x27;s score" in page or (
            "do not change the product's score" in page
        )
        fragments.append(
            client.get(
                reverse("product-alternatives", args=[product.pk]), HTTP_HX_REQUEST="true"
            ).content.decode()
        )
    product.refresh_from_db()
    assert score_for_product(product) == before
    assert fragments[0] == fragments[1]  # the score card is the same for everyone
