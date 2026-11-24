"""In-app notifications: the service, the four sources, the page, the bell and the settings."""

from __future__ import annotations

import datetime as dt

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from catalog.services.importer import import_off_product
from contributions.services import drafts, moderation
from myola import tasks
from myola.models import Notification, ProductVisit
from myola.services import notifications as notes
from myola.services.profiles import get_profile, local_today
from staff.models import Announcement
from tests.test_today import make_entry

pytestmark = pytest.mark.django_db


@pytest.fixture
def product(off_payload):
    return import_off_product(off_payload())


def onboard(user, **fields):
    profile = get_profile(user)
    profile.onboarded = True
    for k, v in fields.items():
        setattr(profile, k, v)
    profile.save()
    return profile


# ---------- Service ----------
def test_notify_is_unique_per_key_and_honours_the_switch(member):
    n = notes.notify(member, "reminder", "k1", "Hello", url="/learn/")
    assert n and n.url == "/learn/" and n.icon == "bell"
    assert notes.notify(member, "reminder", "k1", "Hello again") is None  # same key
    onboard(member, notify_reminders=False)
    assert notes.notify(member, "reminder", "k2", "Muted") is None
    assert Notification.objects.filter(user=member).count() == 1


def test_safe_url_keeps_only_internal_paths():
    assert notes.safe_url("/p/abc/") == "/p/abc/"
    for bad in ("https://evil.test/", "//evil.test/x", "javascript:alert(1)", "p/abc"):
        assert notes.safe_url(bad) == ""


def test_unread_mark_read_and_mark_all(member):
    a = notes.notify(member, "reminder", "a", "A")
    notes.notify(member, "reminder", "b", "B")
    assert notes.unread_count(member) == 2
    notes.mark_read(member, a.pk)
    assert notes.unread_count(member) == 1
    notes.mark_all_read(member)
    assert notes.unread_count(member) == 0


def test_purge_old(member):
    old = notes.notify(member, "reminder", "old", "Old", read=True)
    Notification.objects.filter(pk=old.pk).update(
        created_at=timezone.now() - dt.timedelta(days=120)
    )
    notes.notify(member, "reminder", "new", "New")
    assert notes.purge_old() == 1 and Notification.objects.filter(user=member).count() == 1


# ---------- Reminders ----------
def test_reminder_uses_local_time_and_at_most_two_a_day(member):
    profile = onboard(member, timezone="Europe/London")
    today = local_today(profile)
    noon = timezone.make_aware(
        dt.datetime.combine(today, dt.time(12, 30)), timezone.get_current_timezone()
    )
    first = tasks.reminder_for(member, profile, now=noon)
    assert first and first.kind == "reminder" and first.key.startswith(f"reminder:{today}:")
    assert "kcal" not in first.body.lower()
    assert tasks.reminder_for(member, profile, now=noon) is None  # same nudge, same key
    make_entry(member, day=today, meal="breakfast")
    second = tasks.reminder_for(member, profile, now=noon.replace(hour=16))
    assert second is not None
    assert tasks.reminder_for(member, profile, now=noon.replace(hour=22)) is None  # daily cap


def test_water_reminders_do_not_use_up_the_meal_reminders(member):
    # Regression: two water reminders used to fill the daily cap, so "Add lunch?" never came.
    profile = onboard(member, timezone="Europe/London")
    today = local_today(profile)
    assert tasks.reminder_for(member, profile, now=_local(profile, 10)).key.endswith(":start")
    tasks.water_reminder_for(member, profile, now=_local(profile, 11))
    tasks.water_reminder_for(member, profile, now=_local(profile, 14))
    make_entry(member, day=today, meal="breakfast")
    lunch = tasks.reminder_for(member, profile, now=_local(profile, 15))
    assert lunch is not None and lunch.key.endswith(":meal-lunch")
    assert Notification.objects.filter(user=member, key__contains="meal-lunch").exists()


