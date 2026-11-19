"""“Add to diary” mode: carried from the Food diary through the scanner and the catalogue.

A diary button links to ``/scan/?add=lunch&date=2026-11-18`` (or ``/products/?...``). While the
mode is on, a found product opens the short “how much?” step (``add-food``) instead of the
full product page. Values are validated and never trusted for anything else.
"""

from __future__ import annotations

import re

from django.urls import reverse
from django.utils.http import urlencode
from django.utils.translation import gettext_lazy as _

MEALS = ("breakfast", "lunch", "dinner", "snacks", "auto")
LABELS = {
    "breakfast": _("Breakfast"),
    "lunch": _("Lunch"),
    "dinner": _("Dinner"),
    "snacks": _("Snacks"),
}
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def from_params(params) -> dict[str, str] | None:
    meal = (params.get("add") or "").strip()
    date = (params.get("date") or "").strip()
    if meal not in MEALS:
        return None
    return {"add": meal, "date": date if _DATE.match(date) else ""}


def query(target: dict[str, str] | None) -> str:
    return urlencode({k: v for k, v in (target or {}).items() if v})


def today_url(target: dict[str, str]) -> str:
    """Back to the Food diary on the day the user started from."""
    url = reverse("today")
    return f"{url}?{urlencode({'date': target['date']})}" if target.get("date") else url


def add_url(pk, target: dict[str, str], scanned: str = "") -> str:
    params = {"meal": target["add"], "date": target["date"], "scanned": scanned[:32]}
    return f"{reverse('add-food', args=[pk])}?{urlencode({k: v for k, v in params.items() if v})}"
