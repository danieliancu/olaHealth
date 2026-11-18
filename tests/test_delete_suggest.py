import pytest
from django.urls import reverse

from catalog.models import Product
from catalog.services.importer import import_off_product

pytestmark = pytest.mark.django_db


@pytest.fixture
def product(off_payload):
    return import_off_product(off_payload())


def test_bin_hides_locally_and_never_deletes(client, product):
    html = client.get(reverse("products")).content.decode()
    assert "data-hide-product" in html  # visible to every visitor
    assert "/delete/" not in html  # no server-side delete from the catalogue page
    assert Product.objects.filter(pk=product.pk).exists()


def test_suggest_returns_matches_as_you_type(client, product):
    assert client.get(reverse("api-suggest"), {"q": "g"}).json() == {"results": []}  # < 2 chars
    data = client.get(reverse("api-suggest"), {"q": "greek"}).json()["results"]
    assert [p["name"] for p in data] == ["Greek style yogurt"]
    assert data[0]["url"] == reverse("product", args=[product.pk])
    assert client.get(reverse("api-suggest"), {"q": "zzzz"}).json()["results"] == []


def test_dialog_is_on_every_page(client):
    html = client.get(reverse("home")).content.decode()
    assert "data-dialog" in html and "js/dialog" in html


def test_short_queries_do_not_return_fuzzy_matches(client, product, off_payload):
    import_off_product(off_payload(code="3000000000014", product_name="Organic cane sugar"))
    names = [p["name"] for p in client.get(reverse("api-suggest"), {"q": "gre"}).json()["results"]]
    assert names == ["Greek style yogurt"]
