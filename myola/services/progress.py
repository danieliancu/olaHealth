"""Progress over 7 or 30 days: energy and macros per day, water, activity and weight.

A day without entries is ``None`` ("No entries"), never zero. Charts are server-rendered SVG:
``bar_chart`` turns one series into bar geometry (attributes only, no inline styles: CSP).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from django.db.models import Sum
from django.utils.formats import date_format

from myola.models import LIMITS, ActivityEntry, DiaryEntry, WaterEntry, WeightEntry
from myola.services.diary import NUTRIENTS, entry_values

RANGES = (7, 30)
CHART_WIDTH = 300
CHART_HEIGHT = 120
MIN_BAR = 3


def parse_range(value: str | None) -> int:
    try:
        span = int(value or "")
    except ValueError:
        return RANGES[0]
    return span if span in RANGES else RANGES[0]


def days_back(end: dt.date, span: int) -> list[dt.date]:
    return [end - dt.timedelta(days=i) for i in range(span - 1, -1, -1)]


def _average(values: list) -> int | None:
    known = [v for v in values if v is not None]
    return round(sum(known) / len(known)) if known else None


def series(user, end: dt.date, span: int) -> dict:
    """Per-day values for ``span`` days ending on ``end``.

    Diary values (energy and macros) are ``None`` on days without diary entries; water and
    activity are ``None`` on days without their own entries. ``partial[key]`` marks a day where
    at least one food had an unknown value for that nutrient (not counted in the total).
    """
    days = days_back(end, span)
    start = days[0]
    totals = {d: dict.fromkeys(NUTRIENTS, Decimal(0)) for d in days}
    partial = {d: dict.fromkeys(NUTRIENTS, False) for d in days}
    logged: set[dt.date] = set()
    for e in DiaryEntry.objects.filter(user=user, date__range=(start, end)):
        logged.add(e.date)
        for key, value in entry_values(e).items():
            if value is None:
                partial[e.date][key] = True
            else:
                totals[e.date][key] += value
    water = {
        r["date"]: r["s"]
        for r in WaterEntry.objects.filter(user=user, date__range=(start, end))
        .values("date")
        .annotate(s=Sum("ml"))
    }
    activity = {
        r["date"]: r["s"]
        for r in ActivityEntry.objects.filter(user=user, date__range=(start, end))
        .values("date")
        .annotate(s=Sum("minutes"))
    }
    weights = list(WeightEntry.objects.filter(user=user, date__range=(start, end)).order_by("date"))
    rows = []
    for d in days:
        is_logged = d in logged
        rows.append(
            {
                "date": d,
                "logged": is_logged,
                "energy": round(totals[d]["energy-kcal"]) if is_logged else None,
                "proteins": round(totals[d]["proteins"]) if is_logged else None,
                "carbohydrates": round(totals[d]["carbohydrates"]) if is_logged else None,
                "fat": round(totals[d]["fat"]) if is_logged else None,
                "partial": {
                    "energy": partial[d]["energy-kcal"],
                    "proteins": partial[d]["proteins"],
                    "carbohydrates": partial[d]["carbohydrates"],
                    "fat": partial[d]["fat"],
                },
                "water": water.get(d),
                "activity": activity.get(d),
            }
        )
    return {
        "days": rows,
        "span": span,
        "end": end,
        "logged_days": len(logged),
        "avg_energy": _average([r["energy"] for r in rows]),
        "avg_water": _average([r["water"] for r in rows]),
        "total_activity": sum(r["activity"] or 0 for r in rows),
        "weights": weights,
    }


def _fmt(value) -> str:
    return f"{value:,}".replace(",", " ") if isinstance(value, int) else str(value)


def bar_chart(data: dict, key: str, unit: str) -> dict:
    """Bar geometry for one series of ``series()``: a fixed viewBox, one slot per day."""
    rows = data["days"]
    span = data["span"]
    values = [r[key] for r in rows]
    known = [v for v in values if v is not None]
    top = max([*known, 1])
    slot = CHART_WIDTH / span
    bar_w = min(slot * 0.7, 30)
    usable = CHART_HEIGHT - 4
    bars = []
    any_partial = False
    for i, (row, value) in enumerate(zip(rows, values, strict=True)):
        x = round(i * slot + (slot - bar_w) / 2, 2)
        is_partial = bool(row.get("partial", {}).get(key)) and value is not None
        any_partial = any_partial or is_partial
        h = MIN_BAR if value is None else max(MIN_BAR, round(value / top * usable, 2))
        d = row["date"]
        if span == 7:
            label = date_format(d, "D")
        else:
            label = str(d.day) if i % 5 == 0 or i == span - 1 else ""
        bars.append(
            {
                "x": x,
                "y": round(CHART_HEIGHT - h, 2),
                "width": round(bar_w, 2),
                "height": h,
                "empty": value is None,
                "partial": is_partial,
                "value": value,
                "text": "" if value is None else f"{_fmt(value)} {unit}",
                "date": d,
                "label": label,
            }
        )
    return {
        "key": key,
        "unit": unit,
        "bars": bars,
        "average": _average(values),
        "any_partial": any_partial,
        "width": CHART_WIDTH,
        "height": CHART_HEIGHT,
        "span": span,
    }


def save_weight(user, day: dt.date, kg: Decimal) -> WeightEntry:
    """One weight per day: a second entry the same day replaces the first."""
    entry, _ = WeightEntry.objects.update_or_create(user=user, date=day, defaults={"kg": kg})
    extra = WeightEntry.objects.filter(user=user).order_by("-date")[LIMITS["weights"] :]
    if extra:
        WeightEntry.objects.filter(pk__in=[w.pk for w in extra]).delete()
    return entry
