"""Deterministic comparison of 2-3 products. No winner is designated.

* Nutrition is compared only within the same basis (per 100 g, per 100 ml, ...) and the
  same preparation state. Per-serving values are shown but never compared.
* Missing values are listed last and never treated as zero; zero is a real value.
* Nutri-Score grades from different algorithm versions are not put on one scale.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from django.utils.translation import gettext as _

from assessments.models import AssessmentSnapshot
from catalog.models import NutrientFact, Product
from catalog.nutrition import (
    BASIS_LABELS,
    NUTRIENT_BY_KEY,
    NUTRIENTS,
    PREPARATION_LABELS,
    basis_label,
)
from catalog.presenters import NOVA_LABELS, format_value, humanize_tag

MIN_PRODUCTS, MAX_PRODUCTS = 2, 3
# Same icons as the nutrient rows on the product page (Lucide).
CHART_ICONS = {
    "energy-kj": "flame",
    "energy-kcal": "flame",
    "fat": "droplet",
    "saturated-fat": "droplets",
    "carbohydrates": "wheat",
    "sugars": "candy",
    "fiber": "leaf",
    "proteins": "egg",
    "salt": "cuboid",
    "sodium": "cuboid",
}


class ComparisonError(ValueError):
    pass


@dataclass
class Cell:
    value: Decimal | None
    display: str
    state: str  # declared / not_on_label / unknown


@dataclass
class Row:
    nutrient: str
    label: str
    cells: list[Cell]


@dataclass
class Group:
    basis: str  # "per_100" (g, ml or unspecified, one per column) or "per_serving"
    preparation: str
    title: str
    comparable: bool
    rows: list[Row]
    missing_for: list[str]
    column_bases: list[str] = field(default_factory=list)  # basis label per product column
    mixed_bases: bool = False


@dataclass
class Comparison:
    products: list[Product]
    warnings: list[str] = field(default_factory=list)
    nutriscore: list[dict[str, Any]] = field(default_factory=list)
    nutriscore_note: str = ""
    nova: list[dict[str, Any]] = field(default_factory=list)
    groups: list[Group] = field(default_factory=list)
    differences: list[str] = field(default_factory=list)
    # Data for the comparison charts: one entry per basis shared by >= 2 products.
    charts: list[dict[str, Any]] = field(default_factory=list)


def _label(p: Product) -> str:
    return p.name or _("Unnamed product")


def _category_warnings(products: list[Product]) -> list[str]:
    warnings = []
    for p in products:
        if not p.categories:
            warnings.append(_("The category of “%s” is unknown.") % _label(p))
    with_cat = [p for p in products if p.categories]
    if len(with_cat) >= 2:
        leaves = {p.categories[-1] for p in with_cat}
        if len(leaves) > 1:
            shared = set.intersection(*(set(p.categories) for p in with_cat))
            names = ", ".join(f"“{_label(p)}”: {humanize_tag(p.categories[-1])}" for p in with_cat)
            if shared:
                warnings.append(_("These products are in different categories (%s).") % names)
            else:
                warnings.append(
                    _(
                        "These products have no category in common (%s). Comparing them may "
                        "not be meaningful."
                    )
                    % names
                )
    return warnings


def _sentence(
    nutrient: str, title: str, products: list[Product], cells: list[Cell], without_group: set[str]
) -> str | None:
    known = [(c.value, p) for c, p in zip(cells, products, strict=True) if c.value is not None]
    if len(known) < 2:
        return None
    label = NUTRIENT_BY_KEY[nutrient].label
    unit = NUTRIENT_BY_KEY[nutrient].unit
    if label.startswith("of which "):
        label = label.removeprefix("of which ")
    values = {v for v, _p in known}
    missing = [
        _label(p)
        for c, p in zip(cells, products, strict=True)
        if c.value is None and _label(p) not in without_group
    ]
    if len(values) == 1:
        text = _("%(n)s %(b)s: the same for %(p)s (%(v)s).") % {
            "n": label[:1].upper() + label[1:],
            "b": title,
            "p": " and ".join(f"“{_label(p)}”" for _v, p in known),
            "v": format_value(known[0][0], unit),
        }
    else:
        ordered = sorted(known, key=lambda vp: (-vp[0], _label(vp[1])))
        parts = ", ".join(f"“{_label(p)}” {format_value(v, unit)}" for v, p in ordered)
        diff = ordered[0][0] - ordered[-1][0]
        text = _("%(n)s %(b)s: %(parts)s (difference %(d)s).") % {
            "n": label[:1].upper() + label[1:],
            "b": title,
            "parts": parts,
            "d": format_value(diff, unit),
        }
    if missing:
        text += " " + _("Not available for %s.") % ", ".join(f"“{m}”" for m in missing)
    return text


def compare(products: list[Product]) -> Comparison:
    if not MIN_PRODUCTS <= len(products) <= MAX_PRODUCTS:
        raise ComparisonError(_("Choose 2 or 3 products to compare."))
    revision_ids = [p.current_revision_id for p in products]

    snapshots = defaultdict(dict)
    for s in AssessmentSnapshot.objects.filter(
        revision_id__in=revision_ids, origin="off_computed", indicator__in=("nutriscore", "nova")
    ):
        snapshots[s.revision_id][s.indicator] = s

    facts: dict[tuple[str, str], dict[Any, dict[str, Any]]] = defaultdict(lambda: defaultdict(dict))
    for f in NutrientFact.objects.filter(revision_id__in=revision_ids):
        facts[(f.basis, f.preparation)][f.revision_id][f.nutrient] = f
    return build_comparison(products, snapshots, facts)


def build_comparison(products: list[Any], snapshots: dict, facts: dict) -> Comparison:
    """Pure part of the comparison (no database access).

    ``products`` need ``name``, ``categories`` and ``current_revision_id`` (the key used in
    ``snapshots[key][indicator]`` and ``facts[(basis, preparation)][key][nutrient]``). Snapshot
    objects need ``data_status``, ``invalidated_by_local_change``, ``value`` and
    ``algorithm_version``; facts need ``value``, ``unit``, ``modifier`` and ``status``. Used
    for real products (loaded by ``compare``) and for the fictional demo products.
    """
    if not MIN_PRODUCTS <= len(products) <= MAX_PRODUCTS:
        raise ComparisonError(_("Choose 2 or 3 products to compare."))
    result = Comparison(products=products, warnings=_category_warnings(products))
    snapshots = defaultdict(dict, snapshots)

    versions = set()
    for p in products:
        s = snapshots[p.current_revision_id].get("nutriscore")
        usable = (
            s is not None
            and s.data_status in ("available", "conflicting")
            and not s.invalidated_by_local_change
        )
        grade = (s.value.get("grade") or "").upper() if usable else ""
        if usable and grade:
            versions.add(s.algorithm_version or "unknown")
        result.nutriscore.append(
            {
                "grade": grade,
                "version": s.algorithm_version if s else "",
                "status": "not_current"
                if s is not None and s.invalidated_by_local_change
                else (s.data_status if s else "missing_data"),
            }
        )
        n = snapshots[p.current_revision_id].get("nova")
        group = (n.value or {}).get("group") if n and not n.invalidated_by_local_change else None
        result.nova.append({"group": group, "label": NOVA_LABELS.get(group, "") if group else ""})
    if len(versions) > 1:
        result.nutriscore_note = _(
            "These grades were computed with different Nutri-Score algorithm versions (%s) "
            "and are not directly comparable."
        ) % ", ".join(sorted(versions))

    # One table per preparation for the "per 100" values: every column keeps its own basis
    # (g, ml or not stated). Per-serving values depend on portion size and stay apart.
    per_100 = ("per_100g", "per_100ml", "per_100_unspecified")
    preparations = sorted({prep for (_b, prep) in facts}, key=lambda prep: prep != "as_sold")
    for prep in preparations:
        column_basis: list[str | None] = []
        for p in products:
            have = [b for b in per_100 if p.current_revision_id in facts.get((b, prep), {})]
            column_basis.append(have[0] if have else None)
        if any(column_basis):
            _add_group(result, products, facts, prep, column_basis, comparable=True)
        if any(p.current_revision_id in facts.get(("per_serving", prep), {}) for p in products):
            _add_group(
                result, products, facts, prep, ["per_serving"] * len(products), comparable=False
            )
    return result


def _add_group(result, products, facts, prep, column_basis, *, comparable):
    rows = []
    for n in NUTRIENTS:
        cells = []
        for p, basis in zip(products, column_basis, strict=True):
            f = (
                facts.get((basis, prep), {}).get(p.current_revision_id, {}).get(n.key)
                if basis
                else None
            )
            if f is None:
                cells.append(Cell(None, "", "unknown"))
            elif f.status == "not_on_label":
                cells.append(Cell(None, "", "not_on_label"))
            else:
                cells.append(Cell(f.value, format_value(f.value, f.unit, f.modifier), "declared"))
        if any(c.state != "unknown" for c in cells):
            rows.append(Row(n.key, n.label, cells))
    present = {b for b in column_basis if b}
    missing_for = [
        _label(p)
        for p, b in zip(products, column_basis, strict=True)
        if not b or p.current_revision_id not in facts.get((b, prep), {})
    ]
    if comparable:
        title = BASIS_LABELS[next(iter(present))] if len(present) == 1 else _("Per 100 g / 100 ml")
    else:
        title = BASIS_LABELS["per_serving"]
    title = f"{title}, {PREPARATION_LABELS[prep]}"
    group = Group(
        "per_100" if comparable else "per_serving",
        prep,
        title,
        comparable,
        rows,
        missing_for,
        column_bases=[
            basis_label(b, p.current_revision.data if p.current_revision else None) if b else ""
            for p, b in zip(products, column_basis, strict=True)
        ],
        mixed_bases=comparable and len(present) > 1,
    )
    result.groups.append(group)
    if not comparable:
        return
    # Differences only between products whose values share the same basis.
    for basis in sorted(present):
        idx = [i for i, b in enumerate(column_basis) if b == basis]
        if len(idx) < 2:
            continue
        sub_products = [products[i] for i in idx]
        sub_title = f"{BASIS_LABELS[basis]}, {PREPARATION_LABELS[prep]}"
        panels = []
        for row in rows:
            cells = [row.cells[i] for i in idx]
            if sum(c.value is not None for c in cells) < 2:
                continue
            unit = NUTRIENT_BY_KEY[row.nutrient].unit
            label = row.label.removeprefix("of which ")
            known = [c.value for c in cells if c.value is not None]
            spread = max(known) - min(known)
            panels.append(
                {
                    "key": row.nutrient,
                    "icon": CHART_ICONS.get(row.nutrient, "circle-check"),
                    "label": label[:1].upper() + label[1:],
                    "unit": unit,
                    "values": [float(c.value) if c.value is not None else None for c in cells],
                    "display": [c.display or None for c in cells],
                    "difference": format_value(spread, unit) if spread else "",
                }
            )
        if panels:
            result.charts.append(
                {
                    "title": sub_title[:1].upper() + sub_title[1:],
                    # Colour follows the product's column in the comparison, never its rank.
                    "slots": idx,
                    "products": [_label(p) for p in sub_products],
                    "panels": panels,
                }
            )
        for row in rows:
            sentence = _sentence(
                row.nutrient, sub_title, sub_products, [row.cells[i] for i in idx], set(missing_for)
            )
            if sentence:
                result.differences.append(sentence)
