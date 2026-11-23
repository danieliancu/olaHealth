"""Product data quality: weak products are kept out of listings and marked (catalog/quality.py)."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from catalog import quality
from catalog.services.importer import import_off_product
from catalog.services.search import SearchParams, search_products


def test_name_checks():
    assert quality.name_issue("", "Brand") == quality.NAME_MISSING
    assert quality.name_issue("5000112", "") == quality.NAME_INCOMPLETE  # digits only
    assert quality.name_issue("ab", "") == quality.NAME_INCOMPLETE
    assert quality.name_issue("lindt", "Lindt") == quality.NAME_INCOMPLETE  # just the brand
    assert quality.name_issue("Lindt Excellence 70%", "Lindt") == ""
    assert quality.name_issue("Brânză telemea", "") == ""


def test_issues_and_listable():
    good = {"name": "Greek yogurt", "nutrition": {"status": "available", "facts": [1]},
            "categories": ["en:dairies", "en:yogurts"]}  # fmt: skip
    assert quality.issues(good, has_front_image=True) == []
    no_photo_broad = {**good, "categories": ["en:dairies"]}
    found = quality.issues(no_photo_broad, has_front_image=False)
    assert found == [quality.CATEGORY_MISSING, quality.PHOTO_MISSING]
    assert quality.listable(found)  # marked, not hidden
    found = quality.issues({**good, "nutrition": {"status": "unknown", "facts": []}},
                           has_front_image=True)  # fmt: skip
    assert found == [quality.NUTRITION_MISSING] and not quality.listable(found)
    no_table = {**good, "nutrition": {"status": "not_on_package", "facts": []}}
    assert quality.listable(quality.issues(no_table, has_front_image=True))


@pytest.mark.django_db
def test_weak_products_are_hidden_from_listings_but_still_open(client, off_payload):
    good = import_off_product(off_payload(code="5000000000401", product_name="Good yogurt"))
    weak = import_off_product(off_payload(code="5000000000402", product_name="", nutriments={}))
    assert good.listable and not weak.listable
    assert set(weak.quality_issues) >= {quality.NAME_MISSING, quality.NUTRITION_MISSING}
    ids = {p.pk for p in search_products(SearchParams())}
    assert good.pk in ids and weak.pk not in ids
    assert weak.pk in {p.pk for p in search_products(SearchParams(include_unlisted=True))}
    # Still reachable by link (and barcode), with the reasons shown.
    html = client.get(reverse("product", args=[weak.pk])).content.decode()
    assert "Not shown in search" in html and "Name incomplete" in html


@pytest.mark.django_db
def test_incomplete_data_pill_on_cards_and_staff_filter(client, off_payload):
    p = import_off_product(off_payload(code="5000000000411", product_name="Plain yogurt",
                                       image_front_url=""))  # fmt: skip
    assert quality.PHOTO_MISSING in p.quality_issues and p.listable
    html = client.get(reverse("products")).content.decode()
    assert "Incomplete data" in html and "No photo" in html
    staff = get_user_model().objects.create_superuser("boss", "boss@example.com", "pw-long-123!")
    client.force_login(staff)
    html = client.get(reverse("staff:products") + "?quality=incomplete").content.decode()
    assert "Plain yogurt" in html and "No photo" in html


@pytest.mark.django_db
def test_sources_page_explains_the_foodo_score(client):
    html = client.get(reverse("sources")).content.decode()
    assert "does not compute its own score" not in html
    assert "are imported from Open Food Facts" in html
    assert "starts at 100" not in html
    assert 'id="foodo-score"' in html
    assert "AskFoodo’s conversion of the official Nutri-Score" in html
    assert "foodo = L + (U − L) × (p_max − p) / (p_max − p_min)" in html
    assert "shown as 54/100" in html  # the worked example
    assert "not a clinical or percentage difference" in html


@pytest.mark.django_db
def test_staff_can_switch_off_an_unsuitable_photo_and_refresh_keeps_it_off(client, off_payload):
    from catalog.models import ProductImage

    p = import_off_product(off_payload(code="5000000000421", product_name="Photo yogurt"))
    front = p.images.get(kind="front", is_active=True)
    staff = get_user_model().objects.create_superuser("boss2", "b2@example.com", "pw-long-123!")
    client.force_login(staff)
    url = reverse("staff:product-image-toggle", args=[p.pk, front.pk])
    assert client.post(url).status_code == 302
    p.refresh_from_db()
    front.refresh_from_db()
    assert front.hidden_by_staff and not front.is_active
    assert p.front_image_url == "" and quality.PHOTO_MISSING in p.quality_issues
    # The same photo comes back from Open Food Facts: it stays off.
    p = import_off_product(off_payload(code="5000000000421", product_name="Photo yogurt"))
    assert not ProductImage.objects.get(pk=front.pk).is_active
    client.post(url)  # show again
    assert ProductImage.objects.get(pk=front.pk).is_active
