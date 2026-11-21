"""Publishing immutable revisions. The single place where a product's public data changes.

Callers must hold the product row lock (``select_for_update``) inside a transaction.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Sequence
from typing import Any

from django.db.models import Max
from django.urls import reverse
from django.utils import timezone

from assessments.models import AssessmentSnapshot
from assessments.rules import IndicatorSpec, apply_packaging_conflict, inputs_hash
from catalog import quality
from catalog.models import NutrientFact, Product, ProductRevision
from catalog.nutrition import CORE_NUTRIENTS, to_decimal


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _spec_signature(specs: Sequence[IndicatorSpec | AssessmentSnapshot]) -> list[Any]:
    sig = []
    for s in specs:
        sig.append(
            [
                s.indicator,
                s.data_status,
                s.value,
                s.algorithm_version,
                list(s.warnings),
                getattr(s, "inputs_hash", None),
            ]
        )
    return sorted(sig, key=lambda x: x[0])


def content_hash(data: dict[str, Any], specs_signature: list[Any]) -> str:
    blob = canonical_json({"data": data, "assessments": specs_signature})
    return hashlib.sha256(blob.encode()).hexdigest()


def fold(text: str) -> str:
    """Lowercase, accent-free text used for search."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def completeness(data: dict[str, Any], has_image: bool) -> int:
    """Share of key data present (0-100). A data-completeness measure, not a health score."""
    facts = (data.get("nutrition") or {}).get("facts") or []
    core_present = {
        f["nutrient"]
        for f in facts
        if f["preparation"] == "as_sold"
        and f["basis"] != "per_serving"
        and f["status"] == "declared"
    } & set(CORE_NUTRIENTS)
    nutrition_score = len(core_present) / len(CORE_NUTRIENTS)
    if (data.get("nutrition") or {}).get("status") == "not_on_package":
        nutrition_score = 1.0
    items = [
        bool(data.get("name")),
        bool(data.get("brand")),
        bool(data.get("quantity")),
        bool(data.get("categories")),
        bool((data.get("ingredients") or {}).get("text")),
        has_image,
    ]
    return round(100 * (sum(items) + nutrition_score) / (len(items) + 1))


def publish_revision(
    product: Product,
    data: dict[str, Any],
    *,
    source: str,
    field_sources: dict[str, Any],
    off_specs: Sequence[IndicatorSpec] | None = None,
    off_data: dict[str, Any] | None = None,
    off_payload: dict[str, Any] | None = None,
    off_last_modified=None,
    contribution=None,
) -> ProductRevision | None:
    """Create a new revision unless it would be identical to the current one.

    ``off_specs`` are indicators freshly derived from ``off_data``. When omitted, the
    indicators of the current revision are carried over; either way each indicator is
    marked *not current* if the new data differs from the inputs it was computed from.
    """
    base = product.current_revision
    now = timezone.now()

    if off_specs is not None:
        assert off_data is not None
        drafts = [(spec, inputs_hash(spec.indicator, off_data), now) for spec in off_specs]
    elif base is not None:
        drafts = [
            (
                IndicatorSpec(
                    s.indicator,
                    s.data_status,
                    s.value,
                    s.algorithm_version,
                    tuple(s.warnings),
                    s.origin,
                ),
                s.inputs_hash,
                s.computed_at,
            )
            for s in base.assessments.filter(origin=AssessmentSnapshot.Origin.OFF_COMPUTED)
        ]
    else:
        drafts = []

    # Packaging conflicts depend on the revision data; inputs_hash on the source data.
    drafts = [(apply_packaging_conflict(spec, data), h, at) for spec, h, at in drafts]
    signature = _spec_signature(
        [
            IndicatorSpec(s.indicator, s.data_status, s.value, s.algorithm_version, s.warnings)
            for s, _, _ in drafts
        ]
    ) + [[s.indicator, h] for s, h, _ in drafts]
    source_data = off_data if off_data is not None else (base.off_data if base else None)
    # The OFF source is part of the identity: a new OFF import is kept even when locked
    # local corrections make the published fields identical.
    digest = content_hash(data, [*signature, ["off_source", source_data]])
    if base is not None and base.content_hash == digest:
        return None

    number = (product.revisions.aggregate(m=Max("number"))["m"] or 0) + 1
    revision = ProductRevision.objects.create(
        product=product,
        number=number,
        source=source,
        data=data,
        content_hash=digest,
        field_sources=field_sources,
        off_data=source_data,
        off_payload=off_payload
        if off_payload is not None
        else (base.off_payload if base else None),
        off_last_modified=off_last_modified or (base.off_last_modified if base else None),
        base_revision=base,
        contribution=contribution,
    )
    NutrientFact.objects.bulk_create(
        [
            NutrientFact(
                revision=revision,
                nutrient=f["nutrient"],
                basis=f["basis"],
                preparation=f["preparation"],
                status=f["status"],
                value=to_decimal(f["value"]) if f["value"] is not None else None,
                unit=f["unit"],
                modifier=f.get("modifier") or "",
            )
            for f in (data.get("nutrition") or {}).get("facts") or []
        ]
    )
    snapshots = [
        AssessmentSnapshot(
            revision=revision,
            indicator=spec.indicator,
            data_status=spec.data_status,
            value=spec.value,
            algorithm_version=spec.algorithm_version,
            origin=AssessmentSnapshot.Origin.OFF_COMPUTED,
            warnings=list(spec.warnings),
            inputs_hash=h,
            invalidated_by_local_change=inputs_hash(spec.indicator, data) != h,
            computed_at=at,
        )
        for spec, h, at in drafts
    ]
    declared = data.get("packaging_nutriscore")
    if isinstance(declared, dict) and declared.get("grade"):
        snapshots.append(
            AssessmentSnapshot(
                revision=revision,
                indicator="nutriscore",
                data_status=AssessmentSnapshot.DataStatus.AVAILABLE,
                value={"grade": declared["grade"]},
                algorithm_version=declared.get("version") or "",
                origin=AssessmentSnapshot.Origin.PACKAGING_DECLARED,
                warnings=[],
                inputs_hash=inputs_hash("nutriscore", data),
                computed_at=now,
            )
        )
    AssessmentSnapshot.objects.bulk_create(snapshots)
    apply_revision(product, revision, snapshots)
    return revision


