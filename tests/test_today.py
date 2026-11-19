"""Today: food diary, daily summary, water, activity and the “how much?” step (server-rendered)."""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from catalog.services.importer import import_off_product
from myola.models import ActivityEntry, DiaryEntry, ProductVisit, WaterEntry
from myola.services import diary
from myola.services.profiles import get_profile, local_today

HX = {"HTTP_HX_REQUEST": "true"}


def make_entry(user, day=None, **fields) -> DiaryEntry:
    values = {
        "date": day or local_today(get_profile(user)),
        "meal": "lunch",
        "name": "Yogurt",
        "amount": Decimal("200"),
        "unit": "g",
        "kcal_100": Decimal("97"),
        "protein_100": Decimal("9.2"),
        "carbs_100": Decimal("3.6"),
        "fat_100": Decimal("5"),
        **fields,
    }
    return DiaryEntry.objects.create(user=user, **values)


@pytest.fixture
def other_user(db):
    return get_user_model().objects.create_user(
        username="other", email="other@example.com", password="a-long-test-password-2"
    )


# ---------- services/diary.py ----------
def test_entry_values_scale_per_100():
    e = DiaryEntry(amount=Decimal("150"), basis_known=True, kcal_100=Decimal("97"))
    e.protein_100, e.carbs_100, e.fat_100 = Decimal("9.2"), None, Decimal("5")
    v = diary.entry_values(e)
    assert v["energy-kcal"] == Decimal("145.5")
    assert v["proteins"] == Decimal("13.8")
    assert v["carbohydrates"] is None
    assert v["fat"] == Decimal("7.5")


def test_unknown_basis_has_no_values():
    e = DiaryEntry(amount=Decimal("100"), basis_known=False, kcal_100=Decimal("97"))
    assert set(diary.entry_values(e).values()) == {None}


def test_totals_are_partial_when_values_are_missing():
    known = DiaryEntry(amount=Decimal("100"), basis_known=True, kcal_100=Decimal("97"))
    unknown = DiaryEntry(amount=Decimal("100"), basis_known=False, kcal_100=Decimal("50"))
    t = diary.totals([known, unknown])
    assert t.count == 2
    assert t.values["energy-kcal"] == Decimal("97")
    assert t.partial["energy-kcal"] and t.any_partial
    rows = diary.target_rows(t, {"energy-kcal": 194}, show_energy=True)
    energy = rows[0]
    assert energy["pct"] == 50 and energy["partial"] and energy["display"] == "97"
    assert diary.target_rows(t, {}, show_energy=False)[0]["key"] == "proteins"


@pytest.mark.parametrize(
    ("hour", "minute", "meal"),
    [
        (0, 0, "breakfast"),
        (10, 59, "breakfast"),
        (11, 0, "lunch"),
        (15, 59, "lunch"),
        (16, 0, "dinner"),
        (21, 29, "dinner"),
        (21, 30, "snacks"),
        (23, 59, "snacks"),
    ],
)
def test_default_meal_boundaries(hour, minute, meal):
    assert diary.default_meal(dt.datetime(2026, 11, 19, hour, minute)) == meal


def test_preview_matches_a_saved_entry():
    per100 = {"energy-kcal": Decimal("97"), "proteins": Decimal("9.2")}
    v = diary.preview(per100, Decimal("125"), True)
    assert v["energy-kcal"] == Decimal("121.3") and v["fat"] is None
    assert set(diary.preview(per100, None, True).values()) == {None}
    assert set(diary.preview(per100, Decimal("125"), False).values()) == {None}
    pv = diary.preview_rows(per100, Decimal("12.5"), True, show_energy=False)
    assert [r["key"] for r in pv["rows"]] == ["proteins", "carbohydrates", "fat"]
    assert pv["amount_display"] == "12.5" and pv["missing"] and pv["any_known"]


def test_fmt_and_water_display():
    assert diary.fmt(Decimal("121.3")) == "121"
    assert diary.fmt(Decimal("3.66")) == "3.7"
    assert diary.fmt(None) == "–"
    assert diary.water_display(750) == "750 ml"
    assert diary.water_display(1500) == "1.5 l"


