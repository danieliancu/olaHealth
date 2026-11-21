"""Access control for the staff panel.

* Anonymous visitors are sent to the allauth login page.
* Signed-in users without ``is_staff`` get 403.
* Sections need a permission on top of ``is_staff`` (superusers have all of them):

  ============================  ====================================================
  Moderation queue (view)       ``contributions.view_contribution``
  Moderation actions            ``contributions.moderate_contribution``
  Review flags (view)           ``catalog.view_reviewflag``
  Review flag actions           ``contributions.moderate_contribution``
  Products (view)               ``catalog.view_product``
  "Refresh from OFF now"        ``contributions.moderate_contribution``
  Users                         ``staff.manage_users``
  Site settings                 ``staff.manage_settings``
  Audit log                     ``staff.view_staffaction``
  Learn articles (CMS)          ``core.change_learnarticle``
  ============================  ====================================================

The "Reviewers" group (``setup_roles``) can therefore read the queue, flags and products;
"Moderators" can also act on them. Neither can manage users or settings.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.shortcuts import resolve_url

PERM_VIEW_CONTRIBUTIONS = "contributions.view_contribution"
PERM_MODERATE = "contributions.moderate_contribution"
PERM_VIEW_FLAGS = "catalog.view_reviewflag"
PERM_VIEW_PRODUCTS = "catalog.view_product"
PERM_MANAGE_USERS = "staff.manage_users"
PERM_MANAGE_SETTINGS = "staff.manage_settings"
PERM_VIEW_AUDIT = "staff.view_staffaction"
PERM_MANAGE_LEARN = "core.change_learnarticle"


def is_staff_user(user) -> bool:
    return bool(user.is_authenticated and user.is_active and user.is_staff)


def staff_view(perm: str | None = None) -> Callable:
    """Decorate a view: staff only, optionally with one extra permission."""

    def decorator(view: Callable) -> Callable:
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            user = request.user
            if not user.is_authenticated:
                return redirect_to_login(request.get_full_path(), resolve_url("account_login"))
            if not is_staff_user(user) or (perm and not user.has_perm(perm)):
                raise PermissionDenied
            return view(request, *args, **kwargs)

        return wrapped

    return decorator


def require_perm(user, perm: str) -> None:
    if not user.has_perm(perm):
        raise PermissionDenied


def sections(user) -> dict[str, bool]:
    """Which panel sections this staff member can open (drives the navigation)."""
    return {
        "moderation": user.has_perm(PERM_VIEW_CONTRIBUTIONS),
        "flags": user.has_perm(PERM_VIEW_FLAGS),
        "products": user.has_perm(PERM_VIEW_PRODUCTS),
        "users": user.has_perm(PERM_MANAGE_USERS),
        "settings": user.has_perm(PERM_MANAGE_SETTINGS),
        "audit": user.has_perm(PERM_VIEW_AUDIT),
        "learn": user.has_perm(PERM_MANAGE_LEARN),
        "can_moderate": user.has_perm(PERM_MODERATE),
    }