def test_no_reminders_when_switched_off_or_not_onboarded(member):
    profile = get_profile(member)
    assert tasks.reminder_for(member, profile) is None  # not onboarded yet
    profile = onboard(member, notify_reminders=False)
    assert tasks.reminder_for(member, profile) is None


# ---------- Achievements ----------
def test_achievements_old_ones_arrive_read(member, product):
    onboard(member)
    visit = ProductVisit.objects.create(user=member, product=product, code="123")
    ProductVisit.objects.filter(pk=visit.pk).update(
        created_at=timezone.now() - dt.timedelta(days=10)
    )
    make_entry(member)  # today: a new achievement
    assert tasks.sync_achievements(member) >= 2
    first_scan = Notification.objects.get(user=member, key="achievement:first-scan")
    first_day = Notification.objects.get(user=member, key="achievement:first-day")
    assert first_scan.read_at is not None and first_day.read_at is None
    assert first_scan.icon == "scan-barcode"
    assert tasks.sync_achievements(member) == 0  # nothing new


def test_no_logging_achievements_with_an_eating_disorder_answer(member):
    onboard(member, conditions=["eating_disorder"])
    make_entry(member)
    tasks.sync_achievements(member)
    assert not Notification.objects.filter(user=member, key="achievement:first-day").exists()


# ---------- Contributions ----------
def _submitted(product, user=None):
    c = drafts.create_draft(
        session_key="s", problem_type="incorrect_data", product=product, user=user
    )
    drafts.save_proposal(c, {"brand": "Corrected"}, "")
    assert drafts.submit(c, consent=True) == []
    return c


def test_moderation_notifies_signed_in_contributors_only(
    member, product, admin_user, django_capture_on_commit_callbacks
):
    mine = _submitted(product, user=member)
    anonymous = _submitted(product)
    with django_capture_on_commit_callbacks(execute=True):
        moderation.approve(mine.pk, admin_user, "ok")
    n = Notification.objects.get(user=member)
    assert (
        n.kind == "contribution"
        and "accepted" in n.title
        and n.url == reverse("product", args=[product.pk])
    )
    with django_capture_on_commit_callbacks(execute=True):
        moderation.reject(anonymous.pk, admin_user, "blurry")
    assert Notification.objects.count() == 1


def test_rejection_includes_the_reason(
    member, product, admin_user, django_capture_on_commit_callbacks
):
    c = _submitted(product, user=member)
    with django_capture_on_commit_callbacks(execute=True):
        moderation.reject(c.pk, admin_user, "The photo is blurry")
    n = Notification.objects.get(user=member)
    assert "not accepted" in n.title and n.body == "The photo is blurry"


# ---------- Announcements ----------
def test_announcement_reaches_opted_in_active_users(member, admin_user):
    other = get_user_model().objects.create_user("o", "o@example.com", "x-password-123")
    onboard(other, notify_announcements=False)
    gone = get_user_model().objects.create_user(
        "g", "g@example.com", "x-password-123", is_active=False
    )
    a = Announcement.objects.create(title="New: Learn", url="/learn/")
    tasks.fan_out_announcement(a.pk)
    tasks.fan_out_announcement(a.pk)  # re-run is harmless
    keys = set(Notification.objects.values_list("user__username", flat=True))
    assert "member" in keys and "o" not in keys and gone.username not in keys
    assert Notification.objects.filter(user=member).count() == 1


def test_staff_can_send_an_announcement(client, admin_user, queued_tasks):
    client.force_login(admin_user)
    r = client.post(
        reverse("staff:announcements"), {"title": "Hello", "body": "", "url": "/learn/"}
    )
    assert r.status_code == 302 and Announcement.objects.filter(title="Hello").exists()
    assert queued_tasks[-1][0] == "myola.tasks.fan_out_announcement"
    r = client.post(reverse("staff:announcements"), {"title": "Bad", "url": "https://evil.test/"})
    assert r.status_code == 200 and not Announcement.objects.filter(title="Bad").exists()