@pytest.mark.django_db
def test_week_never_goes_past_today(member):
    today = dt.date(2026, 11, 19)  # a Thursday
    make_entry(member, day=dt.date(2026, 11, 17))
    w = diary.week(member, today, today)
    assert w["days"][0]["iso"] == "2026-11-16"
    assert [d["future"] for d in w["days"]] == [False] * 4 + [True] * 3
    assert [d["logged"] for d in w["days"]][1] is True
    assert w["next"] is None and w["prev"] == "2026-11-12"
    earlier = diary.week(member, dt.date(2026, 11, 5), today)
    assert earlier["next"] == "2026-11-12"


@pytest.mark.django_db
def test_undo_restores_the_same_record(member):
    e = make_entry(member, portion="1 serving (125 g)")
    data = diary.undo_data("entry", e)
    pk = e.pk
    e.delete()
    restored = diary.restore(member, data)
    assert restored.pk == pk and restored.portion == "1 serving (125 g)"
    assert DiaryEntry.objects.get(pk=pk).kcal_100 == Decimal("97")
    assert diary.restore(member, data) is None  # never twice


# ---------- The Today page ----------
@pytest.mark.django_db
def test_today_needs_an_account(client):
    r = client.get(reverse("today"))
    assert r.status_code == 302


@pytest.mark.django_db
def test_today_page_renders_the_day(member_client, member):
    make_entry(member)
    make_entry(member, meal="snacks", name="Mystery bar", basis_known=False)
    r = member_client.get(reverse("today"))
    html = r.content.decode()
    assert r.status_code == 200
    assert "<h1>Today</h1>" in html
    assert "Yogurt" in html and "Mystery bar" in html
    assert "194</strong>" not in html  # values are formatted with a unit
    assert "194<small> kcal</small>" in html  # 200 g × 97 kcal
    assert 'class="partial"' in html  # the bar has unknown values
    assert "Some foods have unknown values" in html
    # Diary target links for each meal
    today = local_today(get_profile(member)).isoformat()
    assert f"/scan/?add=lunch&amp;date={today}" in html
    assert f"/products/?add=dinner&amp;date={today}" in html
    # The setup notice (not onboarded) and the day strip
    assert reverse("start") in html and "Set up your goal" in html
    assert 'aria-current="date"' in html
    # No inline code and no style attributes (strict CSP)
    assert "<script>" not in html.split("</head>")[1]
    assert not re.search(r"\sstyle=", html)
    assert "hx-on" not in html


@pytest.mark.django_db
def test_today_with_a_past_date_and_a_future_one(member_client, member):
    today = local_today(get_profile(member))
    yesterday = today - dt.timedelta(days=1)
    make_entry(member, day=yesterday, name="Old soup")
    html = member_client.get(reverse("today"), {"date": yesterday.isoformat()}).content.decode()
    assert "<h1>Yesterday</h1>" in html and "Old soup" in html
    future = (today + dt.timedelta(days=3)).isoformat()
    html = member_client.get(reverse("today"), {"date": future}).content.decode()
    assert "<h1>Today</h1>" in html and "Old soup" not in html
    html = member_client.get(reverse("today"), {"date": "nonsense"}).content.decode()
    assert "<h1>Today</h1>" in html


@pytest.mark.django_db
def test_energy_is_hidden_with_an_eating_disorder_or_when_switched_off(member_client, member):
    make_entry(member)
    profile = get_profile(member)
    profile.conditions = ["eating_disorder"]
    profile.save()
    html = member_client.get(reverse("today")).content.decode()
    assert "kcal" not in html.split('id="today"')[1]
    profile.conditions, profile.show_energy = [], False
    profile.save()
    html = member_client.get(reverse("today")).content.decode()
    assert "<small> kcal</small>" not in html


