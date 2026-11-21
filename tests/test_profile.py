"""Onboarding, profile, settings and progress: server-rendered pages and their forms."""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal

import pytest
from django.urls import reverse

from myola.models import ActivityEntry, DiaryEntry, Profile, WaterEntry, WeightEntry
from myola.services.profiles import derive_prefs, get_profile, local_today

pytestmark = pytest.mark.django_db

HX = {"HTTP_HX_REQUEST": "true"}


def profile_of(user) -> Profile:
    return Profile.objects.get(user=user)


def start_post(client, step, **data):
    return client.post(reverse("start"), {"step": step, **data})


# ---------- derive_prefs ----------
def test_derive_prefs_maps_restrictions_to_diets_and_allergens():
    p = Profile(restrictions=["lactose", "vegan", "nuts", "seafood", "religious", "other"])
    prefs = derive_prefs(p)
    assert prefs["diets"] == ["vegan", "lactose_free"]
    # No subtype chosen: every type is checked.
    assert prefs["allergens"] == [
        "en:crustaceans",
        "en:fish",
        "en:peanuts",
        "en:nuts",
        "en:molluscs",
    ]
    assert "en:milk" not in prefs["allergens"]  # lactose intolerance is not a milk allergy


def test_derive_prefs_uses_chosen_subtypes_and_ignores_notes():
    p = Profile(
        restrictions=["nuts", "seafood", "religious", "other"],
        nut_types=["en:peanuts"],
        seafood_types=["en:molluscs"],
        religious_note="No pork, halal only",
        other_note="no mushrooms",
    )
    assert derive_prefs(p) == {"goals": [], "diets": [], "allergens": ["en:peanuts", "en:molluscs"]}
    assert derive_prefs(Profile(restrictions=["religious", "other"]))["diets"] == []
    assert derive_prefs(None) == {"goals": [], "diets": [], "allergens": []}


# ---------- Login ----------
@pytest.mark.parametrize(
    "name", ["start", "progress", "profile-edit", "settings-edit", "progress-weight"]
)
def test_pages_need_an_account(client, name):
    r = client.get(reverse(name))
    assert r.status_code == 302 and r["Location"].startswith(reverse("account_login"))


# ---------- Onboarding ----------
def test_onboarding_shows_one_step_and_a_progress_indicator(member_client):
    html = member_client.get(reverse("start"), {"step": 2}).content.decode()
    assert "Step 2 of 4" in html and 'aria-current="step"' in html
    forms = re.findall(r'<form class="ob-step"[^>]*>', html)
    assert len(forms) == 4
    assert [("hidden" in f) for f in forms] == [True, False, True, True]
    assert 'href="/start/?step=3"' in html  # skip
    assert 'href="/start/?step=1"' in html  # back
    assert "<script>" not in html and 'style="' not in html


def test_onboarding_goal_is_required_and_saved(member_client, member):
    r = start_post(member_client, 1)
    assert r.status_code == 200 and "Choose one goal to continue." in r.content.decode()
    r = start_post(member_client, 1, goal="eat_healthier")
    assert r.status_code == 302 and r["Location"] == "/start/?step=2"
    assert profile_of(member).goal == "eat_healthier"


def test_onboarding_about_you_validates_ranges(member_client, member):
    r = start_post(member_client, 2, gender="non_binary", age="7", height="180", weight="")
    html = r.content.decode()
    assert r.status_code == 200 and "Enter an age between 13 and 120" in html
    assert 'aria-invalid="true"' in html
    assert profile_of(member).age is None
    r = start_post(member_client, 2, gender="non_binary", age="48", height="180,5", weight="")
    assert r["Location"] == "/start/?step=3"
    p = profile_of(member)
    assert (p.gender, p.age, p.height, p.weight) == ("non_binary", 48, Decimal("180.5"), None)
    # Saved values show again; placeholders stay examples.
    html = member_client.get(reverse("start"), {"step": 2}).content.decode()
    assert 'value="48"' in html and 'placeholder="100"' in html


