"""Barcode lookup: local catalogue first, Open Food Facts when the product is absent.

1. Parse and normalise the code (``invalid_code`` on failure).
2. Local identifier found and fresh -> return it.
3. Found but stale -> return it with its date and schedule a background refresh.
4. Absent -> short negative cache for explicit OFF "not found" answers.
5. Otherwise take an expiring lease (one fetch per code across all processes), call OFF
   outside any transaction, persist the result with provenance.
6. External errors never remove local data.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

from django.conf import settings
from django.db.models import F
from django.utils import timezone

from catalog.models import LookupState, Product, ProductIdentifier
from core.coordination import acquire_lease, release_lease
from off.client import OFFClient, OFFError, default_client
from off.codes import InvalidCode, ParsedCode, parse_code

from .importer import import_off_product

logger = logging.getLogger(__name__)

Status = Literal["found", "not_found", "pending", "error"]


@dataclass
class LookupResult:
    status: Status
    code: ParsedCode | None = None
    product: Product | None = None
    error: str = ""
    detail: str = ""
    message: str = ""
    source: Literal["local", "off", ""] = ""
    stale: bool = False
    refresh_scheduled: bool = False
    retry_after: int | None = None


def find_local(parsed: ParsedCode, country: str = "") -> Product | None:
    contexts: list[str]
    if parsed.kind == "gtin":
        contexts = [""]
    elif parsed.kind == "restricted":
        contexts = [f"country:{country}"] if country else []
        contexts += ["unscoped", "off"]
    else:
        contexts = ["unverified"]
    idents = {
        i.context: i
        for i in ProductIdentifier.objects.select_related("product__current_revision").filter(
            normalized=parsed.identity_key, context__in=contexts, product__is_demo=False
        )
    }
    for ctx in contexts:
        ident = idents.get(ctx)
        if ident and ident.product.current_revision_id:
            return ident.product
    product = (
        Product.objects.select_related("current_revision")
        .filter(off_code=parsed.off_code, is_demo=False, current_revision__isnull=False)
        .first()
    )
    # Same OFF code without a matching identifier yet (e.g. a different spelling).
    return product


def schedule_refresh(product: Product) -> bool:
    """Queue one background refresh per product (deduplicated by a lease)."""
    if not product.off_code:
        return False
    key = f"refresh-queued:{product.off_code}"
    owner = acquire_lease(key, seconds=15 * 60)
    if owner is None:
        return False
    from django_q.tasks import async_task

    async_task(
        "catalog.tasks.refresh_product",
        str(product.pk),
        key,
        owner,
        task_name=f"refresh-{product.off_code}",
        group="off-refresh",
    )
    return True


def lookup(
    raw: str,
    detected_format: str = "manual",
    *,
    country: str = "",
    allow_bad_check: bool = False,
    client: OFFClient | None = None,
    schedule: bool = True,
) -> LookupResult:
    try:
        parsed = parse_code(raw, detected_format, allow_bad_check=allow_bad_check)
    except InvalidCode as exc:
        return LookupResult("error", error="invalid_code", detail=exc.reason, message=exc.message)

    product = find_local(parsed, country)
    if product is not None:
        stale = product.is_stale() or product.off_sync_status == Product.SyncStatus.REFRESH_FAILED
        scheduled = schedule_refresh(product) if (stale and schedule) else False
        return LookupResult(
            "found", parsed, product, source="local", stale=stale, refresh_scheduled=scheduled
        )

    now = timezone.now()
    state = LookupState.objects.filter(off_code=parsed.off_code).first()
    if state and state.not_found_until and state.not_found_until > now:
        return LookupResult("not_found", parsed, source="local")

    return fetch_from_off(parsed, client=client)


def fetch_from_off(parsed: ParsedCode, *, client: OFFClient | None = None) -> LookupResult:
    key = f"off-lookup:{parsed.off_code}"
    owner = acquire_lease(key, settings.OFF_LEASE_SECONDS)
    if owner is None:
        return LookupResult(
            "pending", parsed, message="This product is being looked up.", retry_after=2
        )
    client = client or default_client()
    now = timezone.now()
    try:
        state, _ = LookupState.objects.get_or_create(off_code=parsed.off_code)
        before = client.requests_made
        try:
            payload = client.get_product(parsed.off_code)
        except OFFError as exc:
            LookupState.objects.filter(pk=state.pk).update(
                last_error=exc.kind,
                last_attempt_at=now,
                off_requests=F("off_requests") + (client.requests_made - before),
                not_found_until=(
                    now + timedelta(minutes=settings.OFF_NEGATIVE_TTL_MINUTES)
                    if exc.kind == "not_found"
                    else None
                ),
            )
            if exc.kind == "not_found":
                return LookupResult("not_found", parsed, source="off")
            logger.warning("OFF lookup failed for %s: %s", parsed.off_code, exc.kind)
            return LookupResult(
                "error", parsed, error=exc.kind, source="off", retry_after=exc.retry_after
            )
        LookupState.objects.filter(pk=state.pk).update(
            last_error="",
            last_attempt_at=now,
            not_found_until=None,
            off_requests=F("off_requests") + (client.requests_made - before),
        )
        product = import_off_product(payload, parsed)
        return LookupResult("found", parsed, product, source="off")
    finally:
        release_lease(key, owner)


def refresh(product: Product, *, client: OFFClient | None = None) -> str:
    """Re-import one product from OFF. Local data is kept on any error."""
    from off.codes import parse_code as _parse

    client = client or default_client()
    now = timezone.now()
    key = f"off-lookup:{product.off_code}"
    owner = acquire_lease(key, settings.OFF_LEASE_SECONDS)
    if owner is None:
        return "busy"
    try:
        try:
            payload = client.get_product(product.off_code)
        except OFFError as exc:
            Product.objects.filter(pk=product.pk).update(
                off_sync_status=Product.SyncStatus.REFRESH_FAILED,
                off_last_error=exc.kind,
                off_last_attempt_at=now,
            )
            return exc.kind
        try:
            parsed = _parse(str(payload.get("code") or product.off_code), "manual")
        except InvalidCode:
            parsed = None
        import_off_product(payload, parsed)
        return "ok"
    finally:
        release_lease(key, owner)
