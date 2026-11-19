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

    ids = _Once(compare_ids)
    return {
        "saved_count": _Once(saved_count),
        "compare_ids": ids,
        "compare_count": _Once(lambda: len(ids())),
    }
