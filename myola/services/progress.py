"""Progress over 7 or 30 days: energy and macros per day, water, activity and weight."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from django.db.models import Sum

from myola.models import ActivityEntry, DiaryEntry, WaterEntry, WeightEntry
from myola.services.diary import entry_values

RANGES = (7, 30)


def days_back(end: dt.date, span: int) -> list[dt.date]:
    return [end - dt.timedelta(days=i) for i in range(span - 1, -1, -1)]


def series(user, end: dt.date, span: int) -> dict:
    days = days_back(end, span)
    start = days[0]
    energy = dict.fromkeys(days, Decimal(0))
    macros = {d: {"proteins": Decimal(0), "carbohydrates": Decimal(0), "fat": Decimal(0)} for d in days}
    partial = dict.fromkeys(days, False)
    logged = set()
    for e in DiaryEntry.objects.filter(user=user, date__range=(start, end)):
        logged.add(e.date)
        values = entry_values(e)
        if values["energy-kcal"] is None:
            partial[e.date] = True
        else:
            energy[e.date] += values["energy-kcal"]
        for k in macros[e.date]:
            if values[k] is not None:
                macros[e.date][k] += values[k]
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
    rows = [
        {
            "date": d,
            "logged": d in logged,
            "energy": int(energy[d]),
            "partial": partial[d],
            "proteins": round(macros[d]["proteins"]),
            "carbohydrates": round(macros[d]["carbohydrates"]),
            "fat": round(macros[d]["fat"]),
            "water": water.get(d, 0),
            "activity": activity.get(d, 0),
        }
        for d in days
    ]
    logged_rows = [r for r in rows if r["logged"]]
    return {
        "days": rows,
        "span": span,
        "logged_days": len(logged_rows),
        "avg_energy": round(sum(r["energy"] for r in logged_rows) / len(logged_rows))
        if logged_rows
        else None,
        "avg_water": round(sum(r["water"] for r in rows) / span),
        "total_activity": sum(r["activity"] for r in rows),
        "weights": weights,
        "max_energy": max([r["energy"] for r in rows] + [1]),
        "max_water": max([r["water"] for r in rows] + [1]),
        "max_activity": max([r["activity"] for r in rows] + [1]),
    }