def test_announcements_page_lists_what_is_coming_up(client, admin_user):
    from core.models import LearnArticle, LearnTopic

    topic = LearnTopic.objects.create(key="t", title="Topic")
    LearnArticle.objects.create(
        topic=topic, slug="soon", title="Coming soon", summary="s", body="b", published=True,
        published_at=timezone.now() + dt.timedelta(days=2),
    )  # fmt: skip
    client.force_login(admin_user)
    html = client.get(reverse("staff:announcements")).content.decode()
    assert "Coming up automatically" in html
    for time, title in (
        ("09:00", "Your diary is open"),
        ("15:00", "Add lunch?"),
        ("17:00", "Time for a glass of water"),
        ("21:00", "Add dinner?"),
    ):
        assert f"<time>{time}</time>" in html and title in html
    assert "Weekly weigh-in" in html and "Coming soon" in html
    hours = [r["hour"] for r in tasks.automatic_schedule()]
    assert hours == sorted(hours) and len(hours) == 7


# ---------- Pages ----------
def test_pages_need_an_account(client):
    assert client.get(reverse("notifications")).status_code == 302


def test_notifications_page_open_and_read_all(member_client, member):
    a = notes.notify(member, "reminder", "a", "Add lunch?", url="/learn/")
    notes.notify(member, "announcement", "b", "News")
    html = member_client.get(reverse("notifications")).content.decode()
    assert "Add lunch?" in html and "News" in html and "Mark all as read" in html
    assert 'style="' not in html and "<script" not in html.split("</head>")[1]
    r = member_client.get(reverse("notification-open", args=[a.pk]))
    assert r.status_code == 302 and r["Location"] == "/learn/"
    a.refresh_from_db()
    assert a.read_at is not None
    r = member_client.post(reverse("notifications-read-all"), HTTP_HX_REQUEST="true")
    assert r.status_code == 200 and 'id="notif-count"' in r.content.decode()
    assert notes.unread_count(member) == 0


def test_bell_on_home_shows_the_unread_count(member_client, member):
    html = member_client.get(reverse("home")).content.decode()
    assert 'href="/notifications/"' in html and 'id="notif-count" hidden' in html
    notes.notify(member, "reminder", "a", "A")
    html = member_client.get(reverse("home")).content.decode()
    assert '<span class="count" id="notif-count">1</span>' in html


def test_settings_switches(member_client, member):
    html = member_client.get(reverse("settings-edit")).content.decode()
    assert 'name="notify_reminders"' in html and 'name="notify_announcements"' in html


def test_links_go_straight_to_the_target_and_mark_read_in_the_background(member_client, member):
    n = notes.notify(member, "announcement", "x", "New in Learn", url="/learn/")
    html = member_client.get(reverse("notifications")).content.decode()
    assert f'href="/learn/" data-notif-read="/notifications/{n.pk}/read/"' in html
    assert member_client.post(reverse("notification-read", args=[n.pk])).status_code == 204
    n.refresh_from_db()
    assert n.read_at is not None


# ---------- Water, weigh-in, profile ----------
def _local(profile, hour, day=None):
    from myola.services.profiles import user_tz

    d = day or local_today(profile)
    return dt.datetime.combine(d, dt.time(hour, 5), tzinfo=user_tz(profile))


def test_water_reminders_follow_a_gentle_pace(member):
    from myola.models import WaterEntry

    profile = onboard(member)
    today = local_today(profile)
    assert tasks.water_reminder_for(member, profile, now=_local(profile, 10)) is None  # too early
    n = tasks.water_reminder_for(member, profile, now=_local(profile, 11))
    assert n and n.key.endswith("water-11") and "No water logged yet" in n.body
    WaterEntry.objects.create(user=member, date=today, ml=1000)
    assert tasks.water_reminder_for(member, profile, now=_local(profile, 14)) is None  # on pace
    n = tasks.water_reminder_for(member, profile, now=_local(profile, 17))
    assert n and n.key.endswith("water-17") and "1000 ml" in n.body
    profile.notify_water = False
    profile.save()
    WaterEntry.objects.all().delete()
    assert tasks.water_reminder_for(member, profile, now=_local(profile, 18)) is None


