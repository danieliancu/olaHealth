"""Background jobs (django-q2) for in-app notifications: daily reminders, achievements,
announcements and clean-up. Every job is safe to re-run: notifications are unique per key."""

from __future__ import annotations

import datetime as dt

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from myola import diary_target
from myola.models import Notification, WaterEntry
from myola.services import diary, nudges, stats
from myola.services import notifications as notes
from myola.services.profiles import get_profile, user_tz

REMINDERS_PER_DAY = 2
ACTIVE_DAYS = 14


def _scan_url(day: dt.date, meal: str = "auto") -> str:
    return f"{reverse('scan')}?{diary_target.query({'add': meal, 'date': day.isoformat()})}"


def reminder_for(user, profile, now: dt.datetime | None = None) -> Notification | None:
    """Today's reminder for one user, if there is something useful to say (at most two a day)."""
    if not profile.notify_reminders or not profile.onboarded:
        return None
    local_now = (now or timezone.now()).astimezone(user_tz(profile))
    today = local_now.date()
    prefix = f"reminder:{today.isoformat()}:"
    if Notification.objects.filter(user=user, key__startswith=prefix).count() >= REMINDERS_PER_DAY:
        return None
    groups, _totals = diary.day_diary(user, today)
    for g in groups:
        g.scan_url = _scan_url(today, g.key)
    water = sum(WaterEntry.objects.filter(user=user, date=today).values_list("ml", flat=True))
    nudge = nudges.pick_nudge(
        is_today=True, now=local_now, groups=groups, water_ml=water, scan_url=_scan_url(today)
    )
    if nudge is None or nudge.key == "water":
        return None
    url = nudge.url if nudge.url.startswith("/") else f"{reverse('home')}{nudge.url}"
    return notes.notify(
        user,
        Notification.Kind.REMINDER,
        prefix + nudge.key,
        nudge.title,
        nudge.text,
        url,
        icon=nudge.icon,
        profile=profile,
    )


def sync_achievements(user, profile=None) -> int:
    """Turn newly earned achievements into notifications. Ones earned before yesterday arrive
    already read, so the first run does not flood the bell."""
    profile = profile or get_profile(user)
    if not profile.notify_achievements:
        return 0
    today = timezone.now().astimezone(user_tz(profile)).date()
    created = 0
    for a in stats.achievements(user, profile.eating_disorder):
        n = notes.notify(
            user,
            Notification.Kind.ACHIEVEMENT,
            f"achievement:{a.key}",
            a.title,
            a.text,
            f"{reverse('ask-foodo')}?tab=overview#ach-h",
            icon=a.icon,
            read=a.on < today - dt.timedelta(days=1),
            profile=profile,
        )
        created += n is not None
    return created


WATER_CHECKPOINTS = ((17, 1500), (14, 1000), (11, 500))  # local hour, ml expected by then


def water_reminder_for(user, profile, now: dt.datetime | None = None) -> Notification | None:
    """At 11, 14 and 17 (local time): a glass of water, when the day is below a gentle pace."""
    if not profile.notify_water or not profile.onboarded:
        return None
    local_now = (now or timezone.now()).astimezone(user_tz(profile))
    today = local_now.date()
    drunk = sum(WaterEntry.objects.filter(user=user, date=today).values_list("ml", flat=True))
    for hour, expected in WATER_CHECKPOINTS:
        if local_now.hour >= hour:
            if drunk >= expected:
                return None
            so_far = f"{drunk} ml so far today." if drunk else "No water logged yet today."
            return notes.notify(
                user,
                Notification.Kind.REMINDER,
                f"reminder:{today.isoformat()}:water-{hour}",
                "Time for a glass of water",
                f"{so_far} A glass now keeps the day easy.",
                f"{reverse('home')}#water-h",
                icon="glass-water",
                profile=profile,
            )
    return None


