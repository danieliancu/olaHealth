"""Pieces built separately that must work together: score and alternatives on the product page,
Ask Foodo with the AI layer, staff link, maintenance banner, footer links."""

from __future__ import annotations

import pytest
from django.urls import reverse

from catalog.services.importer import import_off_product


@pytest.mark.django_db
def test_product_page_lazy_loads_score_and_alternatives(client, off_payload):
    p = import_off_product(off_payload())
    html = client.get(reverse("product", args=[p.pk])).content.decode()
    url = reverse("product-alternatives", args=[p.pk])
    assert f'hx-get="{url}"' in html and 'hx-trigger="revealed"' in html
    # Without htmx the same URL is a full page.
    page = client.get(url)
    assert page.status_code == 200 and "Foodo Score" in page.content.decode()


@pytest.mark.django_db
def test_alternatives_fragment_shows_the_score_or_why_not(client, off_payload, monkeypatch):
    monkeypatch.setattr("catalog.services.alternatives.alternatives", lambda *a, **k: [])
    graded = import_off_product(off_payload(nutriscore_grade="b", nova_group=3))
    r = client.get(reverse("product-alternatives", args=[graded.pk]), HTTP_HX_REQUEST="true")
    body = r.content.decode()
    assert r.status_code == 200 and "/100" in body and "<html" not in body
    bare = import_off_product(
        off_payload(code="3017620422003", nutriscore_grade="", nova_group=None, nutriscore={})
    )
    body = client.get(
        reverse("product-alternatives", args=[bare.pk]), HTTP_HX_REQUEST="true"
    ).content.decode()
    assert "/100" in body or "Not enough data" in body


@pytest.mark.django_db
def test_ask_foodo_uses_the_ai_layer_and_falls_back(member, monkeypatch):
    from myola.services import chat

    monkeypatch.setattr("ai.service.phrase", lambda *a, **k: "AI phrased answer")
    _, reply = chat.send(member, "personal", "What did I scan?")
    assert reply.text == "AI phrased answer"
    monkeypatch.setattr("ai.service.phrase", lambda *a, **k: None)
    _, reply = chat.send(member, "personal", "What did I scan?")
    assert reply.text and reply.text != "AI phrased answer"


@pytest.mark.django_db
def test_ai_never_receives_health_conditions(member, monkeypatch):
    from myola.models import Profile
    from myola.services import chat

    Profile.objects.create(user=member, conditions=["diabetes"], conditions_note="type 2")
    seen = {}

    def fake_phrase(user, facts, question, history):
        from ai.service import strip_private

        seen["facts"] = strip_private(facts)
        return None

    monkeypatch.setattr("ai.service.phrase", fake_phrase)
    chat.send(member, "personal", "What are my preferences?")
    text = repr(seen["facts"])
    assert "diabetes" not in text and "type 2" not in text and member.email not in text


@pytest.mark.django_db
def test_chat_is_rate_limited(member_client, settings):
    settings.RATE_LIMITS = {**settings.RATE_LIMITS, "chat": (1, 60)}
    url = reverse("chat-send")
    assert member_client.post(url, {"context": "personal", "text": "hi"}).status_code != 429
    assert member_client.post(url, {"context": "personal", "text": "hi"}).status_code == 429


@pytest.mark.django_db
def test_staff_link_only_for_staff(member_client, member):
    assert "/staff/" not in member_client.get(reverse("today")).content.decode()
    member.is_staff = True
    member.save()
    assert 'href="/staff/"' in member_client.get(reverse("today")).content.decode()


@pytest.mark.django_db
def test_maintenance_banner_and_footer_links(client):
    from staff.models import SiteSettings

    html = client.get(reverse("scan")).content.decode()
    for name in ("privacy", "terms", "api-docs", "sources"):
        assert f'href="{reverse(name)}"' in html, name
    s = SiteSettings.load()
    s.maintenance_message = "Short maintenance tonight at 22:00."
    s.save()
    assert "Short maintenance tonight" in client.get(reverse("scan")).content.decode()


@pytest.mark.django_db
def test_opening_an_alternative_is_not_a_scan(member_client, member, off_payload):
    from myola.models import ProductVisit

    p = import_off_product(off_payload())
    code = off_payload()["code"]
    r = member_client.get(reverse("lookup"), {"code": code, "via": "alternative"})
    assert r.status_code == 302 and r["Location"] == reverse("product", args=[p.pk])
    member_client.get(r["Location"])
    assert not ProductVisit.objects.filter(user=member).exclude(code="").exists()
    # A real scan still carries the code.
    r = member_client.get(reverse("lookup"), {"code": code})
    assert "scanned=" in r["Location"]
