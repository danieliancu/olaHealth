"""Background jobs run by the django-q2 worker (see ``setup_schedules``)."""

from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from catalog.models import LookupState, Product
from catalog.services import lookup as lookup_service
from core.coordination import purge_expired_leases, purge_rate_counters, release_lease

logger = logging.getLogger(__name__)


def refresh_product(product_id: str, queued_key: str = "", queued_owner: str = "") -> str:
    try:
        product = Product.objects.get(pk=product_id)
        return lookup_service.refresh(product)
    except Product.DoesNotExist:
        return "missing"
    finally:
        if queued_key and queued_owner:
            release_lease(queued_key, queued_owner)


def refresh_stale_products() -> dict[str, int]:
    """Refresh a small batch of stale products, within the shared OFF rate limit."""
    cutoff = timezone.now() - timedelta(hours=settings.OFF_FRESH_TTL_HOURS)
    candidates = (
        Product.objects.filter(off_code__isnull=False, is_demo=False)
        .filter(Q(off_synced_at__lt=cutoff) | Q(off_sync_status=Product.SyncStatus.REFRESH_FAILED))
        .order_by("off_last_attempt_at", "off_synced_at")[: settings.OFF_REFRESH_BATCH]
    )
    results: dict[str, int] = {}
    for product in candidates:
        outcome = lookup_service.refresh(product)
        results[outcome] = results.get(outcome, 0) + 1
        if outcome == "rate_limited":
            break
    logger.info("refresh_stale_products: %s", results)
    return results


def purge_coordination() -> dict[str, int]:
    now = timezone.now()
    expired_negative = LookupState.objects.filter(
        not_found_until__lt=now, last_attempt_at__lt=now - timedelta(days=30)
    ).delete()[0]
    return {
        "leases": purge_expired_leases(),
        "rate_counters": purge_rate_counters(),
        "lookup_states": expired_negative,
    }


def cache_off_images(product_id: str) -> int:
    """Queue one download task per OFF photo (each fits in the worker task timeout)."""
    from catalog.image_cache import cached, queue_cache
    from catalog.models import ProductImage

    queued = 0
    for image in ProductImage.objects.filter(product_id=product_id, source="off", is_active=True):
        if cached(image) is None and queue_cache(image):
            queued += 1
    return queued


def cache_off_image(image_id: str) -> bool:
    from catalog.image_cache import ensure_cached
    from catalog.models import ProductImage

    image = ProductImage.objects.filter(pk=image_id).first()
    return bool(image and ensure_cached(image))
