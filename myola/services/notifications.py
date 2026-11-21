"""In-app notifications: the one place that creates, counts and reads them.

Every source (daily reminders, achievements, moderated contributions, announcements) goes
through ``notify()``, which honours the user's switch for that kind and never creates the same
notification twice (``key`` is unique per user). No health data, calories or weight in here.
"""

from __future__ import annotations

import datetime as dt

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from myola.models import Notification
from myola.services.profiles import get_profile

KIND_ICONS = {
    Notification.Kind.REMINDER: "bell",
    Notification.Kind.ACHIEVEMENT: "award",
    Notification.Kind.CONTRIBUTION: "circle-check",
    Notification.Kind.ANNOUNCEMENT: "megaphone",
}
KIND_TONES = {
    Notification.Kind.REMINDER: "green",
    Notification.Kind.ACHIEVEMENT: "amber",
    Notification.Kind.CONTRIBUTION: "blue",
    Notification.Kind.ANNOUNCEMENT: "violet",
}
RECENT_LIMIT = 50


def wants(profile, kind: str) -> bool:
    """The user's switch for this kind of notification."""
    return bool(getattr(profile, f"notify_{kind}s", True))


def safe_url(url: str) -> str:
    """Only links inside the app (a path); anything else becomes empty."""
    url = (url or "").strip()
    if (
        url.startswith("/")
        and not url.startswith("//")
        and url_has_allowed_host_and_scheme(url, allowed_hosts=None)
    ):
        return url
    return ""


def notify(
    user,
    kind: str,
    key: str,
    title: str,
    body: str = "",
    url: str = "",
    icon: str = "",
    *,
    read: bool = False,
    profile=None,
) -> Notification | None:
    """Create the notification unless the user switched this kind off or already has it."""
    profile = profile or get_profile(user)
    if not wants(profile, kind):
        return None
    defaults = {
        "kind": kind,
        "title": title[:160],
        "body": body[:500],
        "url": safe_url(url),
        "icon": icon or KIND_ICONS.get(kind, "bell"),
        "read_at": timezone.now() if read else None,
    }
    try:
        with transaction.atomic():
            obj, created = Notification.objects.get_or_create(
                user=user, key=key[:120], defaults=defaults
            )
    except IntegrityError:  # created concurrently by another worker
        return None
    return obj if created else None


def unread_count(user) -> int:
    return Notification.objects.filter(user=user, read_at__isnull=True).count()


def recent(user, limit: int = RECENT_LIMIT) -> list[Notification]:
    items = list(Notification.objects.filter(user=user)[:limit])
    for n in items:
        n.tone = KIND_TONES.get(n.kind, "green")
    return items


def mark_read(user, pk: int) -> Notification | None:
    n = Notification.objects.filter(user=user, pk=pk).first()
    if n and n.read_at is None:
        n.read_at = timezone.now()
        n.save(update_fields=["read_at"])
    return n


def mark_all_read(user) -> int:
    return Notification.objects.filter(user=user, read_at__isnull=True).update(
        read_at=timezone.now()
    )


def purge_old(now: dt.datetime | None = None) -> int:
    """Read notifications older than 90 days and any older than 180 days."""
    now = now or timezone.now()
    read_old = Notification.objects.filter(
        read_at__isnull=False, created_at__lt=now - dt.timedelta(days=90)
    )
    any_old = Notification.objects.filter(created_at__lt=now - dt.timedelta(days=180))
    return read_old.delete()[0] + any_old.delete()[0]