@pytest.mark.django_db
def test_targets_show_a_meter_unless_switched_off(member_client, member):
    make_entry(member)
    profile = get_profile(member)
    profile.target_kcal = 2000
    profile.onboarded = True
    profile.save()
    html = member_client.get(reverse("today")).content.decode()
    assert 'class="meter"' in html and 'width="9"' in html  # 194 of 2000 kcal
    assert "of 2000 kcal" in html
    assert "Set up your goal" not in html
    profile.show_targets = False
    profile.save()
    html = member_client.get(reverse("today")).content.decode()
    assert 'class="meter"' not in html


@pytest.mark.django_db
def test_htmx_gets_only_the_day_body(member_client):
    r = member_client.get(reverse("today"), **HX)
    html = r.content.decode()
    assert html.lstrip().startswith('<div class="today" id="today">')
    assert "<html" not in html


# ---------- Quick add, edit, move, delete and undo ----------
@pytest.mark.django_db
def test_quick_add_a_food(member_client, member):
    today = local_today(get_profile(member)).isoformat()
    r = member_client.post(
        reverse("today-entry-add"),
        {
            "quick": "dinner",
            "dinner-name": "Home soup",
            "dinner-amount": "300",
            "dinner-unit": "ml",
            "dinner-kcal_100": "40",
            "dinner-basis_known": "on",
            "dinner-date": today,
            "dinner-meal": "dinner",
        },
    )
    assert r.status_code == 302 and r["Location"] == f"{reverse('today')}?date={today}"
    e = DiaryEntry.objects.get(user=member)
    assert (e.name, e.meal, e.unit, e.amount) == ("Home soup", "dinner", "ml", Decimal("300"))
    assert e.kcal_100 == Decimal("40") and e.protein_100 is None and e.product is None
    html = member_client.get(r["Location"]).content.decode()
    assert "Added to Dinner." in html and "Home soup" in html


@pytest.mark.django_db
def test_quick_add_errors_reopen_the_form(member_client, member):
    today = local_today(get_profile(member)).isoformat()
    r = member_client.post(
        reverse("today-entry-add"),
        {"quick": "lunch", "lunch-name": "", "lunch-amount": "0", "lunch-date": today},
        **HX,
    )
    html = r.content.decode()
    assert r.status_code == 200 and not DiaryEntry.objects.exists()
    assert '<details class="quick-add" open>' in html and "field-error" in html


@pytest.mark.django_db
def test_edit_an_entry_inline(member_client, member):
    e = make_entry(member, portion="1 serving (125 g)")
    url = reverse("today-entry", args=[e.pk])
    html = member_client.get(url, **HX).content.decode()
    assert '<form class="entry-edit"' in html and 'name="e' in html
    assert 'hx-trigger="input changed delay:200ms"' in html
    # Without htmx: the day page with the form open.
    r = member_client.get(url)
    assert r.status_code == 302 and f"edit={e.pk}" in r["Location"]
    assert '<form class="entry-edit"' in member_client.get(r["Location"]).content.decode()
    p = f"e{e.pk.hex[:8]}"
    r = member_client.post(
        url,
        {f"{p}-amount": "150", f"{p}-meal": "dinner", f"{p}-date": e.date.isoformat()},
        **HX,
    )
    html = r.content.decode()
    e.refresh_from_db()
    assert (e.amount, e.meal, e.portion, e.unit) == (Decimal("150"), "dinner", "", "g")
    assert "Entry updated." in html and 'class="toast today-toast"' in html


@pytest.mark.django_db
def test_move_an_entry_to_another_day(member_client, member):
    e = make_entry(member)
    yesterday = e.date - dt.timedelta(days=1)
    p = f"e{e.pk.hex[:8]}"
    r = member_client.post(
        reverse("today-entry", args=[e.pk]),
        {f"{p}-amount": "200", f"{p}-meal": "lunch", f"{p}-date": yesterday.isoformat()},
    )
    assert r.status_code == 302
    e.refresh_from_db()
    assert e.date == yesterday
    # Never into the future
    tomorrow = (e.date + dt.timedelta(days=5)).isoformat()
    r = member_client.post(
        reverse("today-entry", args=[e.pk]),
        {f"{p}-amount": "200", f"{p}-meal": "lunch", f"{p}-date": tomorrow},
    )
    e.refresh_from_db()
    assert r.status_code == 200 and e.date == yesterday
    assert "Choose today or an earlier day." in r.content.decode()


