"""Foodo Score (``score.v1``): a 0–100 summary with reconstructible points.

Computed in Python from the OFF indicators only (Nutri-Score grade, NOVA group, nutrient
levels, additives). It never uses personal data: diets, allergies and goals do not change
the score. See docs/assessment-rules.md, section "Foodo Score (score.v1)".

Input (``foodo_score``): a dict with the OFF field names, as found in an OFF product object
(``revision.off_payload`` or a search result) or rebuilt from assessment snapshots by
:func:`indicators_from_snapshots`::

    {"nutriscore_grade": "c", "nova_group": 4,
     "nutrient_levels": {"sugars": "high", ...}, "additives_tags": ["en:e250", ...]}
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

VERSION = "score.v1"

NUTRISCORE_POINTS = {"a": 0, "b": -10, "c": -25, "d": -40, "e": -55}
NOVA_POINTS = {1: 0, 2: 0, 3: -10, 4: -20}
HIGH_LEVEL_POINTS = -5
LEVEL_NUTRIENTS = {"sugars": "sugars", "salt": "salt", "saturated-fat": "saturated fat"}

# High-risk additives. The codebase does not flag any additive as risky and OFF product data
# carries no risk level, so this list is deliberately empty in score.v1: the component is
# computed (and documented) but never costs points until a reviewed list is added in a new
# score version.
HIGH_RISK_ADDITIVES: frozenset[str] = frozenset()
ADDITIVE_POINTS = -5
ADDITIVE_CAP = -15


def _grade(data: dict[str, Any]) -> str | None:
    grade = str(data.get("nutriscore_grade") or "").lower().strip()
    return grade if grade in NUTRISCORE_POINTS else None


def _nova(data: dict[str, Any]) -> int | None:
    group = data.get("nova_group")
    if isinstance(group, str) and group.strip().isdigit():
        group = int(group)
    if isinstance(group, int) and not isinstance(group, bool) and group in NOVA_POINTS:
        return group
    return None


def foodo_score(data: dict[str, Any]) -> dict[str, Any]:
    grade, nova = _grade(data), _nova(data)
    if grade is None and nova is None:
        return {"status": "not_enough_data", "version": VERSION}
    points: list[dict[str, Any]] = []
    missing: list[str] = []

    if grade is None:
        missing.append("nutriscore")
    elif NUTRISCORE_POINTS[grade]:
        points.append(
            {"reason": f"Nutri-Score {grade.upper()}", "points": NUTRISCORE_POINTS[grade]}
        )

    if nova is None:
        missing.append("nova")
    elif NOVA_POINTS[nova]:
        points.append({"reason": f"NOVA group {nova}", "points": NOVA_POINTS[nova]})

    levels = data.get("nutrient_levels")
    if isinstance(levels, dict) and levels:
        for key, label in LEVEL_NUTRIENTS.items():
            if levels.get(key) == "high":
                points.append({"reason": f"High {label}", "points": HIGH_LEVEL_POINTS})
    else:
        missing.append("nutrient_levels")

    additives = data.get("additives_tags")
    if isinstance(additives, list):
        risky = sorted({a for a in additives if a in HIGH_RISK_ADDITIVES})
        total = max(ADDITIVE_CAP, ADDITIVE_POINTS * len(risky))
        if total:
            names = ", ".join(a.split(":")[-1].upper() for a in risky)
            points.append({"reason": f"High-risk additives: {names}", "points": total})
    else:
        missing.append("additives")

    score = max(0, min(100, 100 + sum(p["points"] for p in points)))
    return {
        "status": "ok",
        "score": score,
        "version": VERSION,
        "points": points,
        "missing": missing,
    }


def indicators_from_snapshots(snapshots: Iterable[Any]) -> dict[str, Any]:
    """OFF-computed, current snapshots as ``foodo_score`` input.

    Snapshots invalidated by a local correction are ignored (their value is not current).
    """
    data: dict[str, Any] = {}
    for s in snapshots:
        if s.origin != "off_computed" or s.invalidated_by_local_change:
            continue
        if s.data_status not in ("available", "conflicting"):
            continue
        value = s.value or {}
        if s.indicator == "nutriscore":
            data["nutriscore_grade"] = value.get("grade")
        elif s.indicator == "nova":
            data["nova_group"] = value.get("group")
        elif s.indicator == "nutrient_levels":
            data["nutrient_levels"] = value.get("levels") or {}
        elif s.indicator == "additives":
            data["additives_tags"] = list(value.get("tags") or [])
    return data


def score_for_product(product: Any) -> dict[str, Any]:
    """Foodo Score of a catalogue product's current revision (computed, not stored)."""
    revision = getattr(product, "current_revision", None)
    if revision is None:
        return {"status": "not_enough_data", "version": VERSION}
    return foodo_score(indicators_from_snapshots(revision.assessments.all()))
