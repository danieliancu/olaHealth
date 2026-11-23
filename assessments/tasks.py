"""Background jobs for the Foodo Score (run by the django-q2 worker, see setup_schedules)."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def recompute_foodo_scores(batch: int = 500) -> int:
    """Store the current ``score.v2`` result on current revisions that lack it, hold another
    rules version or no provenance (``assessments.score.needs_recompute``).

    Only the derived ``ProductRevision.foodo_score`` column is written (``bulk_update``): the
    revision data, approved corrections and history are untouched. Returns how many were
    updated; run repeatedly until it returns 0 (``manage.py recompute_foodo_scores``, which
    also has ``--all`` to recompute every current revision).
    """
    from assessments.score import for_revision, needs_recompute
    from catalog.models import Product, ProductRevision

    current = Product.objects.filter(current_revision__isnull=False).values("current_revision_id")
    revisions = ProductRevision.objects.filter(pk__in=current).prefetch_related("assessments")
    candidates = ProductRevision.objects.filter(pk__in=current).only("pk", "foodo_score")
    candidates = candidates.order_by("pk")
    ids = []
    for r in candidates.iterator(chunk_size=2000):
        if needs_recompute(r.foodo_score):
            ids.append(r.pk)
            if len(ids) >= batch:
                break
    changed = []
    revisions = revisions.filter(pk__in=ids)
    for revision in revisions:
        result = for_revision(revision)
        if result != revision.foodo_score:
            revision.foodo_score = result
            changed.append(revision)
    ProductRevision.objects.bulk_update(changed, ["foodo_score"], batch_size=500)
    if changed:
        logger.info("recompute_foodo_scores: %s revisions updated", len(changed))
    return len(changed)
