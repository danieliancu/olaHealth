"""Approval and rejection of contributions. Used by the admin; one implementation.

Approval runs in a transaction holding the product row lock. If the product changed
since the contribution's base revision *in a field the contribution touches*, the
contribution goes to ``conflict`` instead of overwriting (no last-write-wins).
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from django.db import transaction
from django.utils import timezone

from catalog.models import Product, ProductImage, ProductRevision, ReviewFlag
from catalog.services.importer import ensure_identifier, identifier_context, raise_flag
from catalog.services.revisions import publish_revision
from contributions.models import Contribution, ModerationAudit
from contributions.validation import validate_facts
from off.codes import InvalidCode, parse_code
from off.mapping import DATA_FIELDS

CONTRIBUTED_IMAGE_LICENSE = "CC BY-SA 4.0"


class ModerationError(RuntimeError):
    pass


@dataclass
class Outcome:
    status: str
    revision: ProductRevision | None = None
    conflicting_fields: tuple[str, ...] = ()


def changed_fields(a: dict[str, Any] | None, b: dict[str, Any] | None) -> set[str]:
    a, b = a or {}, b or {}
    return {f for f in DATA_FIELDS if a.get(f) != b.get(f)}


def _empty_data() -> dict[str, Any]:
    return {
        "name": None,
        "generic_name": None,
        "brand": None,
        "quantity": None,
        "serving_size": None,
        "categories": [],
        "countries": [],
        "ingredients": {"text": None, "lang": None},
        "allergens": {"status": "unknown", "items": []},
        "traces": {"status": "unknown", "items": []},
        "nutrition": {"status": "unknown", "facts": []},
        "packaging_nutriscore": None,
    }


def _audit(c: Contribution, actor, action: str, reason: str = "", **details: Any) -> None:
    ModerationAudit.objects.create(
        contribution=c, actor=actor, action=action, reason=reason, details=details
    )


def approve(contribution_id: Any, moderator, reason: str = "") -> Outcome:
    with transaction.atomic():
        c = Contribution.objects.select_for_update().get(pk=contribution_id)
        if c.status != Contribution.Status.SUBMITTED:
            raise ModerationError(f"Only submitted contributions can be approved (is {c.status}).")
        proposed = {k: v for k, v in (c.proposed or {}).items() if k in DATA_FIELDS}
        nutrition = proposed.get("nutrition") or {}
        if nutrition.get("facts"):
            report = validate_facts(
                nutrition["facts"],
                nutrition["facts"][0]["basis"],
                proposed.get("serving_size") or "",
            )
            if not report.ok:
                raise ModerationError("; ".join(report.errors))

        if c.product_id:
            product = Product.objects.select_for_update().get(pk=c.product_id)
        else:
            product = _product_for_new_contribution(c)

        current = product.current_revision
        if c.base_revision_id and current and current.pk != c.base_revision_id:
            base = ProductRevision.objects.get(pk=c.base_revision_id)
            overlap = changed_fields(base.data, current.data) & set(proposed)
            if overlap:
                c.status = Contribution.Status.CONFLICT
                c.moderator = moderator
                c.moderated_at = timezone.now()
                c.save(update_fields=["status", "moderator", "moderated_at", "updated_at"])
                _audit(
                    c,
                    moderator,
                    "conflict",
                    reason,
                    fields=sorted(overlap),
                    base_revision=base.number,
                    current_revision=current.number,
                )
                return Outcome("conflict", conflicting_fields=tuple(sorted(overlap)))

        data = copy.deepcopy(current.data) if current else _empty_data()
        sources = copy.deepcopy(current.field_sources) if current else {}
        now = timezone.now()
        for name, value in proposed.items():
            data[name] = value
            sources[name] = {
                "source": "contribution",
                "contribution": str(c.pk),
                "approved_at": now.isoformat(),
            }
        for name in DATA_FIELDS:
            sources.setdefault(name, {"source": "none"})

        _publish_images(c, product)
        revision = publish_revision(
            product,
            data,
            source="contribution",
            field_sources=sources,
            contribution=c,
        )
        if proposed:
            product.locked_fields = sorted(set(product.locked_fields) | set(proposed))
            product.save(update_fields=["locked_fields", "updated_at"])
        if revision is None and product.current_revision:
            # Photos only (or identical data): refresh denormalised image fields.
            from catalog.services.revisions import apply_revision

            apply_revision(
                product, product.current_revision, list(product.current_revision.assessments.all())
            )
        if c.problem_type == Contribution.ProblemType.WRONG_PRODUCT and c.product_id:
            raise_flag(
                ReviewFlag.Kind.IDENTITY_CONFLICT,
                product,
                "",
                {"contribution": str(c.pk), "code": c.code, "note": c.note[:500]},
            )
        for w in c.validation_warnings:
            raise_flag(
                ReviewFlag.Kind.UNUSUAL_VALUE,
                product,
                "nutrition",
                {"contribution": str(c.pk), "warning": w},
            )

        c.status = Contribution.Status.APPROVED
        c.product = product
        c.moderator = moderator
        c.moderation_reason = reason
        c.moderated_at = now
        c.resulting_revision = revision or product.current_revision
        c.save()
        _audit(
            c,
            moderator,
            "approve",
            reason,
            fields=sorted(proposed),
            revision=c.resulting_revision.number if c.resulting_revision else None,
        )
        return Outcome("approved", revision)


def _product_for_new_contribution(c: Contribution) -> Product:
    if not c.code:
        raise ModerationError("A new product needs a barcode.")
    try:
        parsed = parse_code(c.code, c.code_format or "manual", allow_bad_check=True)
    except InvalidCode as exc:
        raise ModerationError(exc.message) from exc
    context = identifier_context(parsed, origin="local", country=c.country)
    from catalog.models import ProductIdentifier

    existing = (
        ProductIdentifier.objects.select_for_update()
        .filter(normalized=parsed.identity_key, context=context)
        .first()
    )
    if existing is not None:
        # The product appeared meanwhile (e.g. imported from OFF): moderate against it.
        raise ModerationError(
            "A product with this barcode now exists. Re-check the contribution against it."
        )
    product = Product.objects.create(off_sync_status=Product.SyncStatus.LOCAL_ONLY)
    ensure_identifier(product, parsed, context)
    return product


def _publish_images(c: Contribution, product: Product) -> None:
    for img in c.images.all():
        img.is_public = True
        img.save(update_fields=["is_public"])
        ProductImage.objects.update_or_create(
            contribution_image=img,
            defaults={
                "product": product,
                "kind": img.kind,
                "lang": img.lang,
                "source": ProductImage.Source.CONTRIBUTION,
                "license": CONTRIBUTED_IMAGE_LICENSE,
                "attribution": "FoodoTrack contributor",
                "is_active": True,
            },
        )
        # Newer contributed photo of the same kind replaces older ones in display.
        ProductImage.objects.filter(product=product, kind=img.kind, is_active=True).exclude(
            contribution_image=img
        ).update(is_active=False)


def reject(contribution_id: Any, moderator, reason: str) -> None:
    if not reason.strip():
        raise ModerationError("A reason is required to reject a contribution.")
    with transaction.atomic():
        c = Contribution.objects.select_for_update().get(pk=contribution_id)
        if c.status not in (Contribution.Status.SUBMITTED, Contribution.Status.CONFLICT):
            raise ModerationError(f"Cannot reject a contribution in status {c.status}.")
        c.status = Contribution.Status.REJECTED
        c.moderator = moderator
        c.moderation_reason = reason
        c.moderated_at = timezone.now()
        c.save()
        _audit(c, moderator, "reject", reason)


def rebase(contribution_id: Any, moderator, reason: str) -> None:
    """After a moderator reviewed the conflict, re-target the current revision explicitly."""
    if not reason.strip():
        raise ModerationError("Explain why the contribution still applies.")
    with transaction.atomic():
        c = Contribution.objects.select_for_update().get(pk=contribution_id)
        if c.status != Contribution.Status.CONFLICT:
            raise ModerationError("Only conflicting contributions can be re-based.")
        old = c.base_revision.number if c.base_revision else None
        c.base_revision = c.product.current_revision if c.product else None
        c.status = Contribution.Status.SUBMITTED
        c.save()
        _audit(
            c,
            moderator,
            "rebase",
            reason,
            from_revision=old,
            to_revision=c.base_revision.number if c.base_revision else None,
        )