def apply_revision(
    product: Product, revision: ProductRevision, snapshots: Sequence[AssessmentSnapshot]
) -> None:
    data = revision.data
    product.current_revision = revision
    product.name = (data.get("name") or "")[:300]
    product.brand = (data.get("brand") or "")[:300]
    product.quantity = (data.get("quantity") or "")[:100]
    product.search_text = fold(f"{product.name} {product.brand}")
    product.categories = list(data.get("categories") or [])
    product.countries = list(data.get("countries") or [])
    ns = next(
        (
            s
            for s in snapshots
            if s.indicator == "nutriscore" and s.origin == AssessmentSnapshot.Origin.OFF_COMPUTED
        ),
        None,
    )
    usable = (
        ns is not None
        and ns.data_status in ("available", "conflicting")
        and not ns.invalidated_by_local_change
    )
    product.nutriscore_grade = (ns.value.get("grade") or "") if usable else ""
    product.nutriscore_version = ns.algorithm_version if usable else ""
    nova = next(
        (s for s in snapshots if s.indicator == "nova" and s.origin == "off_computed"), None
    )
    product.nova_group = (
        (nova.value or {}).get("group")
        if nova and nova.data_status == "available" and not nova.invalidated_by_local_change
        else None
    )
    _set_front_and_quality(product, data)
    product.save()


def _set_front_and_quality(product: Product, data: dict) -> None:
    """Front photo URL, completeness and quality from the active photos and ``data``."""
    front = product.images.filter(is_active=True, kind="front").order_by("-created_at").first()
    if front is None:
        product.front_image_url = ""
    elif front.remote_url:
        product.front_image_url = reverse("off-image", args=[front.id])
    else:
        product.front_image_url = reverse("public-image", args=[front.id])
    product.completeness = completeness(data, has_image=front is not None)
    product.quality_issues = quality.issues(data, has_front_image=front is not None)
    product.listable = quality.listable(product.quality_issues)


def refresh_photos(product: Product) -> None:
    """After a photo was switched on or off: front photo, completeness and quality again."""
    revision = product.current_revision
    _set_front_and_quality(product, revision.data if revision else {})
    product.save(
        update_fields=[
            "front_image_url",
            "completeness",
            "quality_issues",
            "listable",
            "updated_at",
        ]
    )
