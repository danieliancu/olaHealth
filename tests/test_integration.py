"""Pieces built separately that must work together: score and alternatives on the product page,
Ask Foodo with the AI layer, staff link, maintenance banner, footer links."""

from __future__ import annotations

import pytest
from django.urls import reverse
from django.utils import timezone

from catalog.services.importer import import_off_product


@pytest.mark.django_db
def test_product_page_lazy_loads_score_and_alternatives(client, off_payload):
    p = import_off_product(off_payload())
    html = client.get(reverse("product", args=[p.pk])).content.decode()
    url = reverse("product-alternatives", args=[p.pk])
    assert f'hx-get="{url}"' in html and 'hx-trigger="revealed"' in html
    # Without htmx the same URL is a full page.
    page = client.get(url)
    assert page.status_code == 200 and "Better alternatives" in page.content.decode()


@pytest.mark.django_db
def test_alternatives_card_holds_only_title_products_and_scores(client, off_payload, monkeypatch):
    item = {"url": "/x/", "image": "", "name": "Plain yogurt", "brand": "B", "grade": "a"}
    monkeypatch.setattr(
        "catalog.services.alternatives.alternatives", lambda *a, **k: [{**item, "foodo": 85}]
    )
    graded = import_off_product(off_payload(nova_group=3))
    r = client.get(
        reverse("product-alternatives", args=[graded.pk]),
        HTTP_HX_REQUEST="true",
        HTTP_ACCEPT_LANGUAGE="en-GB,en;q=0.9",
    )
    body = r.content.decode()
    assert r.status_code == 200 and "<html" not in body
    assert "Better alternatives in the UK" in body and "Plain yogurt" in body
    assert ">85</span>" in body and "/100" not in body and 'title="Nutri-Score"' not in body
    assert "Sign in" not in body and "<select" not in body
    monkeypatch.setattr("catalog.services.alternatives.alternatives", lambda *a, **k: [])
    body = client.get(
        reverse("product-alternatives", args=[graded.pk]), HTTP_HX_REQUEST="true"
    ).content.decode()
    assert "found a better alternative yet." in body


@pytest.mark.django_db
def test_ask_foodo_uses_the_ai_only_for_product_label_questions(member, off_payload, monkeypatch):
    from ai.service import AIResult
    from myola.services import chat

    p = import_off_product(off_payload())
    monkeypatch.setattr("ai.service.ask", lambda *a, **k: AIResult(text="AI phrased answer"))
    _, reply = chat.send(member, f"product:{p.pk}", "How much sugar?", product=p)
    assert reply.text == "AI phrased answer"
    # Personal questions and allergen/diet checks never go to the AI.
    _, reply = chat.send(member, "personal", "What did I scan?")
    assert reply.text != "AI phrased answer"
    _, reply = chat.send(member, f"product:{p.pk}", "Is it okay for my allergies?", product=p)
    assert reply.text != "AI phrased answer"
    # Any AI problem falls back to the rule-based answer.
    monkeypatch.setattr("ai.service.ask", lambda *a, **k: AIResult())
    _, reply = chat.send(member, f"product:{p.pk}", "How much sugar?", product=p)
    assert reply.text and reply.text != "AI phrased answer"


def _capture(monkeypatch):
    seen = {}

    from ai.service import AIResult

    def fake_ask(user, facts, question, history, **kwargs):
        seen.update(facts=facts, history=history)
        return AIResult()

    monkeypatch.setattr("ai.service.ask", fake_ask)
    return seen


@pytest.mark.django_db
def test_ai_gets_only_the_allow_list(member, off_payload, monkeypatch):
    from myola.models import Memory, Note, Profile
    from myola.services import chat

    p = import_off_product(off_payload())
    Profile.objects.create(
        user=member,
        conditions=["diabetes"],
        conditions_note="type 2",
        restrictions=["nuts"],
        religious_note="Halal",
        use_notes=True,
    )
    Note.objects.create(user=member, text="SECRET NOTE about sugar", product=p)
    Memory.objects.create(user=member, text="SECRET MEMORY", confirmed_at=timezone.now())
    seen = _capture(monkeypatch)
    chat.send(member, f"product:{p.pk}", "How much sugar?", product=p)
    text = repr(seen["facts"])
    for secret in (
        "diabetes",
        "type 2",
        "Halal",
        "SECRET NOTE",
        "SECRET MEMORY",
        member.email,
        "Peanuts",
        "allerg",
    ):
        assert secret not in text, secret
    assert seen["history"] == []
    assert set(seen["facts"]) <= {"product", "foodo_score"}


@pytest.mark.django_db
def test_ai_gets_diets_and_allergies_only_with_the_opt_in(member, off_payload, monkeypatch):
    from myola.models import Profile
    from myola.services import chat

    p = import_off_product(off_payload())
    Profile.objects.create(user=member, restrictions=["nuts"], ai_use_preferences=True)
    seen = _capture(monkeypatch)
    chat.send(member, f"product:{p.pk}", "How much sugar?", product=p)
    assert "Peanuts" in repr(seen["facts"]["allergies"])


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
