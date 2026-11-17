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
from catalog.nutrition import BASIS_LABELS, NUTRIENT_BY_KEY, NUTRIENTS, PREPARATION_LABELS
from catalog.presenters import NOVA_LABELS, format_value, humanize_tag

MIN_PRODUCTS, MAX_PRODUCTS = 2, 3


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
    basis: str
    preparation: str
    title: str
    comparable: bool
    rows: list[Row]
    missing_for: list[str]


@dataclass
class Comparison:
    products: list[Product]
    warnings: list[str] = field(default_factory=list)
    nutriscore: list[dict[str, Any]] = field(default_factory=list)
    nutriscore_note: str = ""
    nova: list[dict[str, Any]] = field(default_factory=list)
    groups: list[Group] = field(default_factory=list)
    differences: list[str] = field(default_factory=list)


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
            "n": label.capitalize(),
            "b": title,
            "p": " and ".join(f"“{_label(p)}”" for _v, p in known),
            "v": format_value(known[0][0], unit),
        }
    else:
        ordered = sorted(known, key=lambda vp: (-vp[0], _label(vp[1])))
        parts = ", ".join(f"“{_label(p)}” {format_value(v, unit)}" for v, p in ordered)
        diff = ordered[0][0] - ordered[-1][0]
        text = _("%(n)s %(b)s: %(parts)s (difference %(d)s).") % {
            "n": label.capitalize(),
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
    result = Comparison(products=products, warnings=_category_warnings(products))
    revision_ids = [p.current_revision_id for p in products]

    snapshots = defaultdict(dict)
    for s in AssessmentSnapshot.objects.filter(
        revision_id__in=revision_ids, origin="off_computed", indicator__in=("nutriscore", "nova")
    ):
        snapshots[s.revision_id][s.indicator] = s

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

    facts: dict[tuple[str, str], dict[int, dict[str, NutrientFact]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    for f in NutrientFact.objects.filter(revision_id__in=revision_ids):
        facts[(f.basis, f.preparation)][f.revision_id][f.nutrient] = f

    order = {b: i for i, b in enumerate(BASIS_LABELS)}
    for basis, prep in sorted(facts, key=lambda k: (k[1] != "as_sold", order[k[0]])):
        per_rev = facts[(basis, prep)]
        title = f"{BASIS_LABELS[basis]}, {PREPARATION_LABELS[prep]}"
        comparable = basis != "per_serving"
        rows = []
        for n in NUTRIENTS:
            cells = []
            for p in products:
                f = per_rev.get(p.current_revision_id, {}).get(n.key)
                if f is None:
                    cells.append(Cell(None, "", "unknown"))
                elif f.status == "not_on_label":
                    cells.append(Cell(None, "", "not_on_label"))
                else:
                    cells.append(
                        Cell(f.value, format_value(f.value, f.unit, f.modifier), "declared")
                    )
            if any(c.state != "unknown" for c in cells):
                rows.append(Row(n.key, n.label, cells))
        missing_for = [_label(p) for p in products if p.current_revision_id not in per_rev]
        group = Group(basis, prep, title, comparable, rows, missing_for)
        result.groups.append(group)
        if comparable:
            for row in rows:
                sentence = _sentence(row.nutrient, title, products, row.cells, set(missing_for))
                if sentence:
                    result.differences.append(sentence)
    return result
