"""Per 100 g or per 100 ml worked out from the product type when the quantity does not say."""

from __future__ import annotations

import pytest
from django.core.management import call_command
from django.urls import reverse

from catalog.models import Product
from catalog.nutrition import basis_label
from catalog.services.importer import import_off_product
from off import basis
from off.mapping import map_product


@pytest.mark.parametrize(
    ("categories", "name", "expected"),
    [
        (["en:dairies", "en:fermented-milk-products", "en:cheeses"], "Cottage cheese", "per_100g"),
        (["en:snacks", "en:sweet-snacks", "en:confectioneries", "en:candies"], "", "per_100g"),
        (["en:beverages", "en:juices", "en:fruit-juices"], "Orange", "per_100ml"),
        (["en:dairies", "en:milks"], "Semi-skimmed", "per_100ml"),
        (["en:fats", "en:vegetable-oils", "en:olive-oils"], "Olive oil", None),  # either way
        (["en:beverages", "en:instant-beverages"], "Hot chocolate", None),  # a powder
        (["en:dairies", "en:yogurts", "en:drinkable-yogurts"], "", None),
        ([], "Carrot Cake", "per_100g"),
        ([], "Coconut water", "per_100ml"),
        ([], "Milk chocolate", None),  # words of both kinds
        ([], "Mystery", None),
        (["en:cheeses"], "Milk drink", "per_100g"),  # the type wins over a word
    ],
)
def test_infer(categories, name, expected):
    assert basis.infer(categories, name) == expected


def _no_quantity(off_payload, **changes):
    return off_payload(quantity="", product_quantity="", product_quantity_unit="", **changes)


def test_mapping_marks_an_inferred_basis(off_payload):
    data = map_product(_no_quantity(off_payload, categories_tags=["en:dairies", "en:yogurts"]))
    facts = data["nutrition"]["facts"]
    assert {f["basis"] for f in facts if f["basis"] != "per_serving"} == {"per_100g"}
    assert data["nutrition"]["basis_inferred"] is True
    assert basis_label("per_100g", data) == "per 100 g (from the product type)"
    stated = map_product(off_payload(quantity="4 x 125 g"))
    assert "basis_inferred" not in stated["nutrition"]
    assert basis_label("per_100g", stated) == "per 100 g"
    unknown = map_product(_no_quantity(off_payload, categories_tags=["en:sauces"]))
    assert {f["basis"] for f in unknown["nutrition"]["facts"]} <= {
        "per_100_unspecified",
        "per_serving",
    }


@pytest.mark.django_db
def test_remap_command_and_product_page(off_payload, client, monkeypatch):
    payload = _no_quantity(off_payload, categories_tags=["en:dairies", "en:yogurts"])
    # Imported before the rule existed: not stated.
    monkeypatch.setattr(basis, "infer", lambda *a: None)
    product = import_off_product(payload)
    assert product.current_revision.facts.filter(basis="per_100_unspecified").exists()
    monkeypatch.undo()
    call_command("remap_off", "--unspecified")
    product = Product.objects.get(pk=product.pk)
    assert not product.current_revision.facts.filter(basis="per_100_unspecified").exists()
    html = client.get(reverse("product", args=[product.pk])).content.decode()
    assert "per 100 g (from the product type)" in html
