from __future__ import annotations

import secrets
import uuid
from typing import Any

from django.conf import settings
from django.db import models


def new_token() -> str:
    return secrets.token_urlsafe(32)


class Contribution(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft"
        SUBMITTED = "submitted"
        APPROVED = "approved"
        REJECTED = "rejected"
        CONFLICT = "conflict"

    class ProblemType(models.TextChoices):
        NEW_PRODUCT = "new_product", "Product is missing"
        WRONG_PRODUCT = "wrong_product", "This is not my product"
        INCORRECT_DATA = "incorrect_data", "Information is wrong"
        MISSING_DATA = "missing_data", "Information is missing"
        PHOTOS = "photos", "New or better photos"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    access_token = models.CharField(max_length=64, unique=True, default=new_token, editable=False)
    session_key = models.CharField(max_length=40, blank=True)
    # The signed-in contributor, if any: only used to tell them the moderation outcome.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="contributions",
    )
    product = models.ForeignKey(
        "catalog.Product",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="contributions",
    )
    code = models.CharField(max_length=32, blank=True)
    code_format = models.CharField(max_length=16, blank=True)
    country = models.CharField(max_length=60, blank=True)
    label_lang = models.CharField(max_length=8, blank=True)  # language printed on the label
    base_revision = models.ForeignKey(
        "catalog.ProductRevision",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    problem_type = models.CharField(max_length=20, choices=ProblemType.choices)
    proposed = models.JSONField(default=dict, blank=True)  # canonical field -> new value
    note = models.TextField(blank=True, max_length=2000)
    validation_warnings = models.JSONField(default=list, blank=True)
    consent_publication = models.BooleanField(default=False)
    consent_text = models.TextField(blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)
    moderator = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    moderation_reason = models.TextField(blank=True)
    resulting_revision = models.ForeignKey(
        "catalog.ProductRevision",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    moderated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        permissions = [("moderate_contribution", "Can approve or reject contributions")]
        indexes = [
            models.Index(fields=["status", "submitted_at"], name="contribution_status"),
            models.Index(fields=["session_key"], name="contribution_session"),
        ]

    def __str__(self) -> str:
        return f"{self.get_problem_type_display()} – {self.code or self.product_id}"

    @property
    def touched_fields(self) -> set[str]:
        return set(self.proposed)


class ContributionImage(models.Model):
    class Kind(models.TextChoices):
        FRONT = "front"
        INGREDIENTS = "ingredients"
        NUTRITION = "nutrition"
        PACKAGING = "packaging"
        OTHER = "other"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    contribution = models.ForeignKey(Contribution, on_delete=models.CASCADE, related_name="images")
    kind = models.CharField(max_length=12, choices=Kind.choices)
    lang = models.CharField(max_length=8, blank=True)
    file = models.FileField(upload_to="contrib/%Y/%m/")  # private storage, re-encoded JPEG
    ocr_file = models.FileField(upload_to="ocr/%Y/%m/", blank=True)  # OCR derivative
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64)
    is_public = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["contribution", "sha256"], name="uniq_image_per_contrib"
            )
        ]

    def __str__(self) -> str:
        return f"{self.kind} photo"


class OCRRun(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued"
        RUNNING = "running"
        DONE = "done"
        FAILED = "failed"

    image = models.ForeignKey(ContributionImage, on_delete=models.CASCADE, related_name="ocr_runs")
    engine = models.CharField(max_length=20, default="tesseract")
    engine_version = models.CharField(max_length=40)
    lang = models.CharField(max_length=20)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.QUEUED)
    attempts = models.PositiveSmallIntegerField(default=0)
    text = models.TextField(blank=True)
    words = models.JSONField(default=list, blank=True)  # [{text, conf, box:[x,y,w,h]}]
    parsed = models.JSONField(default=dict, blank=True)  # nutrition parser proposals
    error = models.CharField(max_length=200, blank=True)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["image", "engine", "engine_version", "lang"], name="uniq_ocr_run"
            )
        ]

    def __str__(self) -> str:
        return f"OCR {self.pk} ({self.status})"


class ModerationAudit(models.Model):
    contribution = models.ForeignKey(Contribution, on_delete=models.CASCADE, related_name="audit")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    action = models.CharField(max_length=30)
    reason = models.TextField(blank=True)
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.action} {self.created_at:%Y-%m-%d}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise RuntimeError("Audit entries are append-only.")
        super().save(*args, **kwargs)
