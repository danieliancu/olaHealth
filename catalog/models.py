from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.contrib.postgres.indexes import GinIndex, OpClass
from django.db import models
from django.db.models.functions import Upper
from django.utils import timezone


class Product(models.Model):
    """Stable product identity. Its content lives in immutable revisions."""

    class SyncStatus(models.TextChoices):
        CURRENT = "current"
        REFRESH_FAILED = "refresh_failed"
        LOCAL_ONLY = "local_only"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    current_revision = models.ForeignKey(
        "ProductRevision", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    off_code = models.CharField(max_length=14, null=True, blank=True, unique=True)
    locked_fields = ArrayField(models.CharField(max_length=40), default=list, blank=True)

    # Denormalised from the current revision for search, filters and sorting.
    name = models.CharField(max_length=300, blank=True)
    brand = models.CharField(max_length=300, blank=True)
    quantity = models.CharField(max_length=100, blank=True)
    search_text = models.TextField(blank=True)
    categories = ArrayField(models.CharField(max_length=200), default=list, blank=True)
    countries = ArrayField(models.CharField(max_length=100), default=list, blank=True)
    nutriscore_grade = models.CharField(max_length=1, blank=True)
    nutriscore_version = models.CharField(max_length=10, blank=True)
    nova_group = models.PositiveSmallIntegerField(null=True, blank=True)
    completeness = models.PositiveSmallIntegerField(default=0)
    front_image_url = models.URLField(max_length=500, blank=True)
    # Data quality (catalog/quality.py): what is missing, and whether the product may appear
    # in search, suggestions and alternatives. Set with each revision.
    quality_issues = ArrayField(models.CharField(max_length=30), default=list, blank=True)
    listable = models.BooleanField(default=True, db_index=True)

    off_synced_at = models.DateTimeField(null=True, blank=True)
    off_sync_status = models.CharField(
        max_length=20, choices=SyncStatus.choices, default=SyncStatus.LOCAL_ONLY
    )
    off_last_error = models.CharField(max_length=40, blank=True)
    off_last_attempt_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            GinIndex(
                OpClass(Upper("search_text"), name="gin_trgm_ops"), name="product_search_trgm"
            ),
            GinIndex(fields=["categories"], name="product_categories_gin"),
            GinIndex(fields=["countries"], name="product_countries_gin"),
            models.Index(fields=["nutriscore_grade", "nutriscore_version"], name="product_ns"),
            models.Index(fields=["off_sync_status", "off_synced_at"], name="product_sync"),
        ]

    def __str__(self) -> str:
        return self.name or str(self.id)

    @property
    def quality_labels(self) -> list[str]:
        from catalog.quality import LABELS

        return [LABELS.get(i, i) for i in self.quality_issues or []]

    @property
    def data(self) -> dict[str, Any]:
        return self.current_revision.data if self.current_revision else {}

    def is_stale(self, now=None) -> bool:
        if self.off_synced_at is None:
            return False
        now = now or timezone.now()
        return now - self.off_synced_at > timedelta(hours=settings.OFF_FRESH_TTL_HOURS)

    @property
    def sync_label(self) -> str:
        """current / stale / refresh_failed / local_only (shown next to each indicator)."""
        if self.off_sync_status == self.SyncStatus.REFRESH_FAILED:
            return "refresh_failed"
        if self.off_sync_status == self.SyncStatus.LOCAL_ONLY:
            return "local_only"
        return "stale" if self.is_stale() else "current"


class ProductIdentifier(models.Model):
    class Kind(models.TextChoices):
        GTIN = "gtin"
        RESTRICTED = "restricted"
        UNVERIFIED = "unverified"

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="identifiers")
    raw_value = models.CharField(max_length=32)
    detected_format = models.CharField(max_length=16, blank=True)
    normalized = models.CharField(max_length=14)
    off_code = models.CharField(max_length=14)
    kind = models.CharField(max_length=12, choices=Kind.choices)
    # Context scopes codes that are not globally unique (store/country). Empty for GTINs.
    context = models.CharField(max_length=40, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["normalized", "context"], name="uniq_identifier_ctx"),
        ]
        indexes = [models.Index(fields=["off_code"], name="identifier_off_code")]

    def __str__(self) -> str:
        return f"{self.normalized} ({self.kind}{', ' + self.context if self.context else ''})"


class ImmutableError(RuntimeError):
    pass