def test_onboarding_restrictions_step(member_client, member):
    r = start_post(
        member_client,
        3,
        restrictions=["vegan", "nuts", "religious"],
        nut_types=["en:peanuts"],
        seafood_types=["en:fish"],  # seafood not chosen: dropped
        religious_note="Halal",
        other_note="ignored",
        consent="1",
    )
    assert r["Location"] == "/start/?step=4"
    p = profile_of(member)
    assert p.restrictions == ["vegan", "nuts", "religious"]
    assert p.nut_types == ["en:peanuts"] and p.seafood_types == []
    assert p.religious_note == "Halal" and p.other_note == ""
    assert p.restrictions_consent_at is not None


def test_restrictions_need_consent(member_client, member):
    r = start_post(member_client, 3, restrictions=["nuts"])
    assert r.status_code == 200 and "Tick the box" in r.content.decode()
    assert profile_of(member).restrictions == []
    # "None" is not sensitive: no consent needed, and an earlier consent is cleared.
    assert start_post(member_client, 3, restrictions=["none"]).status_code == 302
    assert profile_of(member).restrictions_consent_at is None


def test_none_is_exclusive(member_client, member):
    r = start_post(member_client, 3, restrictions=["none", "vegan"])
    assert r.status_code == 200 and "cannot be combined" in r.content.decode()
    assert profile_of(member).restrictions == []
    assert start_post(member_client, 3, restrictions=["none"]).status_code == 302
    r = start_post(member_client, 4, conditions=["prefer_not", "diabetes"], consent="1")
    assert r.status_code == 200 and "cannot be combined" in r.content.decode()


def test_conditions_need_consent(member_client, member):
    r = start_post(member_client, 4, conditions=["diabetes"])
    assert r.status_code == 302 and r["Location"] == reverse("today")
    p = profile_of(member)
    assert p.conditions == [] and p.health_consent_at is None and p.onboarded
    msgs = [str(m) for m in r.wsgi_request._messages]
    assert any("not saved" in m for m in msgs)

    start_post(member_client, 4, conditions=["diabetes", "hypertension"], consent="1")
    p = profile_of(member)
    assert p.conditions == ["hypertension", "diabetes"] and p.health_consent_at is not None


def test_finish_with_eating_disorder_hides_energy_and_weight(member_client, member):
    p = get_profile(member)
    p.track_weight = True
    p.save()
    r = start_post(member_client, 4, conditions=["eating_disorder"], consent="1")
    assert r["Location"] == reverse("today")
    p = profile_of(member)
    assert p.onboarded and not p.show_energy and not p.track_weight
    assert p.conditions == ["eating_disorder"]


def test_onboarding_with_htmx_returns_the_next_step(member_client, member):
    r = member_client.post(reverse("start"), {"step": 1, "goal": "lose_weight"}, **HX)
    html = r.content.decode()
    assert r.status_code == 200 and r["HX-Push-Url"] == "/start/?step=2"
    assert "<html" not in html and 'id="onboard"' in html and "Step 2 of 4" in html
    r = member_client.post(reverse("start"), {"step": 4}, **HX)
    assert r.status_code == 204 and r["HX-Redirect"] == reverse("today")
    assert profile_of(member).onboarded


def test_onboarding_all_titles_still_in_page(member_client):
    """Replacement for tests/test_myola.py::test_onboarding_has_the_four_steps_in_order."""
    html = member_client.get(reverse("start")).content.decode()
    titles = [
        "What is your goal?",
        "About you",
        "Do you have any dietary restrictions or food allergies?",
        "Have you ever had any of the following conditions?",
    ]
    positions = [html.index(t) for t in titles]
    assert positions == sorted(positions)
    assert 'placeholder="48"' in html and 'value="48"' not in html


# ---------- Profile page ----------
def test_profile_edit_sections(member_client, member):
    html = member_client.get(reverse("profile-edit")).content.decode()
    for anchor in ('id="about"', 'id="restrictions"', 'id="conditions"'):
        assert anchor in html
    r = member_client.post(
        reverse("profile-edit"),
        {"section": "about", "goal": "", "gender": "female", "age": "30", "weight": "70.25"},
    )
    assert r["Location"] == "/ask-foodo/profile/#about"
    p = profile_of(member)
    assert (p.gender, p.age, p.weight, p.goal) == ("female", 30, Decimal("70.2"), "")

    r = member_client.post(reverse("profile-edit"), {"section": "about", "age": "300"})
    assert r.status_code == 200 and "Check the highlighted value" in r.content.decode()
    assert profile_of(member).age == 30


