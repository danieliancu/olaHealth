""" "My products" (myola.models.MyProduct): only the products a user scanned, opened on
purpose, saved or put in the diary; the catalogue stays searchable for everyone."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from catalog.services.importer import import_off_product
from myola.models import MyProduct, ProductVisit
from myola.services import chat, my_products
from myola.services.profiles import get_profile, local_today

HX = {"HTTP_HX_REQUEST": "true"}


@pytest.fixture
def three(db, off_payload):
    return [
        import_off_product(off_payload(code=code, product_name=name))
        for code, name in (("5000000000011", "Yogurt one"), ("5000000000028", "Yogurt two"),
                           ("5000000000035", "Yogurt three"))
    ]  # fmt: skip


def mine(user):
    return list(my_products.listing(user).values_list("product__name", "last_origin"))


def test_new_account_starts_empty_and_can_still_search(member_client, member, three):
    html = member_client.get(reverse("products")).content.decode()
    assert "My products" in html and "No products yet" in html
    assert reverse("scan") in html and f"{reverse('products')}?all=1" in html
    # The catalogue is still there, by search or by browsing.
    found = member_client.get(reverse("products"), {"q": "Yogurt"}).content.decode()
    assert found.count('class="product-card"') == 3
    assert member_client.get(reverse("products"), {"all": "1"}).status_code == 200
    # Search results and chat recommendations show products without adding them.
    chat.send(member, "personal", "Ce iaurt îmi recomanzi?")
    assert mine(member) == []


def test_visitors_keep_the_catalogue_scan_and_public_pages(client, three):
    html = client.get(reverse("products")).content.decode()
    assert "My products" not in html and html.count('class="product-card"') == 3
    assert client.get(reverse("product", args=[three[0].pk])).status_code == 200
    assert client.get(reverse("scan")).status_code == 200
    assert not MyProduct.objects.exists()


def test_open_scan_save_and_diary(member_client, member, three):
    one, two, three_ = three
    url = reverse("product", args=[one.pk])
    # A fragment of the page (htmx) is not the user opening it.
    member_client.get(url, **HX)
    assert mine(member) == []
    member_client.get(url)
    assert mine(member) == [("Yogurt one", "open")]
    # Scanning the same product: still one row, now a scan (the auto-opened page keeps it).
    member_client.get(f"{url}?scanned=5000000000011")
    row = MyProduct.objects.get(user=member, product=one)
    assert (row.last_origin, row.scanned, row.opened) == ("scan", True, True)
    member_client.post(reverse("product-mark", args=[two.pk, "favorite"]))
    day = local_today(get_profile(member)).isoformat()
    member_client.post(reverse("add-food", args=[three_.pk]),
                       {"amount": "100", "meal": "lunch", "date": day, "portion": "g"})  # fmt: skip
    assert mine(member) == [("Yogurt three", "eaten"), ("Yogurt two", "saved"),
                            ("Yogurt one", "scan")]  # fmt: skip
    html = member_client.get(reverse("products")).content.decode()
    assert html.count('class="product-card"') == 3
    assert html.index("Yogurt three") < html.index("Yogurt two") < html.index("Yogurt one")
    assert "Scanned" in html and "Saved" in html and "In your diary" in html
    # Another account sees none of it.
    other = get_user_model().objects.create_user("mp-other", password="a-long-password-55")
    c = Client()
    c.force_login(other)
    assert "No products yet" in c.get(reverse("products")).content.decode()
    assert mine(other) == []


def test_hidden_products_leave_the_list_and_clear_history_keeps_saved(member_client, member,
                                                                      three):  # fmt: skip
    one, two, _ = three
    member_client.get(reverse("product", args=[one.pk]))
    member_client.post(reverse("product-mark", args=[two.pk, "favorite"]))
    member_client.post(reverse("product-mark", args=[one.pk, "hidden"]))
    assert [n for n, _ in mine(member)] == ["Yogurt two"]
    member_client.post(reverse("saved-clear", args=["history"]))
    assert MyProduct.objects.filter(user=member).count() == 1  # the saved one stays


def test_touch_is_one_row_and_keeps_the_newest_interaction(member, three):
    p = three[0]
    now = timezone.now()
    my_products.touch(member, p, "saved", when=now)
    my_products.touch(member, p, "open", when=now - timedelta(days=1))  # an older event
    my_products.touch(member, p, "open", when=now - timedelta(days=1))  # and a retry
    row = MyProduct.objects.get(user=member, product=p)
    assert row.last_origin == "saved" and row.saved and row.opened
    assert row.first_at == now - timedelta(days=1) and row.last_at == now
    assert MyProduct.objects.filter(user=member).count() == 1


def test_off_import_and_sync_never_add_products(member, three, off_payload):
    my_products.touch(member, three[0], "open")
    before = list(MyProduct.objects.values_list("user_id", "product_id", "last_at"))
    import_off_product(off_payload(code="5000000000011", product_name="Yogurt one v2",
                                   last_modified_t=2_000_000_000))  # fmt: skip
    import_off_product(off_payload(code="5000000000042", product_name="Brand new"))
    assert list(MyProduct.objects.values_list("user_id", "product_id", "last_at")) == before


def test_backfill_from_earlier_activity_is_idempotent(member, three):
    import importlib

    ProductVisit.objects.create(user=member, product=three[0], code="5000000000011")
    ProductVisit.objects.create(user=member, product=three[1])
    backfill = importlib.import_module("myola.migrations.0018_my_products_backfill").BACKFILL
    for _ in range(2):
        with connection.cursor() as cur:
            cur.execute(backfill)
    rows = dict(MyProduct.objects.filter(user=member).values_list("product__name", "last_origin"))
    assert rows == {"Yogurt one": "scan", "Yogurt two": "open"}


def test_my_products_in_the_data_download(member_client, member, three):
    my_products.touch(member, three[0], "open")
    data = member_client.get(reverse("download-my-data")).json()
    assert [r["product__name"] for r in data["my_products"]] == ["Yogurt one"]
