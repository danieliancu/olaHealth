"""MVP: strict restrictions, the personal layer on real products, My Ola and the home page."""

from __future__ import annotations

import re

import pytest
from django.urls import reverse

from catalog.services.importer import import_off_product
from myola.facts import ProductFacts
from myola.restrictions import all_statuses, allergen_status, blocking, diet_status


def product(**data) -> ProductFacts:
    labels = data.pop("labels", [])
    analysis = data.pop("analysis", [])
    return ProductFacts(id="p", name="P", data=data, labels=labels, analysis=analysis)


def section(items):
    if items is None:
        return {"status": "unknown", "items": []}
    return {"status": "listed" if items else "none_declared", "items": items}


YOGURT = product(
    allergens=section(["en:milk"]),
    traces=section(["en:nuts"]),
    ingredients={"text": "Yogurt (_milk_), strawberries, sugar"},
    labels=["en:vegetarian"],
    analysis=["en:non-vegan", "en:vegetarian"],
)


# ---------- Restrictions ----------
def test_contains_and_may_contain_are_separate():
    assert allergen_status(YOGURT, "en:milk")["status"] == "contains"
    assert allergen_status(YOGURT, "en:nuts")["status"] == "may_contain"
    assert allergen_status(YOGURT, "en:eggs")["status"] == "not_declared"
    assert "safe" not in allergen_status(YOGURT, "en:eggs")["reason"].lower()


def test_unknown_allergens_are_unknown_not_absent():
    p = product(allergens=section(None), traces=section(None))
    assert allergen_status(p, "en:milk")["status"] == "unknown"
    assert diet_status(p, "vegetarian")["status"] == "unknown"


def test_an_empty_field_never_confirms_a_diet():
    juice = product(allergens=section([]), traces=section([]))
    assert diet_status(juice, "gluten_free")["status"] == "unknown"
    bare = product()
    assert {diet_status(bare, d)["status"] for d in ("vegan", "vegetarian", "pork_free")} == {
        "unknown"
    }


def test_evidence_of_incompatibility_wins():
    assert diet_status(YOGURT, "vegan")["status"] == "incompatible"
    oats = product(allergens=section([]), traces=section(["en:gluten"]))
    assert diet_status(oats, "gluten_free")["status"] == "may_contain"
    ham = product(ingredients={"text": "Pork 80%, salt"}, labels=["en:halal"])
    assert diet_status(ham, "pork_free")["status"] == "incompatible"


def test_lactose_free_is_not_the_same_as_a_milk_allergy():
    lactose_free = product(
        allergens=section(["en:milk"]), traces=section([]), labels=["en:lactose-free"]
    )
    assert diet_status(lactose_free, "lactose_free")["status"] == "compatible"
    assert allergen_status(lactose_free, "en:milk")["status"] == "contains"
    assert diet_status(YOGURT, "lactose_free")["status"] == "incompatible"


def test_blocking_lists_only_unconfirmed_active_restrictions():
    blocked = blocking(all_statuses(YOGURT), ["vegetarian", "vegan"], ["en:milk", "en:eggs"])
    assert [b["key"] for b in blocked] == ["vegan", "en:milk"]


# ---------- Pages ----------
@pytest.mark.django_db
def test_home_is_scan_first(client):
    html = client.get(reverse("home")).content.decode()
    assert "Scan a product" in html and 'action="/lookup/"' in html
    assert "demo" not in html.lower() and "magazine" not in html.lower()


@pytest.mark.django_db
def test_navigation_has_four_entries(client):
    html = client.get(reverse("home")).content.decode()
    nav = html.split('<nav class="tabbar"')[1].split("</nav>")[0]
    labels = re.findall(r"<span>([^<]+)</span></a>", nav)
    assert labels == ["Scan", "Products", "Compare", "My Ola"]
    assert 'href="/my-ola/"' in nav


@pytest.mark.django_db
def test_my_ola_and_the_old_saved_route(client):
    for name in ("my-ola", "saved"):
        assert client.get(reverse(name)).status_code == 200
    assert client.get("/demo/").status_code == 404


@pytest.mark.django_db
def test_only_json_data_scripts_and_no_inline_code(client, off_payload):
    pages = [
        reverse("product", args=[import_off_product(off_payload()).pk]),
        reverse("my-ola"),
        reverse("home"),
    ]
    for url in pages:
        html = client.get(url).content.decode()
        for tag in re.findall(r"<script[^>]*>", html):
            assert "src=" in tag or 'type="application/json"' in tag, (url, tag)


@pytest.mark.django_db
def test_product_page_states_and_personal_data(client, off_payload):
    p = import_off_product(off_payload(allergens_tags=[], ingredients_text=""))
    html = client.get(reverse("product", args=[p.pk])).content.decode()
    body = html.split("</head>")[1]
    assert "Allergen information unavailable" in body
    assert "For you" in body and "Ola Score" not in body
    data = html.split('id="ola-data"')[1].split("</script>")[0]
    assert '"allergens": {"en:gluten": {"status": "unknown"' in data
    full = import_off_product(off_payload(code="3017620422003"))
    body = client.get(reverse("product", args=[full.pk])).content.decode().split("</head>")[1]
    assert "Ingredients missing" not in body and "Awaiting moderation" not in body
