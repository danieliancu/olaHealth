"""Interpretation of Open Food Facts indicators. No score is computed here.

Every indicator is taken as published by OFF, with its algorithm version when OFF
provides one. Each indicator declares which canonical data fields it depends on; when a
published revision's values for those fields differ from the values OFF used, the
indicator is shown as *not current* (see :func:`inputs_hash`).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

AVAILABLE = "available"
MISSING = "missing_data"
NOT_APPLICABLE = "not_applicable"
CONFLICTING = "conflicting"

# Indicator -> canonical fields whose change may make it stale.
# Nutri-Score depends on ingredients too: OFF estimates fruit/vegetable/legume content
# from the ingredient list when the label does not state it.
DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "nutriscore": ("nutrition", "categories", "ingredients"),
    "nova": ("ingredients", "categories"),
    "nutrient_levels": ("nutrition",),
    "ingredients_analysis": ("ingredients",),
    "additives": ("ingredients",),
}
INDICATORS = tuple(DEPENDENCIES)

# Known OFF ``misc_tags`` that qualify a Nutri-Score; anything else nutriscore-related
# is still shown, as the raw tag.
NUTRISCORE_WARNINGS = {
    "en:nutrition-fruits-vegetables-nuts-estimate-from-ingredients": (
        "The amount of fruits, vegetables and legumes is not on the label; Open Food Facts "
        "estimated it from the ingredient list."
    ),
    "en:nutrition-fruits-vegetables-nuts-from-category": (
        "The amount of fruits, vegetables and legumes is not on the label; Open Food Facts "
        "estimated it from the product category."
    ),
    "en:nutrition-no-fiber": "Fibre is not on the label; the score was computed without it.",
    "en:nutrition-no-fruits-vegetables-nuts": (
        "The amount of fruits, vegetables and legumes is unknown."
    ),
    "en:nutriscore-missing-category": "The product category is missing.",
    "en:nutriscore-missing-nutrition-data": "Nutrition facts are missing.",
    "en:nutriscore-missing-nutrition-data-details": "Some nutrition facts are missing.",
    "en:nutrition-not-enough-data-to-compute-nutrition-score": (
        "There is not enough data to compute the Nutri-Score."
    ),
}


@dataclass(frozen=True)
class IndicatorSpec:
    indicator: str
    data_status: str
    value: dict[str, Any] = field(default_factory=dict)
    algorithm_version: str = ""
    warnings: tuple[str, ...] = ()
    origin: str = "off_computed"


def inputs_hash(indicator: str, data: dict[str, Any]) -> str:
    subset = {name: data.get(name) for name in DEPENDENCIES[indicator]}
    blob = json.dumps(subset, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


def _str_list(value: Any) -> list[str]:
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def _nutriscore(product: dict[str, Any]) -> IndicatorSpec:
    grade = str(product.get("nutriscore_grade") or "").lower().strip()
    version = str(product.get("nutriscore_version") or "").strip()
    per_version = product.get("nutriscore") if isinstance(product.get("nutriscore"), dict) else {}
    misc = _str_list(product.get("misc_tags"))
    warnings = [NUTRISCORE_WARNINGS[t] for t in misc if t in NUTRISCORE_WARNINGS]
    warnings += [
        f"Open Food Facts note: {t.removeprefix('en:')}"
        for t in misc
        if t.startswith("en:nutriscore-")
        and t not in NUTRISCORE_WARNINGS
        and t not in ("en:nutriscore-computed", "en:nutriscore-not-computed")
        and not t.startswith(("en:nutriscore-2021-", "en:nutriscore-2023-"))
    ]
    other_versions = {
        str(v): str(d.get("grade")).lower()
        for v, d in sorted(per_version.items())
        if isinstance(d, dict)
        and str(d.get("grade") or "").lower() in ("a", "b", "c", "d", "e")
        and str(v) != version
    }
    value: dict[str, Any] = {"grade": None, "other_versions": other_versions}
    if grade in ("a", "b", "c", "d", "e"):
        value["grade"] = grade
        score = product.get("nutriscore_score")
        if isinstance(score, int | float) and not isinstance(score, bool):
            value["score"] = score
        return IndicatorSpec("nutriscore", AVAILABLE, value, version, tuple(warnings))
    if grade == "not-applicable":
        return IndicatorSpec("nutriscore", NOT_APPLICABLE, value, version, tuple(warnings))
    return IndicatorSpec("nutriscore", MISSING, value, version, tuple(warnings))


def _nova(product: dict[str, Any]) -> IndicatorSpec:
    group = product.get("nova_group")
    tags = _str_list(product.get("nova_groups_tags"))
    if isinstance(group, str) and group.isdigit():
        group = int(group)
    if isinstance(group, int) and not isinstance(group, bool) and 1 <= group <= 4:
        return IndicatorSpec("nova", AVAILABLE, {"group": group})
    if any(t.endswith("not-applicable") for t in tags):
        return IndicatorSpec("nova", NOT_APPLICABLE)
    return IndicatorSpec("nova", MISSING)


def _nutrient_levels(product: dict[str, Any]) -> IndicatorSpec:
    levels = product.get("nutrient_levels")
    clean = {}
    if isinstance(levels, dict):
        clean = {
            k: v
            for k, v in sorted(levels.items())
            if isinstance(k, str) and v in ("low", "moderate", "high")
        }
    if clean:
        return IndicatorSpec("nutrient_levels", AVAILABLE, {"levels": clean})
    return IndicatorSpec("nutrient_levels", MISSING)


def _ingredients_analysis(product: dict[str, Any]) -> IndicatorSpec:
    tags = sorted(_str_list(product.get("ingredients_analysis_tags")))
    known = [t for t in tags if not t.endswith("-unknown")]
    if known:
        return IndicatorSpec("ingredients_analysis", AVAILABLE, {"tags": tags})
    return IndicatorSpec("ingredients_analysis", MISSING, {"tags": tags})


def _additives(product: dict[str, Any]) -> IndicatorSpec:
    if not str(product.get("ingredients_text") or "").strip():
        return IndicatorSpec("additives", MISSING)
    if not isinstance(product.get("additives_tags"), list):
        return IndicatorSpec("additives", MISSING)
    return IndicatorSpec(
        "additives", AVAILABLE, {"tags": sorted(set(_str_list(product["additives_tags"])))}
    )


def derive_from_off(product: dict[str, Any]) -> list[IndicatorSpec]:
    return [
        _nutriscore(product),
        _nova(product),
        _nutrient_levels(product),
        _ingredients_analysis(product),
        _additives(product),
    ]


def apply_packaging_conflict(spec: IndicatorSpec, data: dict[str, Any]) -> IndicatorSpec:
    """A Nutri-Score printed on the package that differs from OFF's grade is a conflict."""
    if spec.indicator != "nutriscore":
        return spec
    declared = data.get("packaging_nutriscore") or {}
    declared_grade = declared.get("grade") if isinstance(declared, dict) else None
    computed = spec.value.get("grade")
    if not declared_grade or not computed or declared_grade == computed:
        return spec
    declared_version = declared.get("version") or ""
    if declared_version and spec.algorithm_version and declared_version != spec.algorithm_version:
        return spec  # different algorithm versions: not a contradiction, shown side by side
    return IndicatorSpec(
        spec.indicator,
        CONFLICTING,
        {**spec.value, "packaging_grade": declared_grade},
        spec.algorithm_version,
        (*spec.warnings, "The grade printed on the package differs from Open Food Facts' grade."),
        spec.origin,
    )