def weigh_in_for(user, profile, now: dt.datetime | None = None) -> Notification | None:
    """Once a week on the chosen day, only with weight tracking on and never with an
    eating-disorder answer; skipped when today's weight is already logged."""
    from myola.models import WeightEntry

    if not profile.track_weight or profile.eating_disorder or not profile.notify_reminders:
        return None
    local_now = (now or timezone.now()).astimezone(user_tz(profile))
    today = local_now.date()
    if today.weekday() != profile.weigh_day or local_now.hour < 8:
        return None
    if WeightEntry.objects.filter(user=user, date=today).exists():
        return None
    year, week, _day = today.isocalendar()
    return notes.notify(
        user,
        Notification.Kind.REMINDER,
        f"reminder:weigh:{year}-W{week:02d}",
        "Weekly weigh-in, if you like",
        "Same day and time each week gives the clearest trend. Skipping is fine too.",
        f"{reverse('progress')}#weight",
        icon="scale",
        profile=profile,
    )


def profile_nudge_for(user, profile, now: dt.datetime | None = None) -> Notification | None:
    """Once ever: the profile is under half complete, a day or more after signing up."""
    from myola.services.profiles import profile_completeness

    if not profile.notify_reminders:
        return None
    joined = getattr(user, "date_joined", None)
    if joined and (now or timezone.now()) - joined < dt.timedelta(days=1):
        return None
    if profile_completeness(profile) >= 50:
        return None
    return notes.notify(
        user,
        Notification.Kind.REMINDER,
        "reminder:profile-incomplete",
        "Finish your profile in a minute",
        "Add your goal, allergies and targets so checks and summaries fit you.",
        reverse("profile-edit"),
        icon="user-round",
        profile=profile,
    )


def send_reminders() -> int:
    """Hourly: reminders and achievements for recently active, opted-in users."""
    since = timezone.now() - dt.timedelta(days=ACTIVE_DAYS)
    users = get_user_model().objects.filter(is_active=True, last_login__gte=since)
    sent = 0
    for user in users.iterator():
        profile = get_profile(user)
        sent += reminder_for(user, profile) is not None
        sent += water_reminder_for(user, profile) is not None
        sent += weigh_in_for(user, profile) is not None
        sent += profile_nudge_for(user, profile) is not None
        sent += sync_achievements(user, profile)
    return sent


def fan_out_announcement(announcement_id: int) -> int:
    """Deliver one announcement to every active user who keeps announcements switched on."""
    from staff.models import Announcement

    a = Announcement.objects.filter(pk=announcement_id).first()
    if a is None:
        return 0
    users = (
        get_user_model()
        .objects.filter(is_active=True)
        .filter(Q(profile__isnull=True) | Q(profile__notify_announcements=True))
    )
    rows = [
        Notification(
            user=u,
            kind=Notification.Kind.ANNOUNCEMENT,
            key=f"announcement:{a.pk}",
            title=a.title,
            body=a.body,
            url=notes.safe_url(a.url),
            icon="megaphone",
        )
        for u in users.only("pk").iterator()
    ]
    Notification.objects.bulk_create(rows, ignore_conflicts=True, batch_size=500)
    return len(rows)


def notify_new_articles() -> int:
    """A notification (the article's title) for every published article whose date has come,
    once per article, to everyone who keeps announcements switched on. Runs on save and hourly
    (for future-dated articles)."""
    from django.urls import reverse
    from django.utils import timezone

    from core.models import LearnArticle

    now = timezone.now()
    due = LearnArticle.objects.filter(
        published=True, published_at__lte=now, notified_at__isnull=True
    )
    users = (
        get_user_model()
        .objects.filter(is_active=True)
        .filter(Q(profile__isnull=True) | Q(profile__notify_announcements=True))
    )
    sent = 0
    for a in due.select_related("topic"):
        # Claim the article first so two workers never send it twice.
        if not LearnArticle.objects.filter(pk=a.pk, notified_at__isnull=True).update(
            notified_at=now
        ):
            continue
        rows = [
            Notification(
                user=u,
                kind=Notification.Kind.ANNOUNCEMENT,
                key=f"learn:{a.pk}",
                # Same style as the other notifications: a short headline, then one sentence.
                title="New article in Learn",
                body=f"“{a.title}”: a {a.minutes}-minute read in {a.topic.title}.",
                url=reverse("learn-article", args=[a.slug]),
                icon="book-open",
            )
            for u in users.only("pk").iterator()
        ]
        Notification.objects.bulk_create(rows, ignore_conflicts=True, batch_size=500)
        sent += len(rows)
    return sent


def purge_old_notifications() -> int:
    return notes.purge_old()
