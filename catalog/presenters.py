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
from catalog.models import Product, ProductImage, ReviewFlag
from catalog.nutrition import BASIS_LABELS, NUTRIENTS, PREPARATION_LABELS, to_decimal

INDICATOR_LABELS = {
    "nutriscore": "Nutri-Score",
    "nova": "NOVA group",
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
    "current": "Up to date with Open Food Facts",
    "stale": "Not refreshed recently",
    "refresh_failed": "Last refresh from Open Food Facts failed",
    "local_only": "Local data (not from Open Food Facts)",
}
NOVA_LABELS = {
    1: "Unprocessed or minimally processed foods",
    2: "Processed culinary ingredients",
    3: "Processed foods",
    4: "Ultra-processed food and drink products",
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
                    _("Open Food Facts also computed grade %(g)s with the %(v)s algorithm.")
                    % {"g": other.upper(), "v": version}
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
            return (
                _("NOVA group %(g)s – %(l)s") % {"g": group, "l": NOVA_LABELS[group]},
                [],
                str(group),
            )
        if s.data_status == "not_applicable":
            return _("NOVA does not apply to this product."), [], ""
        return _("Not enough data for a NOVA group."), [], ""
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
            return _("Open Food Facts identified no additives in the ingredient list."), [], ""
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
            text = _("Open Food Facts")
            if src.get("off_last_modified"):
                text += _(" (last modified %s)") % src["off_last_modified"][:10]
        elif kind == "contribution":
            text = _("Local correction, approved %s") % (src.get("approved_at") or "")[:10]
        elif kind == "demo":
            text = _("Demo data (fictional)")
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
    for ch in text:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        if ch in ",;" and depth == 0:
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
