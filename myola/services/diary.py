"""Food diary arithmetic: values of one entry, day totals, meals, water and activity.

An entry with an unknown basis (per 100 g or per 100 ml not stated) has no values: day totals
then carry ``partial`` so the page never shows an incomplete number as complete.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Sum
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _

from catalog.presenters import format_number
from myola.models import LIMITS, ActivityEntry, DiaryEntry, Meal, WaterEntry

NUTRIENTS = {
    "energy-kcal": "kcal_100",
    "proteins": "protein_100",
    "carbohydrates": "carbs_100",
    "fat": "fat_100",
}
NUTRIENT_LABELS = {
    "energy-kcal": ("Energy", "kcal"),
    "proteins": ("Protein", "g"),
    "carbohydrates": ("Carbs", "g"),
    "fat": ("Fat", "g"),
}
MEAL_ICONS = {
    "breakfast": "coffee",
    "lunch": "sandwich",
    "dinner": "soup",
    "snacks": "cookie",
}


def _round(value: Decimal, places: str = "0.1") -> Decimal:
    return value.quantize(Decimal(places), rounding=ROUND_HALF_UP)


def round_amount(value: Decimal) -> Decimal:
    """Amounts are stored with one decimal."""
    return _round(Decimal(value))


def entry_values(entry: DiaryEntry) -> dict[str, Decimal | None]:
    """Nutrients for the amount eaten: per100 × amount / 100, or None when unknown."""
    out: dict[str, Decimal | None] = {}
    for key, attr in NUTRIENTS.items():
        per100 = getattr(entry, attr)
        if not entry.basis_known or per100 is None:
            out[key] = None
        else:
            out[key] = _round(Decimal(per100) * Decimal(entry.amount) / 100)
    return out


@dataclass
class Totals:
    values: dict[str, Decimal] = field(default_factory=lambda: dict.fromkeys(NUTRIENTS, Decimal(0)))
    partial: dict[str, bool] = field(default_factory=lambda: dict.fromkeys(NUTRIENTS, False))
    count: int = 0

    @property
    def any_partial(self) -> bool:
        return any(self.partial.values())

    def add(self, values: dict[str, Decimal | None]) -> None:
        self.count += 1
        for key, value in values.items():
            if value is None:
                self.partial[key] = True
            else:
                self.values[key] += value


def totals(entries) -> Totals:
    t = Totals()
    for e in entries:
        t.add(entry_values(e))
    for key in t.values:
        t.values[key] = (
            _round(t.values[key], "1") if key == "energy-kcal" else _round(t.values[key])
        )
    return t


@dataclass
class MealGroup:
    key: str
    label: str
    entries: list
    totals: Totals

    @property
    def icon(self) -> str:
        return MEAL_ICONS[self.key]

    @property
    def kcal_display(self) -> str:
        return fmt(self.totals.values["energy-kcal"])

    @property
    def kcal_partial(self) -> bool:
        return self.totals.partial["energy-kcal"]


def day_diary(user, day: dt.date) -> tuple[list[MealGroup], Totals]:
    entries = list(DiaryEntry.objects.filter(user=user, date=day).order_by("created_at"))
    for e in entries:
        e.values = entry_values(e)
    groups = []
    for meal in Meal:
        in_meal = [e for e in entries if e.meal == meal.value]
        groups.append(MealGroup(meal.value, str(meal.label), in_meal, totals(in_meal)))
    return groups, totals(entries)


def default_meal(now: dt.datetime | None = None) -> str:
    """Breakfast before 11:00, lunch before 16:00, dinner before 21:30, else snacks.

    Pass ``now`` in the user's timezone (``timezone.now().astimezone(user_tz(profile))``).
    """
    now = now or timezone.localtime()
    minutes = now.hour * 60 + now.minute
    if minutes < 11 * 60:
        return Meal.BREAKFAST
    if minutes < 16 * 60:
        return Meal.LUNCH
    if minutes < 21 * 60 + 30:
        return Meal.DINNER
    return Meal.SNACKS


def water_total(user, day: dt.date) -> int:
    return WaterEntry.objects.filter(user=user, date=day).aggregate(s=Sum("ml"))["s"] or 0


def activity_total(user, day: dt.date) -> int:
    return ActivityEntry.objects.filter(user=user, date=day).aggregate(s=Sum("minutes"))["s"] or 0


def target_rows(t: Totals, targets: dict[str, int | None], show_energy: bool = True) -> list[dict]:
    """Rows for the daily summary: value, target, percentage (capped for the meter)."""
    rows = []
    for key, (label, unit) in NUTRIENT_LABELS.items():
        if key == "energy-kcal" and not show_energy:
            continue
        value = t.values[key]
        target = targets.get(key)
        pct = int(min(value / target * 100, 100)) if target else None
        rows.append(
            {
                "key": key,
                "label": label,
                "unit": unit,
                "value": value,
                "display": fmt(value),
                "target": target,
                "pct": pct,
                "over": bool(target and value > target),
                "partial": t.partial[key],
            }
        )
    return rows


def preview(per100: dict[str, Decimal | None], amount: Decimal | None, basis_known: bool) -> dict:
    """Live preview for the add/edit form (same arithmetic as a saved entry)."""
    fake = DiaryEntry(amount=amount or Decimal(0), basis_known=basis_known)
    for key, attr in NUTRIENTS.items():
        setattr(fake, attr, per100.get(key))
    return entry_values(fake) if amount else dict.fromkeys(NUTRIENTS)


# ---------- Display helpers ----------
def fmt(value: Decimal | int | float | None) -> str:
    """Whole numbers from 10 up, one decimal below (as the former browser diary)."""
    if value is None:
        return "–"
    value = Decimal(str(value))
    return format_number(_round(value, "1") if abs(value) >= 10 else _round(value))


def water_display(ml: int) -> str:
    return f"{fmt(Decimal(ml) / 1000)} l" if ml >= 1000 else f"{ml} ml"


def entry_per100(entry: DiaryEntry) -> dict[str, Decimal | None]:
    return {key: getattr(entry, attr) for key, attr in NUTRIENTS.items()}


def preview_rows(
    per100: dict[str, Decimal | None],
    amount: Decimal | None,
    basis_known: bool,
    show_energy: bool = True,
) -> dict:
    """Everything the preview partial shows: one cell per nutrient, or why there are none."""
    values = preview(per100, amount, basis_known)
    rows = []
    for key, (label, unit) in NUTRIENT_LABELS.items():
        if key == "energy-kcal" and not show_energy:
            continue
        value = values[key]
        display = "–" if value is None else f"{fmt(value)} {unit}"
        rows.append({"key": key, "label": label, "unit": unit, "value": value, "display": display})
    known = [r for r in rows if r["value"] is not None]
    return {
        "amount": amount,
        "amount_display": format_number(amount) if amount else "",
        "basis_known": basis_known,
        "rows": rows,
        "any_known": bool(known),
        "missing": len(known) < len(rows),
    }


# ---------- Day picker ----------
def week(user, day: dt.date, today: dt.date) -> dict:
    """The Monday-to-Sunday week holding ``day``, with the days that have food logged."""
    monday = day - dt.timedelta(days=day.weekday())
    days = [monday + dt.timedelta(days=i) for i in range(7)]
    logged = set(
        DiaryEntry.objects.filter(user=user, date__range=(days[0], days[-1]))
        .values_list("date", flat=True)
        .distinct()
    )
    return {
        "days": [
            {
                "date": d,
                "iso": d.isoformat(),
                "selected": d == day,
                "today": d == today,
                "future": d > today,
                "logged": d in logged,
            }
            for d in days
        ],
        "prev": (day - dt.timedelta(days=7)).isoformat(),
        "next": min(day + dt.timedelta(days=7), today).isoformat() if days[-1] < today else None,
    }


def day_title(day: dt.date, today: dt.date) -> str:
    if day == today:
        return _("Today")
    if day == today - dt.timedelta(days=1):
        return _("Yesterday")
    return date_format(day, "l")


# ---------- Saving, limits and undo ----------
class LimitReached(Exception):
    """The per-user limit for this kind of record (``LIMITS``) is reached."""


def check_limit(model, user, key: str) -> None:
    if model.objects.filter(user=user).count() >= LIMITS[key]:
        raise LimitReached(key)


def add_entry(user, **fields) -> DiaryEntry:
    check_limit(DiaryEntry, user, "diary")
    return DiaryEntry.objects.create(user=user, **fields)


def add_water(user, day: dt.date, ml: int) -> WaterEntry:
    check_limit(WaterEntry, user, "water")
    return WaterEntry.objects.create(user=user, date=day, ml=ml)


def add_activity(user, day: dt.date, kind: str, minutes: int) -> ActivityEntry:
    check_limit(ActivityEntry, user, "activity")
    return ActivityEntry.objects.create(user=user, date=day, kind=kind, minutes=minutes)


_UNDO_FIELDS = {
    "entry": (
        DiaryEntry,
        [
            "meal",
            "product_id",
            "name",
            "brand",
            "image",
            "amount",
            "unit",
            "basis_known",
            "portion",
            "kcal_100",
            "protein_100",
            "carbs_100",
            "fat_100",
        ],
    ),
    "water": (WaterEntry, ["ml"]),
    "activity": (ActivityEntry, ["kind", "minutes"]),
}


def undo_data(kind: str, obj) -> dict:
    """A JSON-safe copy of a deleted record, kept in the session so it can be put back."""
    _model, names = _UNDO_FIELDS[kind]
    fields = {}
    for name in names:
        value = getattr(obj, name)
        fields[name] = value if value is None or isinstance(value, bool) else str(value)
    return {
        "kind": kind,
        "pk": str(obj.pk),
        "date": obj.date.isoformat(),
        "created_at": obj.created_at.isoformat(),
        "fields": fields,
    }


def restore(user, data: dict):
    """Re-create a deleted record with its id and place in the day; None when it cannot."""
    model, names = _UNDO_FIELDS[data["kind"]]
    fields = {k: data["fields"].get(k) for k in names}
    if model.objects.filter(pk=data["pk"]).exists():
        return None
    if data["kind"] == "entry" and fields.get("product_id"):
        from catalog.models import Product

        if not Product.objects.filter(pk=fields["product_id"]).exists():
            fields["product_id"] = None
    if data["kind"] == "entry":
        check_limit(model, user, "diary")
    obj = model.objects.create(
        pk=data["pk"], user=user, date=dt.date.fromisoformat(data["date"]), **fields
    )
    created = dt.datetime.fromisoformat(data["created_at"])
    model.objects.filter(pk=obj.pk).update(created_at=created)
    return model.objects.get(pk=obj.pk)