@pytest.mark.django_db
def test_entry_preview(member_client, member):
    e = make_entry(member)
    p = f"e{e.pk.hex[:8]}"
    html = member_client.get(
        reverse("today-entry-preview", args=[e.pk]), {f"{p}-amount": "50"}
    ).content.decode()
    assert "49 kcal" in html and "For 50 g" in html
    html = member_client.get(reverse("today-entry-preview", args=[e.pk])).content.decode()
    assert "Enter how much you had" in html


@pytest.mark.django_db
def test_delete_and_undo(member_client, member):
    e = make_entry(member)
    pk = e.pk
    r = member_client.post(reverse("today-entry-delete", args=[pk]), **HX)
    html = r.content.decode()
    assert not DiaryEntry.objects.filter(pk=pk).exists()
    assert "Yogurt deleted." in html and 'name="token"' in html
    assert "today-toast" in html
    r = member_client.post(reverse("today-undo"), {"token": str(pk)}, **HX)
    assert DiaryEntry.objects.filter(pk=pk, user=member).exists()
    assert "Undone." in r.content.decode()
    # Only once
    r = member_client.post(reverse("today-undo"), {"token": str(pk)}, **HX)
    assert "There is nothing to undo." in r.content.decode()
    assert DiaryEntry.objects.filter(user=member).count() == 1


@pytest.mark.django_db
def test_delete_without_htmx_shows_undo_after_the_redirect(member_client, member):
    e = make_entry(member)
    r = member_client.post(reverse("today-entry-delete", args=[e.pk]))
    assert r.status_code == 302
    html = member_client.get(r["Location"]).content.decode()
    assert "Yogurt deleted." in html and reverse("today-undo") in html
    # The notice is shown once.
    assert "Yogurt deleted." not in member_client.get(r["Location"]).content.decode()


@pytest.mark.django_db
def test_entries_of_another_user_are_not_found(member_client, other_user):
    e = make_entry(other_user)
    w = WaterEntry.objects.create(user=other_user, date=e.date, ml=250)
    a = ActivityEntry.objects.create(user=other_user, date=e.date, kind="gym", minutes=30)
    assert member_client.get(reverse("today-entry", args=[e.pk])).status_code == 404
    assert member_client.post(reverse("today-entry", args=[e.pk]), {}).status_code == 404
    assert member_client.post(reverse("today-entry-delete", args=[e.pk])).status_code == 404
    assert member_client.get(reverse("today-entry-preview", args=[e.pk])).status_code == 404
    assert member_client.post(reverse("today-water-delete", args=[w.pk])).status_code == 404
    assert member_client.post(reverse("today-activity-delete", args=[a.pk])).status_code == 404
    assert DiaryEntry.objects.filter(pk=e.pk).exists()
    # Not on the day page either, even with ?edit=
    html = member_client.get(reverse("today"), {"edit": str(e.pk)}).content.decode()
    assert "Yogurt" not in html


# ---------- Water and activity ----------
@pytest.mark.django_db
def test_water_quick_add_custom_and_delete(member_client, member):
    today = local_today(get_profile(member)).isoformat()
    r = member_client.post(reverse("today-water-add"), {"ml": "500", "date": today})
    assert r.status_code == 302
    r = member_client.post(reverse("today-water-add"), {"ml": "750", "date": today}, **HX)
    html = r.content.decode()
    assert "750 ml of water added." in html and "1.3 l" in html
    r = member_client.post(reverse("today-water-add"), {"ml": "6000", "date": today}, **HX)
    assert WaterEntry.objects.filter(user=member).count() == 2
    assert "field-error" in r.content.decode()
    w = WaterEntry.objects.filter(user=member).first()
    html = member_client.post(reverse("today-water-delete", args=[w.pk]), **HX).content.decode()
    assert WaterEntry.objects.filter(user=member).count() == 1
    assert "500 ml removed." in html and 'name="token"' in html


