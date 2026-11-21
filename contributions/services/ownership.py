"""Products a user brought to AskFoodo, and deleting them.

A user may delete a product only when it is theirs alone: it was created from their approved
"Product is missing" contribution, it exists only here (not on Open Food Facts), and no one
else's change to it has been approved. Diary entries keep their own copy of the values; the
product's photos, revisions, flags and everyone's marks go with it. The contributions stay
(their product link is cleared) with an audit row, so moderation history is not lost.
"""

from __future__ import annotations

import logging

from django.db import transaction

from catalog.models import Product
from contributions.models import Contribution, ModerationAudit

logger = logging.getLogger(__name__)


def can_delete(user, product: Product) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    if product.off_sync_status != Product.SyncStatus.LOCAL_ONLY:
        return False
    approved = Contribution.objects.filter(product=product, status=Contribution.Status.APPROVED)
    created_by_user = approved.filter(
        user=user, problem_type=Contribution.ProblemType.NEW_PRODUCT
    ).exists()
    others = approved.exclude(user=user).exists()
    return created_by_user and not others


def delete_product(user, product: Product) -> bool:
    """Delete ``product`` if ``user`` may (see ``can_delete``). True when it was deleted."""
    with transaction.atomic():
        product = Product.objects.select_for_update().filter(pk=product.pk).first()
        if product is None or not can_delete(user, product):
            return False
        pk, code = str(product.pk), product.off_code or ""
        for c in Contribution.objects.filter(product=product):
            ModerationAudit.objects.create(
                contribution=c,
                actor=user,
                action="product_deleted",
                reason="Deleted by the person who added it.",
                details={"product": pk, "code": code},
            )
        product.delete()
    logger.info("Product %s deleted by the user who added it", pk)
    return True
