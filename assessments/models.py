from django.db import models


class AssessmentSnapshot(models.Model):
    """One indicator as published by its source, bound to one product revision."""

    class DataStatus(models.TextChoices):
        AVAILABLE = "available"
        MISSING = "missing_data"
        NOT_APPLICABLE = "not_applicable"
        CONFLICTING = "conflicting"

    class Origin(models.TextChoices):
        OFF_COMPUTED = "off_computed"
        PACKAGING_DECLARED = "packaging_declared"

    revision = models.ForeignKey(
        "catalog.ProductRevision", on_delete=models.CASCADE, related_name="assessments"
    )
    indicator = models.CharField(max_length=30)
    data_status = models.CharField(max_length=20, choices=DataStatus.choices)
    value = models.JSONField(default=dict)
    algorithm_version = models.CharField(max_length=20, blank=True)
    origin = models.CharField(max_length=20, choices=Origin.choices)
    warnings = models.JSONField(default=list)
    # Hash of the canonical inputs the source used. If the revision's own values differ
    # (local correction), the indicator cannot be shown as current.
    inputs_hash = models.CharField(max_length=64)
    invalidated_by_local_change = models.BooleanField(default=False)
    computed_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["revision", "indicator", "origin"], name="uniq_assessment"
            )
        ]

    def __str__(self) -> str:
        return f"{self.indicator} ({self.origin}) r{self.revision_id}"
