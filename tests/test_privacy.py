"""Privacy: public legal pages, data download, deletion removes every personal record."""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from catalog.services.importer import import_off_product
from myola import models as m


def _fill(user, product):
    m.Profile.objects.create(user=user, goal="eat_healthier", restrictions=["vegan"])
    m.DiaryEntry.objects.create(
        user=user,
        date=dt.date(2026, 11, 19),
        meal="lunch",
        product=product,
        name="Yogurt",
        amount=Decimal(125),
        unit="g",
        kcal_100=Decimal(60),
    )
    m.WaterEntry.objects.create(user=user, date=dt.date(2026, 11, 19), ml=250)
    m.ActivityEntry.objects.create(
        user=user, date=dt.date(2026, 11, 19), kind="walking", minutes=30
    )
    m.WeightEntry.objects.create(user=user, date=dt.date(2026, 11, 19), kg=Decimal(70))
    m.ProductMark.objects.create(user=user, product=product, kind="favorite")
    m.ProductVisit.objects.create(user=user, product=product, code="123")
    m.Note.objects.create(user=user, text="Too sweet", product=product)
    m.Memory.objects.create(user=user, text="Likes plain yogurt")
    m.ChatMessage.objects.create(user=user, role="user", text="Is it vegan?")
    m.Notification.objects.create(user=user, kind="reminder", key="r:1", title="Your diary is open")


PERSONAL_MODELS = [
    m.Profile,
    m.DiaryEntry,
    m.WaterEntry,
    m.ActivityEntry,
    m.WeightEntry,
    m.ProductMark,
    m.ProductVisit,
    m.MyProduct,
    m.Note,
    m.Memory,
    m.ChatMessage,
    m.Notification,
]


@pytest.mark.django_db
@pytest.mark.parametrize("name", ["privacy", "terms"])
def test_legal_pages_are_public(client, name):
    html = client.get(reverse(name)).content.decode()
    assert "Not medical advice" in html or "No medical advice" in html


@pytest.mark.django_db
def test_privacy_names_health_consent_and_the_rights(client):
    html = client.get(reverse("privacy")).content.decode()
    assert "explicit consent" in html and "Download my data" in html and "ICO" in html


@pytest.mark.django_db
def test_download_contains_only_my_data(member_client, member, off_payload):
    product = import_off_product(off_payload())
    _fill(member, product)
    other = get_user_model().objects.create_user("other", "other@example.com", "x-password-123")
    m.Note.objects.create(user=other, text="SECRET OF OTHER")
    r = member_client.get(reverse("download-my-data"))
    assert r.status_code == 200 and "attachment" in r["Content-Disposition"]
    data = json.loads(r.content)
    assert data["account"]["email"] == "member@example.com"
    assert data["profile"]["restrictions"] == ["vegan"]
    for key in (
        "diary",
        "water",
        "activity",
        "weights",
        "product_marks",
        "product_visits",
        "notes",
        "memories",
        "chat_messages",
        "notifications",
    ):
        assert len(data[key]) == 1, key
    assert "SECRET OF OTHER" not in r.content.decode()


@pytest.mark.django_db
def test_download_needs_an_account(client):
    assert client.get(reverse("download-my-data")).status_code == 302


@pytest.mark.django_db
def test_deleting_the_account_deletes_every_personal_record(member_client, member, off_payload):
    product = import_off_product(off_payload())
    _fill(member, product)
    r = member_client.post(
        reverse("delete-account"), {"password": "a-long-test-password-1", "confirm": "DELETE"}
    )
    assert r.status_code == 302
    for model in PERSONAL_MODELS:
        assert not model.objects.exists(), model.__name__
    # The catalogue is not personal data and stays.
    assert type(product).objects.filter(pk=product.pk).exists()
