"""Foodo Score.

``score.v3`` (current): "Foodo Score" x/100, AskFoodo's conversion of the official Nutri-Score
points that Open Food Facts computed (``assessments.foodo``: official grade band from the
points, for the algorithm version and calculation category, then AskFoodo display bands with
linear interpolation). It keeps the original Nutri-Score points, grade, version and category,
or gives an explicit reason why there is no value. NOVA, additives, allergens, portion and
personal targets are never part of it. docs/assessment-rules.md, "Foodo Score (score.v3)".

``score.v2`` (retired): the official Nutri-Score letter only. ``score.v1`` (retired,
:func:`foodo_score_v1`): a 0–100 total with our own weights; kept for reference only.

:func:`foodo_score` is pure and deterministic: it reads indicator objects with the attributes
of :class:`assessments.models.AssessmentSnapshot` (``indicator``, ``data_status``, ``value``,
``algorithm_version``, ``warnings``, ``origin`` and optionally
``invalidated_by_local_change``), so it accepts snapshots and
:class:`assessments.rules.IndicatorSpec` alike, plus OFF's ``nutriscore`` object (per-version
results with the calculation-category flags), and touches no database.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Any

from assessments import foodo

VERSION = "score.v3"
VERSION_V1 = "score.v1"

GRADES = ("a", "b", "c", "d", "e")
OFF = "off_computed"

OK = "ok"
NOT_ENOUGH_DATA = "not_enough_data"
NOT_APPLICABLE = "not_applicable"
CONFLICTING = "conflicting"
UNSUPPORTED = "unsupported"
INCONSISTENT = "inconsistent"

REASONS = {
    "foodo_converted": (
        "AskFoodo's conversion of the official Nutri-Score computed by Open Food Facts "
        "({version} algorithm, {category}). It is an orientative indicator; the label on the "
        "product is the reference."
    ),
    "nutriscore_missing": (
        "Open Food Facts has no Nutri-Score for this product, so no score is shown."
    ),
    "nutriscore_not_current": (
        "The product data was corrected after Open Food Facts computed its Nutri-Score, so "
        "that Nutri-Score is not current and no score is shown."
    ),
    "nutriscore_not_applicable": (
        "Nutri-Score does not apply to this kind of product (for example alcoholic drinks), "
        "so no score is shown."
    ),
    "nutriscore_conflicting": (
        "The Nutri-Score printed on the package ({packaging}) differs from the one computed by "
        "Open Food Facts ({computed}), so no score is shown until the data is checked."
    ),
    "nutriscore_version_unknown": (
        "The Nutri-Score algorithm version is unknown, so no score is shown."
    ),
    "category_unknown": (
        "The Nutri-Score calculation category (food, drink, cheese, fat...) cannot be "
        "identified from the data, so no score is shown."
    ),
    "category_version_unsupported": (
        "AskFoodo does not convert this Nutri-Score version and category yet, so no score is shown."
    ),
    "nutriscore_points_missing": (
        "Open Food Facts gives the Nutri-Score grade but not its points, so no score is shown."
    ),
    "points_out_of_range": (
        "The Nutri-Score points are outside the official range for this category, so no score "
        "is shown until the data is checked."
    ),
    "grade_mismatch": (
        "The Nutri-Score grade does not match its points under the official thresholds, so no "
        "score is shown until the data is checked."
    ),
}
CATEGORY_LABELS = {
    "general": "general foods",
    "cheese": "cheese",
    "red_meat": "red meat products",
    "fats": "fats, oils, nuts and seeds",
    "beverage": "beverages",
    "water": "water",
}


def _first(indicators: Iterable[Any], name: str) -> Any | None:
    """The OFF-computed indicator ``name`` (one per revision; the first if repeated)."""
    for item in indicators:
        if item.indicator == name and (getattr(item, "origin", OFF) or OFF) == OFF:
            return item
    return None


def _current(item: Any) -> bool:
    return not bool(getattr(item, "invalidated_by_local_change", False))


def _value(item: Any) -> dict[str, Any]:
    return item.value if isinstance(getattr(item, "value", None), dict) else {}


def _points(value: Any) -> int | float | None:
    """The points as published (an int, or a float kept so a non-integer is caught later)."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _nova(item: Any | None) -> dict[str, Any]:
    if item is None:
        return {"status": "missing_data", "group": None, "source": OFF}
    if not _current(item):
        return {"status": "not_current", "group": None, "source": OFF}
    group = _value(item).get("group")
    if isinstance(group, str) and group.strip().isdigit():
        group = int(group)
    if item.data_status == "available" and not isinstance(group, bool) and group in (1, 2, 3, 4):
        return {"status": "available", "group": group, "source": OFF}
    status = "not_applicable" if item.data_status == "not_applicable" else "missing_data"
    return {"status": status, "group": None, "source": OFF}


