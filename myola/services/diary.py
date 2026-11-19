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

from myola.models import ActivityEntry, DiaryEntry, Meal, WaterEntry

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


def _round(value: Decimal, places: str = "0.1") -> Decimal:
    return value.quantize(Decimal(places), rounding=ROUND_HALF_UP)


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
        t.values[key] = _round(t.values[key], "1") if key == "energy-kcal" else _round(t.values[key])
    return t


@dataclass
class MealGroup:
    key: str
    label: str
    entries: list
    totals: Totals


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
    """Breakfast before 11:00, lunch before 16:00, dinner before 21:30, else snacks."""
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
