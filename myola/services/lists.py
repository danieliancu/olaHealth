"""Product marks (favourite, buy often, avoid, hidden) and recently viewed products."""

from __future__ import annotations

from django.db import transaction

from myola.models import LIMITS, ProductMark, ProductVisit

EXCLUSIVE = {
    ProductMark.Kind.BUY_OFTEN: ProductMark.Kind.AVOID,
    ProductMark.Kind.AVOID: ProductMark.Kind.BUY_OFTEN,
}


def marks_for(user, product) -> set[str]:
    if not user.is_authenticated:
        return set()
    return set(
        ProductMark.objects.filter(user=user, product=product).values_list("kind", flat=True)
    )


@transaction.atomic
def toggle(user, product, kind: str) -> bool:
    """Toggle a mark; returns True when the mark is now on."""
    existing = ProductMark.objects.filter(user=user, product=product, kind=kind)
    if existing.exists():
        existing.delete()
        return False
    other = EXCLUSIVE.get(kind)
    if other:
        ProductMark.objects.filter(user=user, product=product, kind=other).delete()
    ProductMark.objects.create(user=user, product=product, kind=kind)
    _trim(ProductMark.objects.filter(user=user, kind=kind), LIMITS["marks"])
    return True


def record_visit(user, product, code: str = "") -> None:
    if not user.is_authenticated:
        return
    ProductVisit.objects.create(user=user, product=product, code=(code or "")[:32])
    _trim(ProductVisit.objects.filter(user=user), LIMITS["visits"])


def recent(user, *, scans_only: bool = False, limit: int = 50) -> list:
    """Most recent visit per product (newest first)."""
    qs = ProductVisit.objects.filter(user=user).select_related("product")
    if scans_only:
        qs = qs.exclude(code="")
    seen, out = set(), []
    for v in qs.order_by("-created_at")[:300]:
        if v.product_id in seen:
            continue
        seen.add(v.product_id)
        out.append(v)
        if len(out) >= limit:
            break
    return out


def hidden_ids(user) -> set:
    if not user.is_authenticated:
        return set()
    return set(
        ProductMark.objects.filter(user=user, kind=ProductMark.Kind.HIDDEN).values_list(
            "product_id", flat=True
        )
    )


def _trim(qs, keep: int) -> None:
    stale = list(qs.order_by("-created_at").values_list("pk", flat=True)[keep:])
    if stale:
        qs.model.objects.filter(pk__in=stale).delete()
