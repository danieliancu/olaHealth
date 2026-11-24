"""One reading of a chat request on the shared catalogue (myola.services.rag.interpret):
a complete question sets its own product and criteria; references reuse only what they
point to; the same question gives the same results whatever came before. The expected
results are written out here from the test data, not computed by the code under test."""

from __future__ import annotations

import pytest
from django.urls import reverse

from catalog.models import Category
from catalog.services.importer import import_off_product
from myola.models import ChatMessage, DiaryEntry
from myola.services import chat
from myola.services.profiles import get_profile, local_today
from tests.nutri import nutriscore_fields

UK, RO = "en:united-kingdom", "en:romania"
COCONUT_Q = "Ce produse de lapte cu cocos există în UK?"


def category(tag, en, parents=(), **names):
    Category.objects.create(tag=tag, name=en, parents=list(parents),
                            names={"en": [en], **{k: [v] for k, v in names.items()}})  # fmt: skip


def product(off_payload, code, name, tags, countries=(UK,), **nutriments):
    return import_off_product(off_payload(
        code=code, product_name=name, brands="Brand", categories_tags=list(tags),
        countries_tags=list(countries), nutriments=nutriments, nutrient_levels={},
        **nutriscore_fields("c"),
    ))  # fmt: skip


@pytest.fixture
def catalogue(db, off_payload):
    category("en:milks", "Milks", ro="Lapte")
    category("en:coconut-milks-and-creams", "Coconut milks and creams",
             fr="Laits et crèmes de coco")  # fmt: skip
    category("en:desserts", "Desserts", ro="Deserturi")
    category("en:yogurts", "Yogurts", ro="Iaurturi")
    category("en:fishes", "Fishes", ro="Pești")
    coco = ["en:coconut-milks-and-creams"]
    return {
        # relevant, with sugar
        "coconut": product(off_payload, "5000000000707", "Coconut milk", coco,
                           **{"sugars_100g": 1.5}),
        # relevant, no sugar value declared
        "light": product(off_payload, "5000000000714", "Coconut milk light", coco,
                         **{"fat_100g": 8}),
        # sold elsewhere only
        "ro_coconut": product(off_payload, "5000000000721", "Lapte de cocos", coco,
                              countries=(RO,), **{"sugars_100g": 2}),
        # "coconut" in a dessert, and a generic milk: never coconut milk
        "dessert": product(off_payload, "5000000000738", "Coconut pudding", ["en:desserts"],
                           **{"sugars_100g": 20}),
        "milk": product(off_payload, "5000000000745", "Semi skimmed milk", ["en:milks"],
                        **{"sugars_100g": 4.8}),
        "yogurt": product(off_payload, "5000000000752", "Plain yogurt", ["en:yogurts"],
                          **{"sugars_100g": 4}),
        "fish": product(off_payload, "5000000000769", "Mackerel fillets", ["en:fishes"],
                        **{"fat_100g": 25, "salt_100g": 1}),
        "fish_lean": product(off_payload, "5000000000776", "Cod fillets", ["en:fishes"],
                             **{"fat_100g": 0.7, "salt_100g": 0.3}),
    }  # fmt: skip


def ask(member, text, context="personal"):
    return chat.send(member, context, text)[1]


def urls(answer):
    return {s["url"] for s in answer.sources}


def url(p):
    return reverse("product", args=[p.pk])


# ---------- A: the same complete question, the same results ----------
def test_coconut_question_gives_the_same_results_whatever_came_before(member, catalogue):
    expected = {url(catalogue["coconut"]), url(catalogue["light"])}  # UK coconut milks only
    after_sugar = [ask(member, "Ce iaurt are mai puțin zahăr?"), ask(member, COCONUT_Q)][1]
    repeated = ask(member, COCONUT_Q)
    ChatMessage.objects.filter(user=member).delete()  # a new conversation
    fresh = ask(member, COCONUT_Q)
    for answer in (after_sugar, repeated, fresh):
        request = answer.details["request"]
        assert urls(answer) == expected
        assert request["intent"] == "discover" and request["subject"] is None  # no "zahăr"
        assert request["inherited"] == [] and request["market"] == UK
        assert request["filters"]["categories"] == ["en:coconut-milks-and-creams"]
        assert "listate pentru Regatul Unit în catalogul AskFoodo (2)" in answer.text
        assert "pudding" not in answer.text and "Semi skimmed" not in answer.text


