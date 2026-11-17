"""Worker jobs for contributions: OCR and retention."""

from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.core.files.storage import default_storage
from django.db.models import Q
from django.utils import timezone

from contributions.models import Contribution, ContributionImage
from contributions.services import ocr

logger = logging.getLogger(__name__)


def run_ocr(run_id: int) -> str:
    return ocr.run_ocr(run_id)


def _delete_image_files(images) -> int:
    count = 0
    for img in images:
        for f in (img.file, img.ocr_file):
            if f:
                f.delete(save=False)
                count += 1
    return count


def purge_expired_drafts() -> dict[str, int]:
    """Drafts untouched for DRAFT_RETENTION_DAYS and images of contributions rejected more
    than REJECTED_IMAGE_RETENTION_DAYS ago are deleted with their files."""
    now = timezone.now()
    drafts = Contribution.objects.filter(
        status=Contribution.Status.DRAFT,
        updated_at__lt=now - timedelta(days=settings.DRAFT_RETENTION_DAYS),
    )
    files = _delete_image_files(ContributionImage.objects.filter(contribution__in=drafts))
    deleted_drafts = drafts.delete()[0]
    rejected_images = ContributionImage.objects.filter(
        contribution__status=Contribution.Status.REJECTED,
        contribution__moderated_at__lt=now - timedelta(days=settings.REJECTED_IMAGE_RETENTION_DAYS),
    )
    files += _delete_image_files(rejected_images)
    rejected_images.delete()
    return {"drafts": deleted_drafts, "files": files}


def delete_orphan_files() -> int:
    """Remove stored files no database row refers to (e.g. after a crash mid-upload)."""
    referenced = set()
    for file_name, ocr_name in ContributionImage.objects.values_list("file", "ocr_file"):
        referenced.update(n for n in (file_name, ocr_name) if n)
    cutoff = timezone.now() - timedelta(hours=1)
    removed = 0
    for root in ("contrib", "ocr"):
        removed += _walk_and_delete(root, referenced, cutoff)
    return removed


def _walk_and_delete(path: str, referenced: set[str], cutoff) -> int:
    if not default_storage.exists(path):
        return 0
    dirs, files = default_storage.listdir(path)
    removed = 0
    for name in files:
        full = f"{path}/{name}"
        if full not in referenced and default_storage.get_modified_time(full) < cutoff:
            default_storage.delete(full)
            removed += 1
    for d in dirs:
        removed += _walk_and_delete(f"{path}/{d}", referenced, cutoff)
    return removed


def stale_open_contributions() -> int:
    """Count submitted contributions waiting more than a week (logged for moderators)."""
    count = Contribution.objects.filter(
        Q(status=Contribution.Status.SUBMITTED) | Q(status=Contribution.Status.CONFLICT),
        submitted_at__lt=timezone.now() - timedelta(days=7),
    ).count()
    if count:
        logger.info("%d contributions waiting for moderation for more than 7 days", count)
    return count