@pytest.mark.django_db
def test_activity_add_and_delete(member_client, member):
    today = local_today(get_profile(member)).isoformat()
    html = member_client.post(
        reverse("today-activity-add"), {"kind": "cycling", "minutes": "45", "date": today}, **HX
    ).content.decode()
    assert "Cycling, 45 min added." in html and "45 min</strong>" in html
    r = member_client.post(
        reverse("today-activity-add"), {"kind": "flying", "minutes": "45", "date": today}, **HX
    )
    assert ActivityEntry.objects.filter(user=member).count() == 1
    assert "field-error" in r.content.decode()
    a = ActivityEntry.objects.get(user=member)
    member_client.post(reverse("today-activity-delete", args=[a.pk]))
    assert not ActivityEntry.objects.exists()


@pytest.mark.django_db
def test_limits_are_respected(member_client, member, monkeypatch):
    monkeypatch.setitem(diary.LIMITS, "water", 1)
    today = local_today(get_profile(member)).isoformat()
    member_client.post(reverse("today-water-add"), {"ml": "250", "date": today})
    html = member_client.post(
        reverse("today-water-add"), {"ml": "250", "date": today}, **HX
    ).content.decode()
    assert WaterEntry.objects.filter(user=member).count() == 1
    assert "Your water log is full" in html


# ---------- “How much?” for a catalogue product ----------
@pytest.mark.django_db
def test_add_food_page(member_client, off_payload):
    p = import_off_product(off_payload(serving_size="125 g"))
    r = member_client.get(reverse("add-food", args=[p.pk]), {"meal": "lunch", "date": "2026-11-18"})
    html = r.content.decode()
    assert r.status_code == 200
    assert 'name="meal" value="lunch" checked' in html and "How much did you have?" in html
    assert "1 serving (125 g)" in html and "portion=serving" in html
    assert reverse("add-food-preview", args=[p.pk]) in html
    assert 'hx-trigger="input changed delay:200ms"' in html
    assert reverse("product", args=[p.pk]) in html  # full details stay one tap away
    assert "<script" not in html.split("</head>")[1]
    assert not re.search(r"\sstyle=", html)


@pytest.mark.django_db
def test_add_food_portion_chip_fills_the_amount(member_client, off_payload):
    p = import_off_product(off_payload(serving_size="125 g"))
    html = member_client.get(
        reverse("add-food", args=[p.pk]), {"meal": "lunch", "portion": "serving"}
    ).content.decode()
    assert 'value="125"' in html and 'aria-current="true"' in html
    assert "121 kcal" in html  # preview for 125 g


@pytest.mark.django_db
def test_add_food_preview(member_client, off_payload):
    p = import_off_product(off_payload())
    url = reverse("add-food-preview", args=[p.pk])
    html = member_client.get(url, {"amount": "200"}).content.decode()
    assert "194 kcal" in html and "18 g" in html and "For 200 g" in html
    assert "Enter how much" in member_client.get(url, {"amount": "abc"}).content.decode()


@pytest.mark.django_db
def test_add_food_default_meal_in_the_users_timezone(member_client, off_payload, monkeypatch):
    p = import_off_product(off_payload())
    monkeypatch.setattr("myola.views_today.diary.default_meal", lambda now=None: "dinner")
    html = member_client.get(reverse("add-food", args=[p.pk]), {"meal": "auto"}).content.decode()
    assert 'name="meal" value="dinner" checked' in html