def test_profile_restrictions_with_next_and_htmx(member_client, member):
    r = member_client.post(
        reverse("profile-edit"),
        {
            "section": "restrictions",
            "restrictions": ["gluten_free"],
            "next": "/products/",
            "consent": "1",
        },
    )
    assert r["Location"] == "/products/"
    assert profile_of(member).restrictions == ["gluten_free"]
    r = member_client.post(
        reverse("profile-edit"),
        {
            "section": "restrictions",
            "restrictions": ["vegan"],
            "next": "https://evil.test/",
            "consent": "1",
        },
    )
    assert r["Location"] == "/ask-foodo/profile/#restrictions"
    r = member_client.post(
        reverse("profile-edit"),
        {"section": "restrictions", "restrictions": ["lactose"], "consent": "1"},
        **HX,
    )
    html = r.content.decode()
    assert r.status_code == 200 and html.lstrip().startswith('<form id="restrictions"')
    assert "Restrictions and allergies saved." in html
    assert derive_prefs(profile_of(member))["diets"] == ["lactose_free"]


def test_profile_conditions_consent_and_clear(member_client, member):
    member_client.post(
        reverse("profile-edit"),
        {"section": "conditions", "conditions": ["depression"], "consent": "1"},
    )
    p = profile_of(member)
    assert p.conditions == ["depression"] and p.health_consent_at
    # Unticking the consent withdraws it and deletes the answers.
    member_client.post(
        reverse("profile-edit"), {"section": "conditions", "conditions": ["depression"]}
    )
    p = profile_of(member)
    assert p.conditions == [] and p.health_consent_at is None

    member_client.post(
        reverse("profile-edit"),
        {"section": "conditions", "conditions": ["diabetes"], "consent": "1"},
    )
    r = member_client.post(reverse("profile-clear-health"))
    assert r["Location"] == "/ask-foodo/profile/#conditions"
    p = profile_of(member)
    assert p.conditions == [] and p.health_consent_at is None
    assert member_client.get(reverse("profile-clear-health")).status_code == 405


# ---------- Settings ----------
def test_settings_save_and_validate(member_client, member):
    html = member_client.get(reverse("settings-edit")).content.decode()
    assert '<option value="Europe/London" selected>' in html
    data = {
        "target_kcal": "2000",
        "target_protein": "",
        "target_carbs": "250",
        "target_fat": "70",
        "show_energy": "1",
        "track_weight": "1",
        "use_notes": "1",
        "timezone": "America/New_York",
    }
    r = member_client.post(reverse("settings-edit"), data)
    assert r["Location"] == reverse("settings-edit")
    p = profile_of(member)
    assert (p.target_kcal, p.target_protein, p.target_carbs, p.target_fat) == (2000, None, 250, 70)
    assert p.show_energy and not p.show_targets and p.track_weight and p.use_notes
    assert p.timezone == "America/New_York"

    for field, bad in (
        ("target_kcal", "10001"),
        ("target_protein", "0"),
        ("target_carbs", "abc"),
        ("timezone", "Mars/Olympus"),
    ):
        r = member_client.post(reverse("settings-edit"), {**data, field: bad})
        assert r.status_code == 200 and 'aria-invalid="true"' in r.content.decode(), field
    assert profile_of(member).target_kcal == 2000

    r = member_client.post(reverse("settings-edit"), {**data, "target_kcal": ""}, **HX)
    assert r.status_code == 200 and "Settings saved." in r.content.decode()
    assert profile_of(member).target_kcal is None


# ---------- Progress ----------
def _diary(user, day, kcal, amount=100, basis_known=True):
    return DiaryEntry.objects.create(
        user=user,
        date=day,
        meal="lunch",
        name="Food",
        amount=Decimal(amount),
        unit="g",
        basis_known=basis_known,
        kcal_100=kcal,
        protein_100=Decimal(10),
        carbs_100=Decimal(20),
        fat_100=Decimal(5),
    )


