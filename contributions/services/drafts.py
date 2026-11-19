"""Anonymous contribution drafts: creation, photos, proposal, submission.

Flow: photos (optional) -> check (edit/confirm values, validated) -> submit (consent).
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.utils import timezone

from catalog.models import Product
from contributions.models import Contribution, ContributionImage
from contributions.services.ocr import queue_ocr
from contributions.validation import ValidationReport, validate_facts
from core.images import ImageRejected, validate_and_reencode

CONSENT_TEXT = (
    "I took these photos or have the right to share them. I agree that my photos and the "
    "information I submit may be published by FoodoTrack after review and reused by others "
    "under the Open Database License (data) and Creative Commons Attribution-ShareAlike 4.0 "
    "(photos)."
)


def create_draft(
    *,
    session_key: str,
    problem_type: str,
    product: Product | None = None,
    code: str = "",
    code_format: str = "",
    country: str = "",
) -> Contribution:
    return Contribution.objects.create(
        session_key=session_key,
        problem_type=problem_type,
        product=product,
        base_revision=product.current_revision if product else None,
        code=code[:32],
        code_format=code_format[:16],
        country=country[:60],
    )


def _editable(c: Contribution) -> None:
    if c.status != Contribution.Status.DRAFT:
        raise ImageRejected("This contribution can no longer be changed.")


def add_image(
    contribution: Contribution, upload: Any, kind: str, lang: str = ""
) -> ContributionImage:
    _editable(contribution)
    if contribution.images.count() >= settings.UPLOADS_PER_CONTRIBUTION:
        raise ImageRejected(
            f"A contribution can have at most {settings.UPLOADS_PER_CONTRIBUTION} photos."
        )
    clean = validate_and_reencode(upload)
    image = ContributionImage(
        contribution=contribution,
        kind=kind,
        lang=(lang or contribution.label_lang)[:8],
        width=clean.width,
        height=clean.height,
        sha256=clean.sha256,
    )
    image.file.save(f"{image.pk}.jpg", ContentFile(clean.content), save=False)
    try:
        with transaction.atomic():
            image.save()
    except IntegrityError as exc:
        image.file.delete(save=False)
        raise ImageRejected("This photo was already added.") from exc
    queue_ocr(image)
    return image


def save_details(contribution: Contribution, country: str, label_lang: str) -> None:
    _editable(contribution)
    Contribution.objects.filter(pk=contribution.pk).update(
        country=country[:60], label_lang=label_lang[:8], updated_at=timezone.now()
    )
    contribution.images.filter(lang="").update(lang=label_lang[:8])


def check_proposal(proposed: dict[str, Any]) -> ValidationReport:
    nutrition = proposed.get("nutrition")
    if nutrition and nutrition.get("facts"):
        # Each basis/preparation group is validated on its own.
        report = ValidationReport()
        groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for f in nutrition["facts"]:
            groups.setdefault((f["basis"], f["preparation"]), []).append(f)
        for (basis, _prep), facts in groups.items():
            r = validate_facts(facts, basis, proposed.get("serving_size") or "")
            report.errors += r.errors
            report.warnings += r.warnings
        return report
    return ValidationReport()


def save_proposal(
    contribution: Contribution, proposed: dict[str, Any], note: str
) -> ValidationReport:
    """Store the checked values in the draft. Errors are returned and nothing is stored."""
    _editable(contribution)
    report = check_proposal(proposed)
    if report.ok:
        Contribution.objects.filter(pk=contribution.pk, status=Contribution.Status.DRAFT).update(
            proposed=proposed,
            note=note[:2000],
            validation_warnings=report.warnings,
            updated_at=timezone.now(),
        )
        contribution.refresh_from_db()
    return report


def submit(contribution: Contribution, consent: bool) -> list[str]:
    """Submit the stored proposal for moderation. Returns errors (empty on success)."""
    if not consent:
        return ["Please confirm you agree before submitting."]
    report = check_proposal(contribution.proposed or {})
    if not report.ok:
        return report.errors
    has_images = contribution.images.exists()
    if not contribution.proposed and not has_images and not contribution.note.strip():
        return ["Add at least one photo, a correction or a note."]
    if contribution.problem_type == Contribution.ProblemType.NEW_PRODUCT and not contribution.code:
        return ["A new product needs its barcode."]
    updated = Contribution.objects.filter(
        pk=contribution.pk, status=Contribution.Status.DRAFT
    ).update(
        validation_warnings=report.warnings,
        consent_publication=True,
        consent_text=CONSENT_TEXT,
        status=Contribution.Status.SUBMITTED,
        submitted_at=timezone.now(),
    )
    if not updated:
        return ["This contribution was already submitted."]
    contribution.refresh_from_db()
    return []