def test_weigh_in_only_on_the_chosen_day_and_never_with_ed(member):
    profile = onboard(member, track_weight=True)
    today = local_today(profile)
    profile.weigh_day = today.weekday()
    profile.save()
    n = tasks.weigh_in_for(member, profile, now=_local(profile, 9))
    assert n and n.icon == "scale" and n.url.endswith("#weight")
    assert tasks.weigh_in_for(member, profile, now=_local(profile, 10)) is None  # once a week
    other = get_user_model().objects.create_user("w", "w@example.com", "x-password-123")
    p2 = onboard(
        other, track_weight=True, conditions=["eating_disorder"], weigh_day=today.weekday()
    )
    assert tasks.weigh_in_for(other, p2, now=_local(p2, 9)) is None


def test_incomplete_profile_reminder_only_once(member):
    from django.utils import timezone as tz

    get_user_model().objects.filter(pk=member.pk).update(
        date_joined=tz.now() - dt.timedelta(days=3)
    )
    member.refresh_from_db()
    profile = onboard(member)
    n = tasks.profile_nudge_for(member, profile)
    assert n and n.url == reverse("profile-edit")
    assert tasks.profile_nudge_for(member, profile) is None


# ---------- Top of Today: the latest notification while unread ----------
@pytest.mark.django_db
def test_today_card_is_the_latest_notification_only_while_unread(member_client, member):
    onboard(member)
    today = reverse("today")
    assert 'id="home-note"' not in member_client.get(today).content.decode()  # none yet
    older = notes.notify(member, "announcement", "a:1", "New feature", "Try it.", "/learn/")
    lunch = notes.notify(
        member, "reminder", f"reminder:{local_today(get_profile(member))}:meal-lunch",
        "Add lunch?", "If you had lunch, you can still add it.", "/scan/?add=lunch",
    )  # fmt: skip
    html = member_client.get(today).content.decode()
    assert "Add lunch?" in html and "Scan for lunch" in html and "New feature" not in html
    assert reverse("notification-open", args=[lunch.pk]) in html  # opening marks it read
    # Read (here: the close button) → no card, and the older unread one does not take its place.
    r = member_client.post(reverse("notification-read", args=[lunch.pk]), HTTP_HX_REQUEST="true")
    assert r.status_code == 200 and 'id="notif-count"' in r.content.decode()
    html = member_client.get(today).content.decode()
    assert 'id="home-note"' not in html and "New feature" not in html
    older.refresh_from_db()
    assert older.read_at is None
    # A newer unread one shows again; never on past days.
    notes.notify(member, "achievement", "ach:1", "First scan", "", "")
    assert "First scan" in member_client.get(today).content.decode()
    yesterday = local_today(get_profile(member)) - dt.timedelta(days=1)
    assert "First scan" not in member_client.get(f"{today}?date={yesterday}").content.decode()


def test_article_notification_detail_is_for_wider_screens():
    from core.templatetags.ui import notif_parts

    lead, detail = notif_parts("“Sweeteners on the label”: a 4-minute read in Ingredients.")
    assert lead == "“Sweeteners on the label”" and detail == ": a 4-minute read in Ingredients."
    assert notif_parts("Plain text: no title.") == ("Plain text: no title.", "")


# ---------- Recurring reminders collapse (one per subject) ----------
def _reminders(user, topic):
    return list(Notification.objects.filter(user=user, topic=topic))


def test_a_new_water_reminder_replaces_the_previous_one(member):
    profile = onboard(member)
    yesterday = local_today(profile) - dt.timedelta(days=1)
    tasks.water_reminder_for(member, profile, now=_local(profile, 17, yesterday))
    tasks.water_reminder_for(member, profile, now=_local(profile, 11))
    assert notes.unread_count(member) == 1
    last = tasks.water_reminder_for(member, profile, now=_local(profile, 14))
    (only,) = _reminders(member, "water")
    assert only.pk == last.pk and only.key.endswith("water-14")
    assert notes.unread_count(member) == 1  # replaced, not added


