"""MVP: strict restrictions, the personal layer on real products, My Track and the home page."""

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
    assert "Get started" in html and 'href="/accounts/signup/"' in html and 'href="/scan/"' in html
    assert "Welcome to AskFoodo" in html and 'class="quick-actions stagger"' in html
    assert "try the demo" not in html.lower()


@pytest.mark.django_db
def test_navigation_has_five_entries(client):
    html = client.get(reverse("home")).content.decode()
    nav = html.split('<nav class="tabbar"')[1].split("</nav>")[0]
    labels = re.findall(r"<span>([^<]+)</span></a>", nav)
    assert labels == ["Home", "Scan", "Ask Foodo", "Progress", "Magazine"]
    assert 'href="/learn/"' in nav and 'href="/"' in nav and 'href="/ask-foodo/chat/"' in nav
    # Search and favourites are in the header; comparison opens from the product pages.
    header = html.split('<header class="site-header">')[1].split("</header>")[0]
    assert 'href="/products/"' in header and 'href="/compare/"' not in header
    assert 'href="/accounts/login/"' in header  # visitors: the account icon signs in


@pytest.mark.django_db
def test_my_ola_and_the_old_saved_route(member_client):
    for name in ("ask-foodo", "saved", "start", "today", "progress"):
        assert member_client.get(reverse(name)).status_code == 200
    assert member_client.get("/demo/").status_code == 404


@pytest.mark.django_db
def test_only_json_data_scripts_and_no_inline_code(member_client, off_payload):
    client = member_client
    pages = [
        reverse("product", args=[import_off_product(off_payload()).pk]),
        reverse("ask-foodo"),
        reverse("today"),
        reverse("account_email"),
    ]
    for url in pages:
        html = client.get(url).content.decode()
        for tag in re.findall(r"<script[^>]*>", html):
            assert "src=" in tag or 'type="application/json"' in tag, (url, tag)


# ---------- Onboarding ----------
@pytest.mark.django_db
def test_onboarding_has_the_four_steps_in_order(member_client):
    html = member_client.get(reverse("start")).content.decode()
    titles = [
        "What is your goal?",
        "About you",
        "Do you have any dietary restrictions or food allergies?",
        "Have you ever had any of the following conditions?",
    ]
    positions = [html.index(t) for t in titles]
    assert positions == sorted(positions)
    for option in (
        "Losing weight",
        "Gaining muscle and losing fat",
        "Gaining muscle, losing fat is secondary",
        "Eating healthier without losing weight",
        "Non-binary",
        "Prefer not to say",
        "Pescetarian­ism",  # soft hyphen: breaks only on narrow cards
        "Lactose intolerant",
        "Seafood or shellfish",
        "Religious restriction",
        "Tree nuts",
        "Crustaceans",
        "Eating disorders (anorexia, bulimia)",
        "Prefer not to answer",
    ):
        assert option in html, option
    # Examples are placeholders, never values.
    for example in ('placeholder="48"', 'placeholder="180"', 'placeholder="100"'):
        assert example in html
    assert 'value="48"' not in html
    for unwanted in ("hear about", "motivation", "ideal weight", "subscribe", "trial"):
        assert unwanted not in html.lower()


# ---------- Portions for the diary ----------
def test_portions_follow_the_basis_and_never_mix_g_and_ml():
    from myola.portions import portions

    yogurt = portions({"serving_size": "125 g", "quantity": "4 x 125 g"}, "per_100g")
    assert yogurt["unit"] == "g"
    assert [o["amount"] for o in yogurt["options"]] == ["125"]
    drink = portions({"serving_size": "250 ml", "quantity": "1 l"}, "per_100ml")
    assert drink["unit"] == "ml" and [o["key"] for o in drink["options"]] == ["serving", "pack"]
    mixed = portions({"serving_size": "200 ml", "quantity": "500 g"}, "per_100g")
    assert mixed["unit"] == "g" and [o["key"] for o in mixed["options"]] == ["pack"]
    unknown = portions({"serving_size": "30 g"}, None)
    assert unknown == {"unit": None, "options": []}
    multipack_unknown = portions({"quantity": "6 x"}, "per_100g")
    assert multipack_unknown["options"] == []


def test_pescetarian_rule():
    fish = product(ingredients={"text": "Tuna, water, salt"}, analysis=["en:non-vegetarian"])
    assert diet_status(fish, "pescatarian")["status"] == "unknown"
    beef = product(ingredients={"text": "Beef 80%, salt"})
    assert diet_status(beef, "pescatarian")["status"] == "incompatible"
    veg = product(labels=["en:vegetarian"])
    assert diet_status(veg, "pescatarian")["status"] == "compatible"


# ---------- From the Food diary straight to "how much?" ----------
@pytest.mark.django_db
def test_diary_mode_goes_straight_to_the_amount_step(client, off_payload):
    p = import_off_product(off_payload())
    code = off_payload()["code"]
    r = client.get(reverse("lookup"), {"code": code, "add": "lunch", "date": "2026-11-18"})
    assert r.status_code == 302
    assert r["Location"].startswith(reverse("add-food", args=[p.pk]))
    assert "meal=lunch" in r["Location"] and "date=2026-11-18" in r["Location"]
    assert f"scanned={code}" in r["Location"]
    # Without the diary mode the product page opens, as before.
    r = client.get(reverse("lookup"), {"code": code})
    assert r["Location"].startswith(reverse("product", args=[p.pk]))


@pytest.mark.django_db
def test_diary_mode_in_scan_and_catalogue(client, off_payload):
    p = import_off_product(off_payload())
    scan = client.get(reverse("scan"), {"add": "dinner", "date": "2026-11-18"}).content.decode()
    assert 'name="add" value="dinner"' in scan and "Adding to <strong>Dinner</strong>" in scan
    assert 'data-diary-query="add=dinner&amp;date=2026-11-18"' in scan
    listing = client.get(
        reverse("products"), {"add": "dinner", "date": "2026-11-18"}
    ).content.decode()
    assert reverse("add-food", args=[p.pk]) + "?meal=dinner&amp;date=2026-11-18" in listing
    # Unknown values are ignored: no diary mode.
    plain = client.get(reverse("scan"), {"add": "brunch"}).content.decode()
    assert 'name="add"' not in plain


@pytest.mark.django_db
def test_old_my_ola_addresses_redirect(client):
    r = client.get("/my-ola/settings/?x=1")
    assert r.status_code == 301 and r["Location"] == "/ask-foodo/settings/?x=1"
    assert reverse("ask-foodo") == "/ask-foodo/"
