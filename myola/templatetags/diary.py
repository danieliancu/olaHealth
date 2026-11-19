"""Diary template helpers: the server-rendered day calendar (radio buttons, no date input)."""

from __future__ import annotations

import datetime as dt

from django import template

register = template.Library()


@register.inclusion_tag("myola/partials/day_calendar.html", takes_context=True)
def day_calendar(context, day, *, check: bool = True, name: str = "date"):
    """Calendar grid of the last weeks for ``day`` in the signed-in user's timezone.
    ``check`` marks ``day`` as chosen; a day outside the grid is kept in a hidden input."""
    from myola.services.profiles import get_profile, local_today
    from myola.views_today import _calendar

    request = context.get("request")
    profile = get_profile(request.user) if request and request.user.is_authenticated else None
    today = local_today(profile)
    if isinstance(day, str):
        try:
            day = dt.date.fromisoformat(day)
        except ValueError:
            day = today
    cal = _calendar(today, day or today)
    in_grid = any(c["checked"] for week in cal["weeks"] for c in week)
    return {
        "calendar": cal,
        "check": check,
        "day": day,
        "outside": check and not in_grid,
        "name": name,
    }
