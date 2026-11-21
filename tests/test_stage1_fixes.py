"""Regression tests for the bugs found in the critical review (stage 1)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.urls import reverse

from catalog.services.importer import import_off_product
from myola.models import DiaryEntry, Note, Profile
from myola.services.profiles import get_profile, local_today


@pytest.mark.django_db
@pytest.mark.parametrize("name", ["start", "progress"])
def test_back_button_restore_gets_the_full_page(member_client, name):
    """Back/Forward re-fetches with HX-Request + HX-History-Restore-Request: full page."""
    html = member_client.get(
        reverse(name), HTTP_HX_REQUEST="true", HTTP_HX_HISTORY_RESTORE_REQUEST="true"
    ).content.decode()
    assert "<html" in html and '<nav class="tabbar"' in html


@pytest.mark.django_db
def test_htmx_responses_vary_on_hx_request(member_client):
    r = member_client.get(reverse("progress"), HTTP_HX_REQUEST="true")
    assert "HX-Request" in r["Vary"]


@pytest.mark.django_db
def test_expired_session_on_htmx_becomes_a_full_page_redirect(client):
    r = client.get(reverse("today"), HTTP_HX_REQUEST="true")
    assert r.status_code == 204 and r["HX-Redirect"].startswith(reverse("account_login"))
    # Normal requests keep the plain redirect.
    assert client.get(reverse("today")).status_code == 302


@pytest.mark.django_db
def test_edit_preview_keeps_its_box(member_client, member):
    e = DiaryEntry.objects.create(
        user=member,
        date=local_today(get_profile(member)),
        meal="lunch",
        name="Soup",
        amount=Decimal(200),
        unit="g",
        kcal_100=Decimal(50),
    )
    html = member_client.get(
        reverse("today-entry", args=[e.pk]), HTTP_HX_REQUEST="true"
    ).content.decode()
    amount = html.split(f'id="id_e{e.pk.hex[:8]}-amount"')[0].rsplit("<input", 1)[1]
    amount += html.split(f'id="id_e{e.pk.hex[:8]}-amount"')[1].split(">", 1)[0]
    assert 'hx-swap="innerHTML"' in amount
    assert 'hx-disinherit="*"' in html


@pytest.mark.django_db
def test_tapping_today_wins_over_an_old_day(member_client, member, off_payload):
    p = import_off_product(off_payload(serving_size="125 g"))
    today = local_today(get_profile(member))
    old = today - dt.timedelta(days=60)  # outside the 5-week calendar
    url = reverse("add-food", args=[p.pk])
    html = member_client.get(url, {"date": old.isoformat()}).content.decode()
    assert 'name="date_fallback"' in html
    member_client.post(
        url,
        {
            "meal": "lunch",
            "date": today.isoformat(),
            "date_fallback": old.isoformat(),
            "amount": "125",
        },
    )
    assert DiaryEntry.objects.get().date == today
    # Untouched "Other day" keeps the old day.
    member_client.post(
        url,
        {"meal": "lunch", "date": "other", "date_fallback": old.isoformat(), "amount": "125"},
    )
    assert DiaryEntry.objects.filter(date=old).exists()


@pytest.mark.django_db
@pytest.mark.parametrize("tab", ["overview", "profile", "notes", "saved", "account"])
def test_one_chat_dialog_on_every_my_track_tab(member_client, tab):
    html = member_client.get(reverse("ask-foodo"), {"tab": tab}).content.decode()
    assert html.count('id="chat-dialog-personal"') == 1


@pytest.mark.django_db
def test_lookup_not_found_keeps_diary_mode(member_client, monkeypatch):
    from catalog.services import lookup as lookup_service

    class Result:
        status, error, message, code, detail = "not_found", "", "", None, ""
        product, stale, refresh_scheduled, retry_after, source = None, False, False, 0, ""

    monkeypatch.setattr("catalog.views.lookup", lambda *a, **k: Result())
    assert lookup_service  # module imported for the patch target
    html = member_client.get(
        reverse("lookup"), {"code": "5000000000001", "add": "dinner", "date": "2026-11-18"}
    ).content.decode()
    assert "Add it to Dinner with Quick add" in html
    assert "/scan/?add=dinner&amp;date=2026-11-18" in html


@pytest.mark.django_db
def test_product_note_at_the_limit_is_refused_not_rotated(
    member_client, member, off_payload, settings
):
    from myola import models

    p = import_off_product(off_payload())
    monkey_limit = 2
    old = dict(models.LIMITS)
    models.LIMITS["notes"] = monkey_limit
    try:
        Note.objects.create(user=member, text="first")
        Note.objects.create(user=member, text="second")
        member_client.post(reverse("product-note", args=[p.pk]), {"text": "third"})
        assert sorted(Note.objects.values_list("text", flat=True)) == ["first", "second"]
    finally:
        models.LIMITS.clear()
        models.LIMITS.update(old)


@pytest.mark.django_db
def test_calories_follow_one_rule(member_client, member):
    profile = Profile.objects.create(user=member, show_energy=True, conditions=["eating_disorder"])
    assert not profile.energy_visible
    html = member_client.get(reverse("settings-edit")).content.decode()
    assert "Calories stay hidden everywhere" in html
    field = html.split('name="show_energy"')[1].split(">")[0]
    assert "disabled" in field
    my = member_client.get(reverse("ask-foodo"), {"tab": "profile"}).content.decode()
    assert "Calories hidden" in my


@pytest.mark.django_db
def test_alternatives_sign_in_returns_to_the_product(client, off_payload, monkeypatch):
    monkeypatch.setattr(
        "catalog.services.alternatives.alternatives",
        lambda *a, **k: [{"url": "/x/", "image": "", "name": "N", "brand": "B", "score": 90}],
    )
    p = import_off_product(off_payload(nutriscore_grade="c", nova_group=3))
    html = client.get(
        reverse("product-alternatives", args=[p.pk]), HTTP_HX_REQUEST="true"
    ).content.decode()
    assert f"next={reverse('product', args=[p.pk])}%23better" in html