class ProductRevision(models.Model):
    """Immutable snapshot of product data with per-field provenance."""

    class Source(models.TextChoices):
        OFF_IMPORT = "off_import"
        CONTRIBUTION = "contribution"

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="revisions")
    number = models.PositiveIntegerField()
    source = models.CharField(max_length=20, choices=Source.choices)
    data = models.JSONField()
    content_hash = models.CharField(max_length=64)
    # field -> {"source": "off"|"contribution", "at": iso, "contribution": id?}
    field_sources = models.JSONField(default=dict)
    off_data = models.JSONField(null=True, blank=True)  # mapped OFF data used as source
    off_payload = models.JSONField(null=True, blank=True)  # raw OFF product object
    off_last_modified = models.DateTimeField(null=True, blank=True)
    base_revision = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    contribution = models.ForeignKey(
        "contributions.Contribution",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["product", "number"], name="uniq_revision_number"),
        ]
        ordering = ["-number"]

    def __str__(self) -> str:
        return f"{self.product_id} r{self.number}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ImmutableError("Published revisions are immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any):
        raise ImmutableError("Published revisions are immutable.")


class NutrientFact(models.Model):
    """Relational copy of a revision's nutrition facts, for comparison and queries."""

    revision = models.ForeignKey(ProductRevision, on_delete=models.CASCADE, related_name="facts")
    nutrient = models.CharField(max_length=30)
    basis = models.CharField(max_length=24)
    preparation = models.CharField(max_length=10)
    status = models.CharField(max_length=16)
    value = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    unit = models.CharField(max_length=8)
    modifier = models.CharField(max_length=2, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["revision", "nutrient", "basis", "preparation"], name="uniq_fact"
            ),
            models.CheckConstraint(
                condition=models.Q(value__isnull=True) | models.Q(value__gte=0),
                name="fact_non_negative",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.nutrient} {self.basis} {self.preparation}"


class ProductImage(models.Model):
    class Kind(models.TextChoices):
        FRONT = "front"
        INGREDIENTS = "ingredients"
        NUTRITION = "nutrition"
        PACKAGING = "packaging"
        OTHER = "other"

    class Source(models.TextChoices):
        OFF = "off"
        CONTRIBUTION = "contribution"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="images")
    kind = models.CharField(max_length=12, choices=Kind.choices)
    lang = models.CharField(max_length=8, blank=True)
    source = models.CharField(max_length=12, choices=Source.choices)
    license = models.CharField(max_length=80)
    attribution = models.CharField(max_length=300)
    remote_url = models.URLField(max_length=500, blank=True)
    contribution_image = models.OneToOneField(
        "contributions.ContributionImage",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="published_as",
    )
    is_active = models.BooleanField(default=True)
    # Switched off by staff as unsuitable: never shown, and an OFF refresh never brings it back.
    hidden_by_staff = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["product", "source", "kind", "remote_url"],
                condition=models.Q(source="off"),
                name="uniq_off_image",
            )
        ]

    def __str__(self) -> str:
        return f"{self.kind} ({self.source})"


class LookupState(models.Model):
    """Per-OFF-code lookup memory: negative cache and last error."""

    off_code = models.CharField(max_length=14, unique=True)
    not_found_until = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=40, blank=True)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    off_requests = models.PositiveIntegerField(default=0)

    def __str__(self) -> str:
        return self.off_code


class ReviewFlag(models.Model):
    class Kind(models.TextChoices):
        IDENTITY_CONFLICT = "identity_conflict"
        DATA_CONFLICT = "data_conflict"
        UNUSUAL_VALUE = "unusual_value"

    class Status(models.TextChoices):
        OPEN = "open"
        RESOLVED = "resolved"

    product = models.ForeignKey(
        Product, null=True, blank=True, on_delete=models.CASCADE, related_name="flags"
    )
    kind = models.CharField(max_length=20, choices=Kind.choices)
    field = models.CharField(max_length=40, blank=True)
    fingerprint = models.CharField(max_length=64)
    details = models.JSONField(default=dict)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["kind", "fingerprint"],
                condition=models.Q(status="open"),
                name="uniq_open_flag",
            )
        ]

    def __str__(self) -> str:
        return f"{self.kind} {self.field}".strip()


class Category(models.Model):
    """Full Open Food Facts category taxonomy (reference data, see ``sync_taxonomies``)."""

    tag = models.CharField(max_length=200, primary_key=True)
    name = models.CharField(max_length=300)
    parents = ArrayField(models.CharField(max_length=200), default=list, blank=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "categories"

    def __str__(self) -> str:
        return self.name


class Country(models.Model):
    """Full Open Food Facts country taxonomy (reference data, see ``sync_taxonomies``)."""

    tag = models.CharField(max_length=100, primary_key=True)
    name = models.CharField(max_length=200)
    code = models.CharField(max_length=3, blank=True)  # ISO 3166-1 alpha-2 when known

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "countries"

    def __str__(self) -> str:
        return self.name