def test_after_a_read_reminder_the_new_one_counts_once(member):
    profile = onboard(member)
    first = tasks.water_reminder_for(member, profile, now=_local(profile, 11))
    notes.mark_read(member, first.pk)
    assert notes.unread_count(member) == 0
    tasks.water_reminder_for(member, profile, now=_local(profile, 14))
    assert len(_reminders(member, "water")) == 1 and notes.unread_count(member) == 1


def test_meal_reminders_collapse_per_meal_not_across_meals(member):
    assert notes.notify(member, "reminder", "reminder:2026-11-21:meal-lunch", "Add lunch?")
    assert notes.notify(member, "reminder", "reminder:2026-11-22:meal-lunch", "Add lunch?")
    assert notes.notify(member, "reminder", "reminder:2026-11-22:meal-dinner", "Add dinner?")
    keys = sorted(Notification.objects.filter(user=member).values_list("key", flat=True))
    assert keys == ["reminder:2026-11-22:meal-dinner", "reminder:2026-11-22:meal-lunch"]
    assert notes.unread_count(member) == 2


def test_weigh_in_collapses_and_one_off_events_never_do(member):
    notes.notify(member, "reminder", "reminder:weigh:2026-W46", "Weigh-in")
    notes.notify(member, "reminder", "reminder:weigh:2026-W47", "Weigh-in")
    assert [n.key for n in _reminders(member, "weigh")] == ["reminder:weigh:2026-W47"]
    for key in ("achievement:first-scan", "achievement:streak-3"):
        notes.notify(member, "achievement", key, "Well done")
    notes.notify(member, "reminder", "reminder:profile-incomplete", "Finish your profile")
    assert Notification.objects.filter(user=member, kind="achievement").count() == 2
    assert Notification.objects.get(key="reminder:profile-incomplete").topic == ""


def test_topics():
    assert notes.reminder_topic("reminder", "reminder:2026-11-22:water-17") == "water"
    assert notes.reminder_topic("reminder", "reminder:2026-11-22:start") == "start"
    assert notes.reminder_topic("reminder", "reminder:weigh:2026-W47") == "weigh"
    assert notes.reminder_topic("reminder", "reminder:profile-incomplete") == ""
    assert notes.reminder_topic("achievement", "achievement:a:b") == ""


def test_migration_collapses_existing_duplicates(member):
    import importlib

    from django.apps import apps

    collapse = importlib.import_module("myola.migrations.0013_collapse_repeated_reminders").collapse
    for key in (
        "reminder:2026-11-20:water-11",
        "reminder:2026-11-21:water-14",
        "reminder:2026-11-22:water-17",
        "reminder:2026-11-22:meal-lunch",
        "achievement:x",
    ):
        Notification.objects.create(
            user=member, kind=key.split(":")[0], key=key, title="t", topic=""
        )
    collapse(apps, None)
    keys = sorted(Notification.objects.filter(user=member).values_list("key", flat=True))
    assert keys == [
        "achievement:x",
        "reminder:2026-11-22:meal-lunch",
        "reminder:2026-11-22:water-17",
    ]


def test_unread_notifications_show_in_the_browser_tab(client, member):
    """A red dot on the tab icon and "(n)" before the title while something is unread."""
    client.force_login(member)
    html = client.get(reverse("products")).content.decode()
    assert "<title>Products" in html and 'href="/static/img/icon.svg"' in html
    for i in range(3):
        notes.notify(member, "reminder", f"tab-{i}", f"Hello {i}")
    html = client.get(reverse("products")).content.decode()
    assert "<title>(3) Products" in html and 'href="/static/img/icon-alert.svg"' in html
    assert 'data-notif-unread="3"' in html and "js/notif-badge.js" in html
    Notification.objects.filter(user=member).update(read_at=timezone.now())
    assert "<title>Products" in client.get(reverse("products")).content.decode()