def _additives(item: Any | None) -> dict[str, Any]:
    base = {"source": OFF, "assessed": False}
    if item is None:
        return {"status": "missing_data", "tags": None, "count": None, **base}
    if not _current(item):
        return {"status": "not_current", "tags": None, "count": None, **base}
    tags = _value(item).get("tags")
    if item.data_status != "available" or not isinstance(tags, list):
        return {"status": "missing_data", "tags": None, "count": None, **base}
    clean = sorted({t for t in tags if isinstance(t, str)})
    return {"status": "available", "tags": clean, "count": len(clean), **base}


def category_flags(nutriscore_data: Any, version: str) -> dict[str, Any] | None:
    """OFF's calculation-category flags (``is_*``) of one Nutri-Score version, or ``None``."""
    if not isinstance(nutriscore_data, dict):
        return None
    entry = nutriscore_data.get(version)
    data = entry.get("data") if isinstance(entry, dict) else None
    if not isinstance(data, dict):
        return None
    return {k: data[k] for k in sorted(data) if k.startswith("is_")}


def _hash_inputs(items: list[Any | None], flags: dict[str, Any] | None) -> str:
    rows = [
        None
        if item is None
        else {
            "indicator": item.indicator,
            "data_status": item.data_status,
            "value": _value(item),
            "algorithm_version": item.algorithm_version or "",
            "warnings": list(item.warnings or []),
            "current": _current(item),
        }
        for item in items
    ]
    blob = json.dumps(
        {"indicators": rows, "category_flags": flags},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(blob.encode()).hexdigest()


def foodo_score(indicators: Iterable[Any], *, nutriscore_data: Any = None) -> dict[str, Any]:
    """``score.v3`` of one product (pure, deterministic).

    ``nutriscore_data`` is OFF's ``nutriscore`` object (keyed by version, each with ``data``
    holding the calculation-category flags), from ``revision.off_payload`` or a raw OFF
    product: the same normalisation for API, dump and search data.
    """
    items = list(indicators)
    ns, nova, additives = (_first(items, n) for n in ("nutriscore", "nova", "additives"))

    value = _value(ns) if ns is not None else {}
    grade = str(value.get("grade") or "").lower().strip()
    version = str(getattr(ns, "algorithm_version", "") or "").strip() if ns else ""
    flags = category_flags(nutriscore_data, version) if version else None
    category = foodo.category_for(version, flags) if flags is not None else None
    points = _points(value.get("score"))
    other_versions = value.get("other_versions")
    nutriscore: dict[str, Any] = {
        "status": ns.data_status if ns is not None else "missing_data",
        "grade": grade if grade in GRADES else None,  # original, kept for checking
        "points": points,  # original
        "version": version or None,
        "category": category,
        "source": OFF,
        "other_versions": dict(sorted(other_versions.items()))
        if isinstance(other_versions, dict)
        else {},
        "packaging_grade": None,
        "warnings": list(getattr(ns, "warnings", None) or []) if ns is not None else [],
    }
    result_foodo: dict[str, Any] = {"value": None, "band": None, "formula": foodo.FORMULA}
    reason_args: dict[str, str] = {}
    known = ("available", "conflicting", "not_applicable")
    if (
        ns is None
        or ns.data_status not in known
        or (ns.data_status != "not_applicable" and grade not in GRADES)
    ):
        status, code = NOT_ENOUGH_DATA, "nutriscore_missing"
        nutriscore["status"] = "missing_data"
    elif not _current(ns):
        status, code = NOT_ENOUGH_DATA, "nutriscore_not_current"
        nutriscore["status"] = "not_current"
    elif ns.data_status == "not_applicable":
        status, code = NOT_APPLICABLE, "nutriscore_not_applicable"
    elif ns.data_status == "conflicting":
        status, code = CONFLICTING, "nutriscore_conflicting"
        packaging = str(value.get("packaging_grade") or "").lower()
        nutriscore["packaging_grade"] = packaging or None
        reason_args = {"packaging": packaging.upper() or "?", "computed": grade.upper()}
    elif not version:
        status, code = NOT_ENOUGH_DATA, "nutriscore_version_unknown"
    elif category is None:
        status, code = UNSUPPORTED, "category_unknown"
    elif (version, category) not in foodo.OFFICIAL:
        status, code = UNSUPPORTED, "category_version_unsupported"
    else:
        conversion = foodo.convert(points, version, category)
        if conversion.error == "points_missing":
            status, code = NOT_ENOUGH_DATA, "nutriscore_points_missing"
        elif conversion.error:
            status, code = INCONSISTENT, conversion.error
        elif conversion.band.lower() != grade:
            status, code = INCONSISTENT, "grade_mismatch"
        else:
            status, code = OK, "foodo_converted"
            result_foodo.update(value=conversion.value, band=conversion.band)
            reason_args = {"version": version, "category": CATEGORY_LABELS[category]}

    separate = {"nova": _nova(nova), "additives": _additives(additives)}
    missing: list[str] = []
    if code == "nutriscore_missing":
        missing.append("nutriscore")
    if code == "nutriscore_points_missing":
        missing.append("nutriscore_points")
    if separate["nova"]["status"] not in ("available", "not_applicable"):
        missing.append("nova")
    if separate["additives"]["status"] != "available":
        missing.append("additives")

    return {
        "version": VERSION,
        "status": status,
        "foodo": result_foodo,
        "nutriscore": nutriscore,
        "comparable_key": f"nutriscore:{version}:{category}" if status == OK else None,
        "separate": separate,
        "missing": missing,
        "reasons": [{"code": code, "text": REASONS[code].format(**reason_args)}],
        "inputs_hash": _hash_inputs([ns, nova, additives], flags),
    }


def foodo_value(score: dict[str, Any] | None) -> int | None:
    """The Foodo Score (0–100) of a result, or ``None`` when there is none. 0 is a value."""
    if not isinstance(score, dict) or score.get("status") != OK:
        return None
    return (score.get("foodo") or {}).get("value")


def _comparable(candidate: dict[str, Any], reference: dict[str, Any]) -> bool:
    return (
        candidate.get("version") == VERSION == reference.get("version")
        and candidate.get("status") == OK == reference.get("status")
        and candidate.get("comparable_key") == reference.get("comparable_key")
    )


def is_better(candidate: dict[str, Any], reference: dict[str, Any]) -> bool:
    """A strictly higher Foodo Score, with the same Nutri-Score version and calculation
    category. Equal values after rounding are not better."""
    return _comparable(candidate, reference) and (foodo_value(candidate) > foodo_value(reference))


def score_from_off(product: dict[str, Any]) -> dict[str, Any]:
    """``score.v3`` of a raw OFF product object (e.g. a search result), read with the same
    rules as an imported product."""
    from assessments.rules import derive_from_off

    return with_provenance(
        foodo_score(derive_from_off(product), nutriscore_data=product.get("nutriscore")),
        source="off_api",
        data_id=None,
    )


# Provenance added to a stored result: where the data came from and which data it was.
PROVENANCE = ("source", "data_id")
SOURCES = {"off_import": "off_api", "off_dump": "off_dump", "contribution": "contribution"}


def score_source(revision_source: str) -> str:
    """``off_api`` / ``off_dump`` / ``contribution`` from a ``ProductRevision.source``."""
    return SOURCES.get(revision_source, revision_source or "unknown")


def with_provenance(result: dict[str, Any], *, source: str, data_id: str | None) -> dict[str, Any]:
    """The result with its data source and the identifier of the data used (the revision's
    content hash). Provenance never changes the status or the value."""
    return {**result, "source": source, "data_id": data_id}


def core(result: dict[str, Any]) -> dict[str, Any]:
    """The result without provenance: equal for the same data, whatever its source."""
    return {k: v for k, v in result.items() if k not in PROVENANCE}


def needs_recompute(stored: Any) -> bool:
    """True for a missing result, another rules version, or one stored without provenance."""
    return (
        not isinstance(stored, dict)
        or stored.get("version") != VERSION
        or any(k not in stored for k in PROVENANCE)
    )


def nutriscore_data_of(revision: Any) -> Any:
    payload = getattr(revision, "off_payload", None)
    return payload.get("nutriscore") if isinstance(payload, dict) else None


def for_revision(revision: Any) -> dict[str, Any]:
    """Compute (never read) the result of a revision, with its provenance."""
    return with_provenance(
        foodo_score(revision.assessments.all(), nutriscore_data=nutriscore_data_of(revision)),
        source=score_source(getattr(revision, "source", "")),
        data_id=getattr(revision, "content_hash", None),
    )


def score_for_revision(revision: Any) -> dict[str, Any]:
    """The stored result of a revision, or computed now (without writing) for a revision whose
    stored result is missing or outdated (``needs_recompute``); the batch job
    ``assessments.tasks.recompute_foodo_scores`` stores those. Another version is never
    returned."""
    if revision is None:
        return foodo_score([])
    stored = getattr(revision, "foodo_score", None)
    if not needs_recompute(stored):
        return stored
    return for_revision(revision)


def score_for_product(product: Any) -> dict[str, Any]:
    """Foodo Score of a catalogue product's current revision."""
    return score_for_revision(getattr(product, "current_revision", None))


STATUS_MESSAGES = {
    NOT_ENOUGH_DATA: "Not enough data",
    NOT_APPLICABLE: "Doesn't apply to this product",
    CONFLICTING: "Data being checked",
    INCONSISTENT: "Data being checked",
    UNSUPPORTED: "Not available for this kind of product yet",
}


def display(score: dict[str, Any] | None) -> dict[str, Any]:
    """What a page shows: ``{"value": 0–100 or None, "message": short text}``."""
    value = foodo_value(score)
    if value is not None:
        return {"value": value, "message": ""}
    status = (score or {}).get("status") or NOT_ENOUGH_DATA
    message = STATUS_MESSAGES.get(status, STATUS_MESSAGES[NOT_ENOUGH_DATA])
    return {"value": None, "message": message}


def summary(score: dict[str, Any]) -> dict[str, Any]:
    """The facts an AI model may rephrase (Ask Foodo): the Foodo value or why there is none;
    no Nutri-Score letter or raw points."""
    shown = display(score)
    return {
        "version": score.get("version"),
        "status": score.get("status"),
        "foodo_score": f"{shown['value']}/100" if shown["value"] is not None else None,
        "message": shown["message"] or None,
        "reasons": [r["text"] for r in score.get("reasons") or []],
    }


# --- score.v1 (retired) ------------------------------------------------------------------
# Kept unchanged for reference: docs/assessment-rules.md, "score.v1 (retired)".

NUTRISCORE_POINTS = {"a": 0, "b": -10, "c": -25, "d": -40, "e": -55}
NOVA_POINTS = {1: 0, 2: 0, 3: -10, 4: -20}
HIGH_LEVEL_POINTS = -5
LEVEL_NUTRIENTS = {"sugars": "sugars", "salt": "salt", "saturated-fat": "saturated fat"}
HIGH_RISK_ADDITIVES: frozenset[str] = frozenset()
ADDITIVE_POINTS = -5
ADDITIVE_CAP = -15


def _grade_v1(data: dict[str, Any]) -> str | None:
    grade = str(data.get("nutriscore_grade") or "").lower().strip()
    return grade if grade in NUTRISCORE_POINTS else None


def _nova_v1(data: dict[str, Any]) -> int | None:
    group = data.get("nova_group")
    if isinstance(group, str) and group.strip().isdigit():
        group = int(group)
    if isinstance(group, int) and not isinstance(group, bool) and group in NOVA_POINTS:
        return group
    return None


def foodo_score_v1(data: dict[str, Any]) -> dict[str, Any]:
    """Retired ``score.v1``: 0–100 with own weights. Not used by pages, the API or the AI."""
    grade, nova = _grade_v1(data), _nova_v1(data)
    if grade is None and nova is None:
        return {"status": "not_enough_data", "version": VERSION_V1}
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
        "version": VERSION_V1,
        "points": points,
        "missing": missing,
    }


def indicators_from_snapshots(snapshots: Iterable[Any]) -> dict[str, Any]:
    """Current OFF-computed snapshots as OFF field names (``nutriscore_grade``, ``nova_group``,
    ``nutrient_levels``, ``additives_tags``): the ``score.v1`` input, still used to word the
    advantages of an alternative. Snapshots invalidated by a local correction are ignored."""
    data: dict[str, Any] = {}
    for s in snapshots:
        if s.origin != OFF or s.invalidated_by_local_change:
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
