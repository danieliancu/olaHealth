"""My Track overview: a few calm numbers and achievements, computed from existing records.

No model of its own. With an eating-disorder answer in the profile, nothing that counts how
often food was logged is shown (no "days logged", no logging achievements): only neutral
numbers such as products discovered and water.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from django.db.models import Count, Min, Sum
from django.utils.translation import gettext as _

from myola.models import (
    ActivityEntry,
    ChatMessage,
    DiaryEntry,
    ProductMark,
    ProductVisit,
    WaterEntry,
)


@dataclass(frozen=True)
class Stat:
    key: str
    icon: str
    tone: str  # colour family of the icon circle: green, blue, amber, violet
    value: str
    label: str


ACHIEVEMENTS_SHOWN = 6

ACHIEVEMENT_ICONS = {
    "first-scan": "scan-barcode",
    "first-saved": "heart",
    "first-question": "message-circle",
    "first-day": "notebook-pen",
    "full-day": "utensils",
    "seven-days": "calendar-days",
}


@dataclass(frozen=True)
class Achievement:
    key: str
    title: str
    text: str
    on: dt.date

    @property
    def icon(self) -> str:
        return ACHIEVEMENT_ICONS.get(self.key, "award")


def _date(value) -> dt.date | None:
    if value is None:
        return None
    return value.date() if isinstance(value, dt.datetime) else value


def overview_stats(user, profile, today: dt.date) -> dict:
    sensitive = profile.eating_disorder
    since30 = today - dt.timedelta(days=29)
    since7 = today - dt.timedelta(days=6)

    scans = ProductVisit.objects.filter(user=user).exclude(code="")
    products_seen = ProductVisit.objects.filter(user=user).values("product").distinct().count()
    water_7 = (
        WaterEntry.objects.filter(user=user, date__gte=since7, date__lte=today).aggregate(
            total=Sum("ml")
        )["total"]
        or 0
    )
    active_days = (
        ActivityEntry.objects.filter(user=user, date__gte=since30, date__lte=today)
        .values("date")
        .distinct()
        .count()
    )
    diary_days = (
        DiaryEntry.objects.filter(user=user, date__gte=since30, date__lte=today)
        .values("date")
        .distinct()
        .count()
    )

    water_avg = round(water_7 / 7 / 100) / 10  # litres, one decimal
    if sensitive:
        first = Stat("scans", "search", "green", str(scans.count()), _("Barcodes scanned"))
    else:
        first = Stat(
            "days", "calendar-days", "green", f"{diary_days}/30", _("Days with food logged")
        )
    stats = [
        first,
        Stat("products", "scan-barcode", "violet", str(products_seen), _("Products discovered")),
        Stat("water", "glass-water", "blue", f"{water_avg:g} l", _("Water a day, last 7 days")),
        Stat("active", "activity", "amber", str(active_days), _("Active days, last 30")),
    ]

    earned = achievements(user, sensitive)
    # Desktop shows them two by two: an even number, at most six; the rest behind "See more".
    shown = min(len(earned), ACHIEVEMENTS_SHOWN)
    shown -= shown % 2
    return {"stats": stats, "achievements": earned, "ach_shown": shown or len(earned)}


def achievements(user, sensitive: bool) -> list[Achievement]:
    found: list[Achievement] = []

    first_scan = _date(
        ProductVisit.objects.filter(user=user).exclude(code="").aggregate(m=Min("created_at"))["m"]
    )
    if first_scan:
        found.append(
            Achievement(
                "first-scan", _("First scan"), _("You scanned your first barcode."), first_scan
            )
        )
    first_saved = _date(ProductMark.objects.filter(user=user).aggregate(m=Min("created_at"))["m"])
    if first_saved:
        found.append(
            Achievement(
                "first-saved",
                _("First saved product"),
                _("You saved a product to one of your lists."),
                first_saved,
            )
        )
    first_question = _date(
        ChatMessage.objects.filter(user=user, role=ChatMessage.Role.USER).aggregate(
            m=Min("created_at")
        )["m"]
    )
    if first_question:
        found.append(
            Achievement(
                "first-question",
                _("First question"),
                _("You asked Foodo your first question."),
                first_question,
            )
        )

    if not sensitive:
        days = list(
            DiaryEntry.objects.filter(user=user)
            .values("date")
            .annotate(meals=Count("meal", distinct=True))
            .order_by("date")
        )
        if days:
            found.append(
                Achievement(
                    "first-day",
                    _("First day in the diary"),
                    _("You logged food for the first time."),
                    days[0]["date"],
                )
            )
        full = next((d["date"] for d in days if d["meals"] >= 3), None)
        if full:
            found.append(
                Achievement(
                    "full-day", _("A full day"), _("Three meals logged on the same day."), full
                )
            )
        if len(days) >= 7:
            found.append(
                Achievement(
                    "seven-days",
                    _("Seven days logged"),
                    _("Food logged on seven different days (not in a row)."),
                    days[6]["date"],
                )
            )

    found.sort(key=lambda a: a.on, reverse=True)
    return found
