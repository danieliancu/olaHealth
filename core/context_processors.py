from django.conf import settings
from django.http import HttpRequest


class _Once:
    """A value computed only if a template uses it, then cached (templates call callables)."""

    def __init__(self, func):
        self._func = func
        self._done = False
        self._value = None

    def __call__(self):
        if not self._done:
            self._value = self._func()
            self._done = True
        return self._value


def site(request: HttpRequest) -> dict:
    return {
        "SITE_NAME": settings.SITE_NAME,
        "MAINTENANCE_MESSAGE": _Once(_maintenance_message),
        "learn_topics": _Once(learn_topics),
        "learn_latest": _Once(learn_latest),
        **personal(request),
    }


_MAINTENANCE: dict[str, float | str] = {"at": 0.0, "text": ""}
MAINTENANCE_TTL = 30.0  # seconds: a banner change appears within half a minute


def _maintenance_message() -> str:
    """Banner text set in the staff panel (Site settings); empty when none. Read at most once
    every ``MAINTENANCE_TTL`` seconds per process so pages do not pay a query each."""
    import time

    from django.db import DatabaseError

    from staff.models import SiteSettings

    now = time.monotonic()
    if now - float(_MAINTENANCE["at"]) < MAINTENANCE_TTL:
        return str(_MAINTENANCE["text"])
    try:
        row = SiteSettings.objects.only("maintenance_message").filter(pk=1).first()
    except DatabaseError:
        return ""
    _MAINTENANCE.update(at=now, text=row.maintenance_message if row else "")
    return str(_MAINTENANCE["text"])


def personal(request: HttpRequest) -> dict:
    """Header favourite count (signed-in users) and the session compare selection."""

    def saved_count() -> int:
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return 0
        from myola.models import ProductMark

        return ProductMark.objects.filter(user=user, kind=ProductMark.Kind.FAVORITE).count()

    def compare_ids() -> list[str]:
        if not hasattr(request, "session"):
            return []
        from catalog.views import compare_selection

        return compare_selection(request)

    def notif_unread() -> int:
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return 0
        from myola.services.notifications import unread_count

        return unread_count(user)

    ids = _Once(compare_ids)
    return {
        "saved_count": _Once(saved_count),
        "notif_unread": _Once(notif_unread),
        "compare_ids": ids,
        "compare_count": _Once(lambda: len(ids())),
    }


_TOPICS: dict = {"at": 0.0, "items": []}
TOPICS_TTL = 300.0  # seconds; cleared at once when a topic is saved or deleted (core.signals)


def learn_topics() -> list[tuple[str, str]]:
    """(key, title) of the Learn topics for the footer, cached per process."""
    import time

    from django.db import DatabaseError

    from core.models import LearnTopic

    now = time.monotonic()
    if _TOPICS["items"] and now - float(_TOPICS["at"]) < TOPICS_TTL:
        return list(_TOPICS["items"])
    try:
        items = list(LearnTopic.objects.order_by("order", "title").values_list("key", "title")[:6])
    except DatabaseError:
        return []
    _TOPICS.update(at=now, items=items)
    return items


def reset_learn_topics(**_kwargs) -> None:
    _TOPICS.update(at=0.0, items=[])
    _LATEST.update(at=0.0, items=[])


_LATEST: dict = {"at": 0.0, "items": []}


def learn_latest() -> list[str]:
    """Titles of the 4 newest published Learn articles (the ticker under "Learn"), cached
    per process like the topics and cleared when an article is saved or deleted."""
    import time

    from django.db import DatabaseError

    from core.models import LearnArticle

    now = time.monotonic()
    if _LATEST["items"] and now - float(_LATEST["at"]) < TOPICS_TTL:
        return list(_LATEST["items"])
    try:
        items = list(
            LearnArticle.objects.filter(published=True)
            .order_by("-published_at")
            .values_list("title", flat=True)[:4]
        )
    except DatabaseError:
        return []
    _LATEST.update(at=now, items=items)
    return items
