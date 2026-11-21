"""Turns a product and its current revision into plain structures for templates and API.

All wording is deterministic. No health verdicts, no combined score.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from django.utils.translation import gettext as _

from assessments.models import AssessmentSnapshot
from catalog import explanations
from catalog.models import Product, ProductImage, ReviewFlag
from catalog.nutrition import BASIS_LABELS, NUTRIENTS, PREPARATION_LABELS, to_decimal

INDICATOR_LABELS = {
    "nutriscore": "Nutri-Score",
    "nova": "Processing",
    "nutrient_levels": "Nutrient levels",
    "ingredients_analysis": "Ingredients analysis",
    "additives": "Additives identified",
}
DATA_STATUS_LABELS = {
    "available": "Available",
    "missing_data": "Missing data",
    "not_applicable": "Not applicable",
    "conflicting": "Conflicting",
}
SYNC_LABELS = {
    "current": "Up to date",
    "stale": "Not refreshed recently",
    "refresh_failed": "Last refresh failed",
    "local_only": "Local data",
}
NOVA_LABELS = {
    1: "Unprocessed or minimally processed foods",
    2: "Processed culinary ingredients",
    3: "Processed foods",
    4: "Ultra-processed food and drink products",
}
NOVA_SHORT = {
    1: "Unprocessed or minimally processed",
    2: "Processed culinary ingredient",
    3: "Processed food",
    4: "Ultra-processed",
}
LEVEL_NUTRIENTS = {
    "fat": "Fat",
    "saturated-fat": "Saturated fat",
    "sugars": "Sugars",
    "salt": "Salt",
}
FIELD_LABELS = {
    "name": "Name",
    "generic_name": "Generic name",
    "brand": "Brand",
    "quantity": "Quantity",
    "serving_size": "Serving size",
    "categories": "Categories",
    "countries": "Countries where sold",
    "ingredients": "Ingredients",
    "allergens": "Allergens",
    "traces": "Traces (may contain)",
    "nutrition": "Nutrition facts",
    "packaging_nutriscore": "Nutri-Score printed on the package",
}


def humanize_tag(tag: str) -> str:
    text = tag.split(":", 1)[1] if ":" in tag[:4] else tag
    return text.replace("-", " ").strip().capitalize()


def additive_label(tag: str) -> str:
    text = tag.split(":", 1)[-1]
    return text.upper() if text[:1].lower() == "e" and text[1:2].isdigit() else humanize_tag(tag)


TWO_PLACES = Decimal("0.01")


def format_number(number: Decimal) -> str:
    """At most two decimals for display. Stored values are never rounded."""
    rounded = number.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
    if rounded == 0 and number != 0:
        return "< 0.01"  # a tiny value must not read as zero
    text = f"{rounded:f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def format_value(value: Decimal | str | None, unit: str, modifier: str = "") -> str:
    if value is None:
        return ""
    number = to_decimal(value)
    if number is None:
        return ""
    text = format_number(number)
    if text.startswith("<") and modifier:
        modifier = ""
    return f"{modifier}{text} {unit}".strip()


@dataclass
class IndicatorView:
    key: str
    label: str
    data_status: str
    data_status_label: str
    sync: str
    sync_label: str
    not_current: bool
    headline: str
    details: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    version: str = ""
    badge: str = ""
    computed_at: Any = None


def _indicator_headline(s: AssessmentSnapshot) -> tuple[str, list[str], str]:
    v = s.value or {}
    if s.indicator == "nutriscore":
        grade = (v.get("grade") or "").upper()
        if s.data_status in ("available", "conflicting") and grade:
            details = []
            if s.algorithm_version:
                details.append(_("Algorithm version: %s") % s.algorithm_version)
            for version, other in (v.get("other_versions") or {}).items():
                details.append(
                    _("Grade %(g)s with the %(v)s algorithm.") % {"g": other.upper(), "v": version}
                )
            if v.get("packaging_grade"):
                details.append(_("Grade printed on the package: %s") % v["packaging_grade"].upper())
            return _("Grade %s") % grade, details, grade.lower()
        if s.data_status == "not_applicable":
            return _("Nutri-Score does not apply to this product category."), [], ""
        return _("Not enough data for a Nutri-Score."), [], ""
    if s.indicator == "nova":
        group = v.get("group")
        if group:
            return NOVA_LABELS[group], [], str(group)
        if s.data_status == "not_applicable":
            return _("The processing level does not apply to this product."), [], ""
        return _("Not enough data to say how processed it is."), [], ""
    if s.indicator == "nutrient_levels":
        levels = v.get("levels") or {}
        if levels:
            details = [
                f"{LEVEL_NUTRIENTS.get(k, humanize_tag(k))}: {lvl}" for k, lvl in levels.items()
            ]
            return _("Levels per 100 g / 100 ml"), details, ""
        return _("No nutrient levels available."), [], ""
    if s.indicator == "ingredients_analysis":
        tags = v.get("tags") or []
        if s.data_status == "available":
            return _("Based on the ingredient list"), [humanize_tag(t) for t in tags], ""
        return _("The ingredient list could not be analysed."), [], ""
    if s.indicator == "additives":
        tags = v.get("tags")
        if s.data_status != "available":
            return _("No ingredient list to identify additives from."), [], ""
        if not tags:
            return _("No additives identified in the ingredient list."), [], ""
        return (
            _("%d identified in the ingredient list") % len(tags),
            [additive_label(t) for t in tags],
            "",
        )
    return "", [], ""


def indicator_views(product: Product, snapshots: list[AssessmentSnapshot]) -> list[IndicatorView]:
    by_key = {s.indicator: s for s in snapshots if s.origin == "off_computed"}
    sync = product.sync_label
    views = []
    for key, label in INDICATOR_LABELS.items():
        s = by_key.get(key)
        if s is None:
            views.append(
                IndicatorView(
                    key,
                    label,
                    "missing_data",
                    DATA_STATUS_LABELS["missing_data"],
                    sync,
                    SYNC_LABELS[sync],
                    False,
                    _("No data from the source."),
                )
            )
            continue
        headline, details, badge = _indicator_headline(s)
        views.append(
            IndicatorView(
                key=key,
                label=label,
                data_status=s.data_status,
                data_status_label=DATA_STATUS_LABELS[s.data_status],
                sync=sync,
                sync_label=SYNC_LABELS[sync],
                not_current=s.invalidated_by_local_change,
                headline=headline,
                details=details,
                warnings=list(s.warnings),
                version=s.algorithm_version,
                badge="" if s.invalidated_by_local_change else badge,
                computed_at=s.computed_at,
            )
        )
    return views


@dataclass
class NutritionGroup:
    basis: str
    preparation: str
    title: str
    rows: list[dict[str, str]]


def nutrition_groups(data: dict[str, Any]) -> list[NutritionGroup]:
    facts = (data.get("nutrition") or {}).get("facts") or []
    keys = sorted({(f["basis"], f["preparation"]) for f in facts}, key=lambda k: (k[1], k[0]))
    groups = []
    for basis, prep in keys:
        group_facts = {
            f["nutrient"]: f for f in facts if f["basis"] == basis and f["preparation"] == prep
        }
        # Only values that are actually available are listed.
        rows = [
            {
                "label": n.label,
                "value": format_value(f["value"], f["unit"], f.get("modifier") or ""),
                "state": "declared",
                "sub": n.label.startswith("of which"),
            }
            for n in NUTRIENTS
            if (f := group_facts.get(n.key)) is not None and f["status"] == "declared"
        ]
        if not rows:
            continue
        title = f"{BASIS_LABELS[basis]}, {PREPARATION_LABELS[prep]}"
        groups.append(NutritionGroup(basis, prep, title, rows))
    return groups


def gaps(product: Product, data: dict[str, Any], indicators: list[IndicatorView]) -> list[str]:
    """Only things that need attention (conflicts, values no longer current).
    Missing information is simply not shown."""
    items = []
    for ind in indicators:
        if ind.not_current:
            items.append(
                _("%s was computed before a local correction and may not be current.") % ind.label
            )
        if ind.data_status == "conflicting":
            items.append(_("%s: sources disagree.") % ind.label)
    return items


def open_flags(product: Product) -> list[ReviewFlag]:
    return list(product.flags.filter(status=ReviewFlag.Status.OPEN))


def quality_pills(
    product: Product, data: dict[str, Any], flags: list[ReviewFlag]
) -> list[dict[str, str]]:
    """Compact data-quality states, only when the stored data supports them.

    "Up to date with the source", "checked by a moderator" and "complete" are different things;
    none of them is implied by the absence of a pill.
    """
    from catalog import quality
    from contributions.models import Contribution

    pills = []
    issues = set(product.quality_issues or [])
    if issues & {quality.NAME_MISSING, quality.NAME_INCOMPLETE}:
        pills.append({"key": "name_incomplete", "label": _("Name incomplete")})
    if quality.CATEGORY_MISSING in issues:
        pills.append({"key": "category_missing", "label": _("Category unclear")})
    if quality.PHOTO_MISSING in issues:
        pills.append({"key": "photo_missing", "label": _("No photo")})
    if not product.listable:
        pills.append({"key": "not_listed", "label": _("Not shown in search")})
    if not ((data.get("ingredients") or {}).get("text") or "").strip():
        pills.append({"key": "ingredients_missing", "label": _("Ingredients missing")})
    nutrition = data.get("nutrition") or {}
    if nutrition.get("status") != "not_on_package" and not nutrition.get("facts"):
        pills.append({"key": "nutrition_missing", "label": _("Nutrition missing")})
    if (data.get("allergens") or {}).get("status", "unknown") == "unknown":
        pills.append({"key": "allergens_unknown", "label": _("Allergen information unavailable")})
    if product.sync_label in ("stale", "refresh_failed"):
        pills.append({"key": "needs_refresh", "label": _("Needs refresh")})
    revision = product.current_revision
    if revision and any(
        (src or {}).get("source") == "contribution" for src in revision.field_sources.values()
    ):
        pills.append({"key": "local_contribution", "label": _("Local contribution")})
    waiting = product.contributions.filter(
        status__in=(Contribution.Status.SUBMITTED, Contribution.Status.CONFLICT)
    ).exists()
    if waiting or flags:
        pills.append({"key": "awaiting_moderation", "label": _("Awaiting moderation")})
    return pills


def section_list(section: dict[str, Any] | None) -> dict[str, Any]:
    section = section or {}
    status = section.get("status") or "unknown"
    return {"status": status, "items": [humanize_tag(i) for i in section.get("items") or []]}


def sources_view(product: Product) -> list[dict[str, str]]:
    revision = product.current_revision
    if revision is None:
        return []
    rows = []
    for key, label in FIELD_LABELS.items():
        src = revision.field_sources.get(key) or {}
        kind = src.get("source", "")
        if kind == "off":
            text = _("Product database")
            if src.get("off_last_modified"):
                text += _(" (last modified %s)") % src["off_last_modified"][:10]
        elif kind == "contribution":
            text = _("Local correction, approved %s") % (src.get("approved_at") or "")[:10]
        else:
            continue  # nothing recorded for this field: not shown
        if not _has_value(revision.data.get(key)):
            continue
        rows.append({"field": label, "source": text})
    return rows


def public_images(product: Product) -> list[ProductImage]:
    return [img for img in product.images.all() if img.is_active]


def _has_value(value: Any) -> bool:
    if isinstance(value, dict):
        if value.get("status") in ("unknown",):
            return False
        return any(_has_value(v) for k, v in value.items() if k != "lang")
    return bool(value)


AVAILABLE_STATES = ("available", "conflicting")


def available_indicators(indicators: list[IndicatorView]) -> list[IndicatorView]:
    return [i for i in indicators if i.data_status in AVAILABLE_STATES]


_PERCENT = re.compile(r"(\d+(?:[.,]\d+)?)\s?%")


@dataclass
class Ingredient:
    parts: list[tuple[str, bool]]  # (text, is_allergen) – rendered with escaping
    detail: str  # text in parentheses (sub-ingredients), shown muted
    percent: str


def _split_top_level(text: str) -> list[str]:
    items, depth, current = [], 0, []
    for i, ch in enumerate(text):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        # A comma between two digits is a decimal comma ("99,7%"), not a separator.
        decimal_comma = (
            ch == "," and 0 < i < len(text) - 1 and text[i - 1].isdigit() and text[i + 1].isdigit()
        )
        if ch in ",;" and depth == 0 and not decimal_comma:
            items.append("".join(current))
            current = []
        else:
            current.append(ch)
    items.append("".join(current))
    return [i.strip().rstrip(".").strip() for i in items if i.strip().rstrip(".").strip()]


def split_ingredients(text: str | None) -> list[Ingredient]:
    """Display helper: top-level ingredients, percentages, and allergens marked by the
    source (OFF marks them as _word_). Nothing is inferred."""
    if not text:
        return []
    result = []
    for item in _split_top_level(text):
        main, detail = item, ""
        start = item.find("(")
        if start > 0 and item.endswith(")"):
            main, detail = item[:start].strip(), item[start + 1 : -1].strip()
        percent = ""
        m = _PERCENT.search(main)
        if m:
            percent = m.group(0).replace(" ", "")
            main = (main[: m.start()] + main[m.end() :]).strip(" :-")
        parts = [(seg, i % 2 == 1) for i, seg in enumerate(main.split("_")) if seg]
        if not parts:
            continue
        result.append(Ingredient(parts, detail.replace("_", ""), percent))
    return result


# ---------- Nutrient rows (product page) ----------
# UK FSA front-of-pack thresholds, which Open Food Facts uses for its nutrient levels.
# (low upper bound, high lower bound), grams per 100 g / per 100 ml.
FSA_THRESHOLDS = {
    "solid": {"fat": (3, 17.5), "saturated-fat": (1.5, 5), "sugars": (5, 22.5), "salt": (0.3, 1.5)},
    "liquid": {
        "fat": (1.5, 8.75),
        "saturated-fat": (0.75, 2.5),
        "sugars": (2.5, 11.25),
        "salt": (0.3, 0.75),
    },
}
ROW_META = {
    # key: (label, icon, label used in "Low ..." sentences)
    "energy-kcal": ("Energy", "flame", ""),
    "fat": ("Fat", "droplet", "fat"),
    "saturated-fat": ("Saturates", "droplets", "saturates"),
    "carbohydrates": ("Carbo\u00adhydrate", "wheat", ""),  # soft hyphen: splits when narrow
    "sugars": ("Sugars", "candy", "sugars"),
    "fiber": ("Fibre", "leaf", ""),
    "proteins": ("Protein", "egg", ""),
    "salt": ("Salt", "cuboid", "salt"),
}
LEVEL_WORDS = {"low": "Low", "moderate": "Moderate", "high": "High"}
GAUGE_WIDTH = 300


@dataclass
class Gauge:
    segments: list[tuple[float, float, str]]  # x, width, level
    marker_x: float
    ticks: list[tuple[float, str, str]]  # x, label, text-anchor
    low: str
    high: str

    @property
    def marker_points(self) -> str:
        x = self.marker_x
        return f"{x - 6},1 {x + 6},1 {x},10"


@dataclass
class NutrientRow:
    key: str
    label: str
    icon: str
    value: str
    level: str = ""  # low / moderate / high (from Open Food Facts) or ""
    level_text: str = ""
    gauge: Gauge | None = None
    explain: str = ""  # short explanation shown on tap (catalog/explanations.py)


def _gauge(value: Decimal, low: float, high: float) -> Gauge:
    top = max(high * 2, float(value) * 1.1)
    top = min(top, high * 4)
    scale = GAUGE_WIDTH / top

    def x(v: float) -> float:
        return round(min(v, top) * scale, 1)

    segments = [
        (0.0, x(low), "low"),
        (x(low), round(x(high) - x(low), 1), "moderate"),
        (x(high), round(GAUGE_WIDTH - x(high), 1), "high"),
    ]
    ticks = [
        (0.0, "0", "start"),
        (x(low), format_number(Decimal(str(low))), "middle"),
        (x(high), format_number(Decimal(str(high))), "middle"),
    ]
    return Gauge(
        segments,
        x(float(value)),
        ticks,
        format_number(Decimal(str(low))),
        format_number(Decimal(str(high))),
    )


def nutrient_rows(
    data: dict[str, Any], levels_snapshot: AssessmentSnapshot | None
) -> tuple[list[NutrientRow], str]:
    """Rows for the per-100 as-sold values that are available, with OFF levels if any."""
    facts = (data.get("nutrition") or {}).get("facts") or []
    group = [
        f
        for f in facts
        if f["preparation"] == "as_sold"
        and f["basis"] != "per_serving"
        and f["status"] == "declared"
    ]
    if not group:
        return [], ""
    basis = group[0]["basis"]
    by_key = {f["nutrient"]: f for f in group if f["basis"] == basis}
    levels = {}
    if (
        levels_snapshot is not None
        and levels_snapshot.data_status == "available"
        and not levels_snapshot.invalidated_by_local_change
    ):
        levels = (levels_snapshot.value or {}).get("levels") or {}
    if "energy-kcal" not in by_key and "energy-kj" in by_key:
        by_key["energy-kcal"] = by_key["energy-kj"]  # show kJ when kcal is not declared
    thresholds = FSA_THRESHOLDS["liquid" if basis == "per_100ml" else "solid"]
    rows = []
    for key, (label, icon, word) in ROW_META.items():
        f = by_key.get(key)
        if f is None:
            continue
        row = NutrientRow(
            key,
            label,
            icon,
            format_value(f["value"], f["unit"], f.get("modifier") or ""),
            explain=explanations.nutrient(f["nutrient"], basis, by_key),
        )
        level = levels.get(key)
        if level in LEVEL_WORDS and key in thresholds:
            row.level = level
            row.level_text = f"{LEVEL_WORDS[level]} {word}"
            value = to_decimal(f["value"])
            if value is not None:
                row.gauge = _gauge(value, *thresholds[key])
        rows.append(row)
    # Levelled nutrients first (as on the label: fat, saturates, sugars, salt), then the rest.
    rows.sort(key=lambda r: (r.level == "", list(ROW_META).index(r.key)))
    return rows, BASIS_LABELS[basis]
