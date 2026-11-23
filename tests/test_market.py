"""The market for alternatives is worked out from the browser, never asked
(myola/services/market.py)."""

from __future__ import annotations

import pytest
from django.core.cache import cache
from django.test import RequestFactory
from django.urls import reverse

from catalog.services.importer import import_off_product
from myola.services import market

pytestmark = pytest.mark.django_db


def request(tz="", lang=""):
    req = RequestFactory().get("/", HTTP_ACCEPT_LANGUAGE=lang)
    req.session = {}
    if tz:
        market.remember_timezone(req, tz)
    return req


@pytest.mark.parametrize(
    "tz, lang, expected",
    [
        ("Europe/London", "", "en:united-kingdom"),
        ("Europe/Bucharest", "", "en:romania"),
        ("Europe/London", "ro-RO,ro;q=0.9", "en:united-kingdom"),  # where the device is wins
        ("", "ro-RO,ro;q=0.9,en;q=0.8", "en:romania"),
        ("", "en-GB,en;q=0.9", "en:united-kingdom"),
        ("UTC", "fr,en;q=0.8", None),  # no country in either
        ("", "", None),
        ("Not/A_Zone", "", None),
    ],
)
def test_country_from_time_zone_then_language(tz, lang, expected):
    assert market.country_for(request(tz, lang)) == expected


def test_bad_time_zone_values_are_ignored():
    req = request()
    market.remember_timezone(req, "../../etc/passwd")
    assert market.SESSION_TZ not in req.session


def test_names_read_after_in():
    assert market.name("en:united-kingdom") == "the UK"
    assert market.name("en:romania") == "Romania"


def test_card_title_list_and_search_follow_the_detected_country(client, off_payload, monkeypatch):
    seen = []

    def fake(product, user=None, limit=3, *, country=None, client=None, by="foodo"):
        seen.append(country)
        return []

    monkeypatch.setattr("catalog.services.alternatives.alternatives", fake)
    cache.clear()
    product = import_off_product(off_payload())
    url = reverse("product-alternatives", args=[product.pk])
    uk = client.get(url, {"tz": "Europe/London"}, HTTP_HX_REQUEST="true").content.decode()
    ro = client.get(url, {"tz": "Europe/Bucharest"}, HTTP_HX_REQUEST="true").content.decode()
    again = client.get(url, HTTP_HX_REQUEST="true").content.decode()  # remembered
    assert "Better alternatives in the UK" in uk and "Better alternatives in Romania" in ro
    assert "Better alternatives in Romania" in again
    assert seen == ["en:united-kingdom", "en:romania", "en:romania"]


def test_unknown_country_names_none_and_searches_everywhere(client, off_payload, monkeypatch):
    seen = []
    monkeypatch.setattr(
        "catalog.services.alternatives.alternatives",
        lambda *a, **k: seen.append(k.get("country")) or [],
    )
    product = import_off_product(off_payload())
    html = client.get(
        reverse("product-alternatives", args=[product.pk]),
        HTTP_HX_REQUEST="true",
        HTTP_ACCEPT_LANGUAGE="en",
    ).content.decode()
    assert (
        '<h2 id="alt-h">Better alternatives</h2>' in html and " in " not in html.split("</h2>")[0]
    )
    assert seen == [None]