@pytest.mark.django_db
def test_add_food_saves_a_snapshot_and_the_scan(member_client, member, off_payload):
    p = import_off_product(off_payload(serving_size="125 g"))
    day = local_today(get_profile(member)).isoformat()
    r = member_client.post(
        reverse("add-food", args=[p.pk]),
        {
            "amount": "125",
            "meal": "lunch",
            "date": day,
            "portion": "serving",
            "scanned": "5941234567899",
        },
    )
    assert r.status_code == 302
    assert r["Location"] == f"{reverse('today')}?date={day}&added=lunch"
    e = DiaryEntry.objects.get(user=member)
    assert (e.product_id, e.meal, e.unit, e.basis_known) == (p.pk, "lunch", "g", True)
    assert e.kcal_100 == Decimal("97") and e.fat_100 == Decimal("5")
    assert e.portion == "1 serving (125 g)" and e.name == "Greek style yogurt"
    visit = ProductVisit.objects.get(user=member)
    assert visit.code == "5941234567899" and visit.product_id == p.pk
    html = member_client.get(r["Location"]).content.decode()
    assert "Added to Lunch." in html and "Greek style yogurt" in html


@pytest.mark.django_db
def test_add_food_rejects_bad_input(member_client, member, off_payload):
    p = import_off_product(off_payload())
    tomorrow = (local_today(get_profile(member)) + dt.timedelta(days=1)).isoformat()
    r = member_client.post(
        reverse("add-food", args=[p.pk]), {"amount": "0", "meal": "brunch", "date": tomorrow}
    )
    html = r.content.decode()
    assert r.status_code == 200 and not DiaryEntry.objects.exists()
    assert html.count('class="field-error"') >= 3


@pytest.mark.django_db
def test_add_food_warns_about_unconfirmed_restrictions(member_client, member, off_payload):
    profile = get_profile(member)
    profile.restrictions = ["vegan"]
    profile.save()
    p = import_off_product(off_payload())
    html = member_client.get(reverse("add-food", args=[p.pk])).content.decode()
    assert 'class="rs rs-' in html and "Check the physical label." in html


@pytest.mark.django_db
def test_add_food_without_a_basis_asks_for_the_unit(member_client, member, off_payload):
    p = import_off_product(off_payload(nutriments={}))
    html = member_client.get(reverse("add-food", args=[p.pk]), {"amount": "50"}).content.decode()
    assert '<select name="unit"' in html
    day = local_today(get_profile(member)).isoformat()
    r = member_client.post(
        reverse("add-food", args=[p.pk]), {"amount": "50", "meal": "snacks", "date": day}
    )
    assert r.status_code == 200 and not DiaryEntry.objects.exists()
    member_client.post(
        reverse("add-food", args=[p.pk]),
        {"amount": "50", "unit": "ml", "meal": "snacks", "date": day},
    )
    e = DiaryEntry.objects.get(user=member)
    assert (e.unit, e.basis_known, e.kcal_100) == ("ml", False, None)


@pytest.mark.django_db
def test_scanner_close_returns_to_today(client):
    from myola import diary_target

    target = diary_target.from_params({"add": "lunch", "date": "2026-11-18"})
    assert diary_target.today_url(target) == f"{reverse('today')}?date=2026-11-18"


@pytest.mark.django_db
def test_add_food_day_chips_and_other_day(member_client, member, off_payload):
    import datetime as dt

    from myola.models import DiaryEntry
    from myola.services.profiles import get_profile, local_today

    p = import_off_product(off_payload(serving_size="125 g"))
    url = reverse("add-food", args=[p.pk])
    html = member_client.get(url).content.decode()
    assert "When did you have it?" in html and "Yesterday" in html and "Other day" in html
    assert 'class="cal-grid"' in html and 'type="date"' not in html  # no native date picker
    # "Other day" without picking a day in the calendar is refused, nothing is saved.
    r = member_client.post(url, {"meal": "lunch", "date": "other", "amount": "125"})
    assert r.status_code == 200 and not DiaryEntry.objects.exists()
    earlier = local_today(get_profile(member)) - dt.timedelta(days=5)
    r = member_client.post(
        url,
        {"meal": "lunch", "date": earlier.isoformat(), "amount": "125"},
    )
    assert r.status_code == 302
    assert DiaryEntry.objects.get().date == earlier