def test_progress_seven_and_thirty_days(member_client, member):
    today = local_today(get_profile(member))
    _diary(member, today, Decimal(200), amount=150)  # 300 kcal
    _diary(member, today - dt.timedelta(days=2), Decimal(100))  # 100 kcal
    _diary(member, today - dt.timedelta(days=2), None)  # unknown energy: partial day
    _diary(member, today - dt.timedelta(days=20), Decimal(500))
    WaterEntry.objects.create(user=member, date=today, ml=500)
    WaterEntry.objects.create(user=member, date=today, ml=250)
    ActivityEntry.objects.create(user=member, date=today, kind="walking", minutes=30)

    r = member_client.get(reverse("progress"))
    html = r.content.decode()
    assert r.status_code == 200
    assert "You logged food on 2 of the last 7 days." in html
    assert "Average 200 kcal" in html  # (300 + 100) / 2 logged days
    assert "Average 750 ml" in html and "Average 30 min" in html
    assert html.count('class="bar-rect is-partial"') == 1
    assert "Some days are partial" in html
    assert html.count("<rect ") == 6 * 7
    assert "<script" not in html.split("</head>")[1] and 'style="' not in html

    html = member_client.get(reverse("progress"), {"range": "30"}).content.decode()
    assert "You logged food on 3 of the last 30 days." in html
    assert "Average 300 kcal" in html
    assert html.count("<rect ") == 6 * 30

    r = member_client.get(reverse("progress"), {"range": "9"}, **HX)
    assert r["HX-Push-Url"] == "/progress/?range=7" and "<html" not in r.content.decode()


def test_progress_hides_energy_when_asked_or_with_eating_disorder(member_client, member):
    p = get_profile(member)
    p.show_energy = False
    p.save()
    assert 'id="chart-energy"' not in member_client.get(reverse("progress")).content.decode()
    p.show_energy = True
    p.conditions = ["eating_disorder"]
    p.save()
    assert 'id="chart-energy"' not in member_client.get(reverse("progress")).content.decode()
    p.conditions = []
    p.save()
    assert 'id="chart-energy"' in member_client.get(reverse("progress")).content.decode()


def test_weight_save_replace_and_delete(member_client, member, django_user_model):
    p = get_profile(member)
    html = member_client.get(reverse("progress")).content.decode()
    assert 'id="weight"' not in html  # weight tracking is off by default
    p.track_weight = True
    p.save()
    assert 'id="weight"' in member_client.get(reverse("progress")).content.decode()

    r = member_client.post(reverse("progress-weight"), {"kg": "80.4", "range": "30"})
    assert r["Location"] == "/progress/?range=30#weight"
    r = member_client.post(reverse("progress-weight"), {"kg": "79,9"})
    assert WeightEntry.objects.filter(user=member).count() == 1
    entry = WeightEntry.objects.get(user=member)
    assert entry.kg == Decimal("79.9") and entry.date == local_today(p)

    r = member_client.post(reverse("progress-weight"), {"kg": "12"})
    assert r.status_code == 200 and "Enter a weight between 25 and 400 kg." in r.content.decode()
    r = member_client.post(reverse("progress-weight"), {"kg": ""}, **HX)
    assert r.status_code == 200 and 'id="weight"' in r.content.decode()
    assert WeightEntry.objects.get(user=member).kg == Decimal("79.9")

    html = member_client.get(reverse("progress")).content.decode()
    assert "79.9 kg" in html

    other = django_user_model.objects.create_user(username="other", password="x-long-pass-123")
    theirs = WeightEntry.objects.create(user=other, date=entry.date, kg=Decimal(60))
    assert (
        member_client.post(reverse("progress-weight-delete", args=[theirs.pk])).status_code == 404
    )
    r = member_client.post(reverse("progress-weight-delete", args=[entry.pk]), **HX)
    assert r.status_code == 200 and "No weight logged in this period." in r.content.decode()
    assert not WeightEntry.objects.filter(user=member).exists()


def test_restrictions_consent_ticked_alone_stays_ticked(member_client, member):
    r = member_client.post(
        reverse("profile-edit"), {"section": "restrictions", "consent": "1"}, HTTP_HX_REQUEST="true"
    )
    assert r.status_code == 200
    assert profile_of(member).restrictions_consent_at is not None
    assert 'name="consent" value="1" checked' in r.content.decode()
