"""Notifications page: the list behind the bell, open one (marks it read), mark all as read."""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from myola.services import notifications as notes
from myola.services.profiles import get_profile, local_today, user_tz
from myola.tasks import sync_achievements


def _context(request: HttpRequest) -> dict:
    profile = get_profile(request.user)
    items = notes.recent(request.user)
    today = local_today(profile)
    tz = user_tz(profile)
    today_items = [n for n in items if n.created_at.astimezone(tz).date() == today]
    earlier = [n for n in items if n.created_at.astimezone(tz).date() != today]
    return {
        "groups": [g for g in (("today", today_items), ("earlier", earlier)) if g[1]],
        "has_unread": any(n.read_at is None for n in items),
        "nav_active": "",
    }


@login_required
@require_GET
def notifications(request: HttpRequest) -> HttpResponse:
    sync_achievements(request.user)  # newly earned ones show up at once
    return render(request, "myola/notifications.html", _context(request))


@login_required
@require_GET
def notification_open(request: HttpRequest, pk: int) -> HttpResponse:
    n = notes.mark_read(request.user, pk)
    target = notes.safe_url(n.url) if n else ""
    return redirect(target or reverse("notifications"))


@login_required
@require_POST
def notifications_read_all(request: HttpRequest) -> HttpResponse:
    notes.mark_all_read(request.user)
    if request.headers.get("HX-Request") == "true":
        ctx = _context(request)
        ctx["notif_unread"] = 0
        ctx["oob"] = True
        return render(request, "myola/partials/notifications_body.html", ctx)
    return redirect("notifications")


@login_required
@require_POST
def notification_read(request: HttpRequest, pk: int) -> HttpResponse:
    """Mark one notification read: sent in the background when its link is followed, or the
    close button on the Today card (htmx: the card goes and the bell count updates)."""
    notes.mark_read(request.user, pk)
    if request.headers.get("HX-Request") == "true":
        ctx = {"notif_unread": notes.unread_count(request.user), "oob": True}
        return render(request, "partials/notif_count.html", ctx)
    return HttpResponse(status=204)
