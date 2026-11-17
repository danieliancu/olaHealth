"""Import and re-import of Open Food Facts products, with reconciliation.

* The new OFF data is always kept as the revision's source (``off_data``/``off_payload``).
* Fields corrected locally (``Product.locked_fields``) are never overwritten.
* When OFF changes a locked field to a value different from the local one, an open
  ``data_conflict`` review flag is raised (once per distinct value).
* Idempotent: an import producing the same content creates no revision.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from assessments.rules import derive_from_off
from catalog.models import Product, ProductIdentifier, ProductImage, ReviewFlag
from catalog.services.revisions import apply_revision, canonical_json, publish_revision
from off.codes import ParsedCode, off_normalize
from off.mapping import DATA_FIELDS, image_urls, map_product

OFF_IMAGE_LICENSE = "CC BY-SA (Open Food Facts contributors)"
OFF_IMAGE_ATTRIBUTION = "Open Food Facts contributors, openfoodfacts.org"


def identifier_context(parsed: ParsedCode, *, origin: str, country: str = "") -> str:
    """Scope for codes that are not globally unique.

    GTINs share one global namespace (empty context). Restricted-circulation codes found
    in OFF are kept under ``off``; locally added ones under the declared country.
    """
    if parsed.kind == "gtin":
        return ""
    if parsed.kind == "unverified":
        return "unverified"
    return "off" if origin == "off" else (f"country:{country}" if country else "unscoped")


def ensure_identifier(
    product: Product, parsed: ParsedCode, context: str
) -> ProductIdentifier | None:
    """Attach the identifier, or raise an identity conflict if it belongs elsewhere."""
    existing = ProductIdentifier.objects.filter(
        normalized=parsed.identity_key, context=context
    ).first()
    if existing is None:
        try:
            with transaction.atomic():
                return ProductIdentifier.objects.create(
                    product=product,
                    raw_value=parsed.raw[:32],
                    detected_format=parsed.detected_format,
                    normalized=parsed.identity_key,
                    off_code=parsed.off_code,
                    kind=parsed.kind,
                    context=context,
                )
        except IntegrityError:
            existing = ProductIdentifier.objects.get(
                normalized=parsed.identity_key, context=context
            )
    if existing.product_id != product.id:
        raise_flag(
            ReviewFlag.Kind.IDENTITY_CONFLICT,
            product,
            "",
            {
                "identifier": existing.normalized,
                "context": context,
                "other_product": str(existing.product_id),
            },
        )
    return existing


def raise_flag(kind: str, product: Product | None, field: str, details: dict[str, Any]) -> None:
    fingerprint = hashlib.sha256(
        canonical_json([str(product.pk) if product else "", field, details]).encode()
    ).hexdigest()
    ReviewFlag.objects.get_or_create(
        kind=kind,
        fingerprint=fingerprint,
        status=ReviewFlag.Status.OPEN,
        defaults={"product": product, "field": field, "details": details},
    )


def reconcile(
    product: Product, off_data: dict[str, Any], off_modified: str | None
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    current = product.current_revision
    locked = set(product.locked_fields) if current else set()
    previous_off = (current.off_data or {}) if current else {}
    data: dict[str, Any] = {}
    sources: dict[str, Any] = {}
    conflicts: list[str] = []
    for name in DATA_FIELDS:
        if name in locked:
            data[name] = current.data.get(name)
            sources[name] = current.field_sources.get(name, {"source": "contribution"})
            new_off = off_data.get(name)
            if new_off != data[name] and new_off != previous_off.get(name):
                conflicts.append(name)
        else:
            data[name] = off_data.get(name)
            sources[name] = {"source": "off", "off_last_modified": off_modified}
    return data, sources, conflicts


def _off_modified(payload: dict[str, Any]) -> datetime | None:
    value = payload.get("last_modified_t")
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return datetime.fromtimestamp(value, tz=UTC)
    return None


def _sync_off_images(product: Product, urls: dict[str, str]) -> bool:
    """Upsert OFF image references. Returns True when anything changed."""
    changed = False
    current = {
        (img.kind, img.remote_url): img
        for img in product.images.filter(source=ProductImage.Source.OFF)
    }
    wanted = {(kind, url) for kind, url in urls.items()}
    for key, img in current.items():
        active = key in wanted
        if img.is_active != active:
            img.is_active = active
            img.save(update_fields=["is_active"])
            changed = True
    for kind, url in wanted - set(current):
        changed = True
        ProductImage.objects.create(
            product=product,
            kind=kind,
            source=ProductImage.Source.OFF,
            license=OFF_IMAGE_LICENSE,
            attribution=OFF_IMAGE_ATTRIBUTION,
            remote_url=url,
        )
    return changed


def _queue_image_cache(product_id: str) -> None:
    from django_q.tasks import async_task

    async_task("catalog.tasks.cache_off_images", product_id, group="off-images")


def _get_or_create_product(off_code: str, parsed: ParsedCode | None) -> Product:
    product = Product.objects.select_for_update().filter(off_code=off_code).first()
    if product is not None:
        return product
    if parsed is not None and parsed.kind == "gtin":
        # A product added locally (e.g. by a contribution) that OFF now knows.
        ident = (
            ProductIdentifier.objects.select_related("product")
            .filter(normalized=parsed.identity_key, context="", product__off_code__isnull=True)
            .first()
        )
        if ident is not None:
            product = Product.objects.select_for_update().get(pk=ident.product_id)
            product.off_code = off_code
            product.save(update_fields=["off_code"])
            return product
    try:
        with transaction.atomic():
            return Product.objects.create(off_code=off_code)
    except IntegrityError:
        return Product.objects.select_for_update().get(off_code=off_code)


def import_off_product(payload: dict[str, Any], parsed: ParsedCode | None = None) -> Product:
    off_code = off_normalize(str(payload.get("code") or (parsed.digits if parsed else "")))
    off_data = map_product(payload)
    specs = derive_from_off(payload)
    modified = _off_modified(payload)
    now = timezone.now()
    with transaction.atomic():
        product = _get_or_create_product(off_code, parsed)
        if parsed is not None:
            ensure_identifier(product, parsed, identifier_context(parsed, origin="off"))
        images_changed = _sync_off_images(product, image_urls(payload, settings.OFF_IMAGE_HOSTS))
        data, sources, conflicts = reconcile(
            product, off_data, modified.isoformat() if modified else None
        )
        revision = publish_revision(
            product,
            data,
            source="off_import",
            field_sources=sources,
            off_specs=specs,
            off_data=off_data,
            off_payload=payload,
            off_last_modified=modified,
        )
        if revision is None and images_changed and product.current_revision:
            apply_revision(
                product, product.current_revision, list(product.current_revision.assessments.all())
            )
        for name in conflicts:
            raise_flag(
                ReviewFlag.Kind.DATA_CONFLICT,
                product,
                name,
                {"off_value": off_data.get(name), "local_value": data.get(name)},
            )
        if images_changed:
            transaction.on_commit(lambda pk=str(product.pk): _queue_image_cache(pk))
        product.off_synced_at = now
        product.off_last_attempt_at = now
        product.off_sync_status = Product.SyncStatus.CURRENT
        product.off_last_error = ""
        product.save(
            update_fields=[
                "off_synced_at",
                "off_last_attempt_at",
                "off_sync_status",
                "off_last_error",
                "updated_at",
            ]
        )
    return product