def test_coconut_question_in_english_and_french_finds_the_same_products(member, catalogue):
    expected = {url(catalogue["coconut"]), url(catalogue["light"])}
    assert urls(ask(member, "Which coconut milk products are available in the UK?")) == expected
    assert urls(ask(member, "Quel lait de coco y a-t-il au Royaume-Uni?")) == expected


def test_nothing_found_is_said_plainly(member, catalogue):
    out = ask(member, "Which almond milk is sold in the UK?")
    assert out.sources == []
    assert out.text.startswith("I found no “almond milk” listed for the UK")


# ---------- B: "which of them" compares only the previous group ----------
def test_which_of_them_has_less_sugar_uses_only_the_previous_products(member, catalogue):
    ask(member, COCONUT_Q)
    out = ask(member, "Care dintre ele are mai puțin zahăr?")
    request = out.details["request"]
    assert request["intent"] == "group" and request["inherited"] == ["products"]
    assert urls(out) == {url(catalogue["coconut"])}  # the only one with a sugar value
    assert "- Coconut milk (Brand): 1,5 g" in out.text
    assert "Am lăsat deoparte 1 fără o valoare comparabilă pentru zahăr" in out.text


def test_which_of_them_when_none_has_the_value(member, catalogue):
    ask(member, COCONUT_Q)
    out = ask(member, "Which of them has the most fibre?")
    assert out.text.startswith("None of the 2 products of the previous answer has a comparable")


def test_which_of_them_without_a_previous_list_asks_back(member, catalogue):
    out = ask(member, "Care dintre ele are mai puțin zahăr?")
    assert out.text.startswith("Care produse?") and out.sources == []


# ---------- C: other products, wording, markets, subjects, diary ----------
def test_another_market_keeps_the_search(member, catalogue):
    ask(member, COCONUT_Q)
    out = ask(member, "Dar în România?")
    request = out.details["request"]
    assert request["market"] == RO and request["inherited"] == ["concept", "subject"]
    assert urls(out) == {url(catalogue["ro_coconut"])}


def test_a_new_subject_starts_a_new_search(member, catalogue):
    ask(member, COCONUT_Q)
    out = ask(member, "Which fish has the least fat?")
    request = out.details["request"]
    assert request["concept"]["tags"] == ["en:fishes"] and request["subject"] == "fat"
    assert out.text.splitlines()[1] == "- Cod fillets (Brand): 0.7 g fat per 100 g"


def test_from_the_diary_to_the_catalogue(member, catalogue):
    DiaryEntry.objects.create(
        user=member, date=local_today(get_profile(member)), meal="lunch", name="Mackerel salad",
        amount=150, unit="g",
    )  # fmt: skip
    diary = ask(member, "Ce am mâncat azi?")
    assert diary.text == "Azi în jurnal: Prânz: Mackerel salad."
    out = ask(member, "Ce pește îmi recomanzi pentru aceste grăsimi?")
    request = out.details["request"]
    assert request["concept"]["tags"] == ["en:fishes"] and request["subject"] == "fat"
    assert out.sources[0]["url"] == url(catalogue["fish"])  # the most fat: "these fats"


def test_same_results_without_the_ai(member, catalogue, settings):
    from staff.models import SiteSettings

    site = SiteSettings.load()
    site.ai_enabled = False
    site.save()
    out = ask(member, COCONUT_Q)
    assert urls(out) == {url(catalogue["coconut"]), url(catalogue["light"])}


def test_saved_products_come_from_the_users_data(member, catalogue, client):
    from myola.services import lists

    lists.toggle(member, catalogue["coconut"], "favorite")
    assert ask(member, "Ce am salvat?").text == "Ai salvat: Coconut milk."
