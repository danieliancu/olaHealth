"""Personal context from the app's own models (the default source, ``myola.context``).

Retrieval: the question picks the sections (keywords in English, Romanian and the other app
languages); a short profile is always there. When the question names no section, a compact
summary of everything is sent instead. The result is capped in size.

Never included: name, email, age, height, gender, health conditions and their notes, religious
or other free-text restriction notes. With an eating-disorder answer, nothing about calories,
targets or weight; with calories hidden, no kcal.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from typing import Any

from myola import preferences as P
from myola.models import Note, ProductMark
from myola.services import diary, lists, progress
from myola.services.memories import used_in_replies
from myola.services.profiles import derive_prefs, get_profile

MAX_CHARS = 6000
DIARY_DAYS = 7
WEIGHT_DAYS = 30
LIST_LIMIT = 15
NOTES_LIMIT = 10

# Which sections a question needs. Lower-case, any of the app's languages.
SECTIONS: dict[str, re.Pattern] = {
    "diary": re.compile(
        r"\b(eat|ate|eaten|eating|food|meal|breakfast|lunch|dinner|snack|calor|kcal|protein|"
        r"carb|fat|sugar|diary|today|yesterday|week|logged|m[aâ]nc|mas[aă]|mic dejun|pr[aâ]nz|"
        r"cin[aă]|gustare|protein|gr[aă]sim|carbohidra|zah[aă]r|jurnal|azi|ieri|s[aă]pt[aă]m|"
        r"com[ií]|comida|mang[eé]|repas|gegessen|essen|mangiat)",
        re.IGNORECASE,
    ),
    "water": re.compile(r"\b(water|drink|drank|hydrat|ap[aă]|b[aă]ut|agua|eau|wasser|acqua)"),
    "activity": re.compile(
        r"\b(activ|exercis|walk|run|sport|gym|workout|mi[sș]care|plimb|alerg|antrenament)",
        re.IGNORECASE,
    ),
    "weight": re.compile(
        r"\b(weigh|weight|kg|kilos?|greutate|c[aâ]nt[aă]r|kilogram|sl[aă]bi|[iî]ngr[aă][sș]|"
        r"peso|poids|gewicht)",
        re.IGNORECASE,
    ),
    "targets": re.compile(
        r"\b(target|goal|limit|aim|how much should|obiectiv|[tț]int|c[aâ]t ar trebui|"
        r"objetivo|objectif|ziel)",
        re.IGNORECASE,
    ),
    "lists": re.compile(
        r"\b(favou?rite|buy|bought|shopping|avoid|list|preferat|favorit|cump[aă]r|evit|"
        r"list[aă]|compr|achat|kaufen)",
        re.IGNORECASE,
    ),
    "scans": re.compile(
        r"\b(scan|checked|history|looked at|scanat|verificat|istoric|escane|scann)",
        re.IGNORECASE,
    ),
    "memories": re.compile(
        r"\b(remember|memor|know about me|[tț]ii minte|aminte[sș]te|[sș]tii despre mine|"
        r"recuerda|souviens|erinnerst)",
        re.IGNORECASE,
    ),
    "notes": re.compile(r"\b(note|notes|noti[tț]|not[aă]|nota|notiz)", re.IGNORECASE),
}
# Questions that ask for something to buy or eat: add products available where they live.
SUGGEST = re.compile(
    r"\b(recommend|suggest|alternative|what should i (eat|buy|have)|idea|instead|swap|"
    r"recomand|suger|alternativ|ce s[aă] (m[aâ]nc|cump[aă]r)|ce pot (m[aâ]nca|cump[aă]ra)|"
    r"[iî]n loc|recomiend|sugier|propose|empfiehl)",
    re.IGNORECASE,
)


def sections_for(question: str) -> set[str]:
    q = (question or "").lower()
    return {name for name, pattern in SECTIONS.items() if pattern.search(q)}


def wants_products(question: str) -> bool:
    return bool(SUGGEST.search((question or "").lower()))


def _num(value) -> int | float | None:
    if value is None:
        return None
    f = float(value)
    return int(f) if f.is_integer() else round(f, 1)


class DjangoContextSource:
    name = "django"

    # ---------- the user ----------
    def user_context(self, user, question: str, *, today: dt.date) -> dict[str, Any]:
        profile = get_profile(user)
        sensitive = profile.eating_disorder
        energy = profile.energy_visible and not sensitive
        wanted = sections_for(question)
        compact = not wanted  # nothing named: a little of everything
        if compact:
            wanted = {"diary", "water", "activity", "lists", "memories"}
        out: dict[str, Any] = {"today": today.isoformat(), "profile": self._profile(profile)}
        if sensitive:
            out["sensitive"] = True
        if "targets" in wanted or "diary" in wanted:
            targets = self._targets(profile, energy)
            if targets:
                out["targets"] = targets
        if "diary" in wanted:
            out["diary"] = self._diary(user, today, energy, compact=compact)
        if wanted & {"water", "activity"} or compact:
            out["water_and_activity"] = self._water_activity(user, today)
        if "weight" in wanted and profile.track_weight and not sensitive:
            out["weights"] = self._weights(user, today)
        if "lists" in wanted:
            out["lists"] = self._lists(user)
        if "scans" in wanted:
            out["recent_scans"] = [
                {"name": v.product.name or "Unnamed product", "date": v.created_at.date()}
                for v in lists.recent(user, scans_only=True, limit=10)
            ]
        if "memories" in wanted:
            out["memories"] = list(used_in_replies(user).values_list("text", flat=True))
        if "notes" in wanted and profile.use_notes:
            out["notes"] = [
                {"kind": n.kind, "text": n.text, "product": n.product.name if n.product else ""}
                for n in Note.objects.filter(user=user).select_related("product")[:NOTES_LIMIT]
            ]
        return _capped(out)

    def _profile(self, profile) -> dict[str, Any]:
        prefs = derive_prefs(profile)
        level = P.ACTIVITY_LEVELS.get(profile.activity_level)
        return {
            "goal": str(P.GOALS[profile.goal][0]) if profile.goal in P.GOALS else "",
            "diets": [str(P.DIETS[d][0]) for d in prefs["diets"]],
            "allergies": [str(P.ALLERGENS[a]) for a in prefs["allergens"]],
            "activity_level": str(level[0]) if level else "",
            "calories_shown": profile.energy_visible,
        }

    def _targets(self, profile, energy: bool) -> dict[str, int]:
        if not profile.show_targets:
            return {}
        t = profile.targets
        names = {"energy-kcal": "kcal", "proteins": "protein_g", "carbohydrates": "carbs_g",
                 "fat": "fat_g"}  # fmt: skip
        return {names[k]: v for k, v in t.items() if v and (energy or k != "energy-kcal")}

    def _diary(self, user, today: dt.date, energy: bool, *, compact: bool) -> dict[str, Any]:
        data = progress.series(user, today, DIARY_DAYS)
        days = []
        for row in data["days"]:
            if not row["logged"]:
                days.append({"date": row["date"], "logged": False})
                continue
            day = {"date": row["date"], "logged": True, "protein_g": row["proteins"],
                   "carbs_g": row["carbohydrates"], "fat_g": row["fat"]}  # fmt: skip
            if energy:
                day["kcal"] = row["energy"]
            days.append(day)
        out: dict[str, Any] = {"last_7_days": days}
        # The foods themselves: today and yesterday (all of the week when asked about it).
        for offset in range(1 if compact else 2):
            day = today - dt.timedelta(days=offset)
            groups, _totals = diary.day_diary(user, day)
            foods = []
            for g in groups:
                for e in g.entries:
                    item = {"meal": g.key, "name": e.name, "amount": f"{_num(e.amount)} {e.unit}"}
                    kcal = e.values.get("energy-kcal")
                    if energy and kcal is not None:
                        item["kcal"] = _num(round(kcal))
                    foods.append(item)
            out[f"foods_{day.isoformat()}"] = foods
        return out

    def _water_activity(self, user, today: dt.date) -> list[dict[str, Any]]:
        data = progress.series(user, today, DIARY_DAYS)
        return [
            {"date": r["date"], "water_ml": r["water"] or 0, "activity_min": r["activity"] or 0}
            for r in data["days"]
        ]

    def _weights(self, user, today: dt.date) -> list[dict[str, Any]]:
        data = progress.series(user, today, WEIGHT_DAYS)
        return [{"date": w.date, "kg": _num(w.kg)} for w in data["weights"]]

    def _lists(self, user) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for kind, key in ((ProductMark.Kind.FAVORITE, "favourites"),
                          (ProductMark.Kind.BUY_OFTEN, "buy_often"),
                          (ProductMark.Kind.AVOID, "avoid")):  # fmt: skip
            marks = (
                ProductMark.objects.filter(user=user, kind=kind)
                .select_related("product")
                .order_by("-created_at")[:LIST_LIMIT]
            )
            out[key] = [m.product.name or "Unnamed product" for m in marks]
        return out

    # ---------- products where they live ----------
    def available_products(self, user, *, query: str = "", limit: int = 6) -> list[dict]:
        from catalog.models import Product
        from catalog.services.search import SearchParams, search_products

        country = user_country(user)
        avoid = set(
            ProductMark.objects.filter(
                user=user, kind__in=(ProductMark.Kind.AVOID, ProductMark.Kind.HIDDEN)
            ).values_list("product_id", flat=True)
        )
        found = []
        terms = " ".join(w for w in re.findall(r"[^\W\d_]{4,}", query or "") if len(w) >= 4)
        if terms:
            found = list(search_products(SearchParams(q=terms[:100], country=country))[:40])
        if not found:
            # Nothing named: products in the categories they already like or buy.
            liked = ProductMark.objects.filter(
                user=user, kind__in=(ProductMark.Kind.FAVORITE, ProductMark.Kind.BUY_OFTEN)
            ).values_list("product__categories", flat=True)[:20]
            tags = {c[-1] for c in liked if c}
            if tags:
                qs = Product.objects.filter(
                    current_revision__isnull=False, listable=True, countries__contains=[country]
                ).filter(categories__overlap=list(tags))
                found = list(qs[:40])
        found = [p for p in found if p.pk not in avoid]
        # Highest Foodo Score first; products without one last.
        found.sort(key=lambda p: (-(p.foodo_value if p.foodo_value is not None else -1), p.name))
        return [
            {
                "name": p.name,
                "brand": p.brand,
                "quantity": p.quantity,
                "foodo_score": f"{p.foodo_value}/100" if p.foodo_value is not None else None,
                "nova": p.nova_group,
            }
            for p in found[:limit]
        ]


def user_country(user) -> str:
    """Where the user shops. The app is UK-only for now; the one place to change later."""
    return "en:united-kingdom"


def _capped(data: dict[str, Any]) -> dict[str, Any]:
    """Keep the payload under ``MAX_CHARS``: long lists are shortened, oldest parts first."""

    def size() -> int:
        return len(json.dumps(data, default=str, ensure_ascii=False))

    for key in ("notes", "recent_scans", "lists", "weights", "memories", "water_and_activity"):
        while size() > MAX_CHARS and isinstance(data.get(key), list) and data[key]:
            data[key] = data[key][:-1]
        if size() > MAX_CHARS and isinstance(data.get(key), dict):
            for k in data[key]:
                data[key][k] = data[key][k][: max(1, len(data[key][k]) // 2)]
    if size() > MAX_CHARS and "diary" in data:
        for k in [k for k in data["diary"] if k.startswith("foods_")]:
            data["diary"][k] = data["diary"][k][:15]
    return data
