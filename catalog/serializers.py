from __future__ import annotations

from rest_framework import serializers

from catalog import presenters
from catalog.models import Product


class ProductSummarySerializer(serializers.ModelSerializer):
    url = serializers.SerializerMethodField()
    image_url = serializers.SerializerMethodField()
    nutriscore = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            "id",
            "url",
            "name",
            "brand",
            "quantity",
            "image_url",
            "nutriscore",
            "completeness",
        ]

    def get_image_url(self, obj: Product) -> str:
        from core.assets import image_url

        return image_url(obj.front_image_url)

    def get_url(self, obj: Product) -> str:
        from django.urls import reverse

        return reverse("product", args=[obj.pk])

    def get_nutriscore(self, obj: Product) -> dict | None:
        if not obj.nutriscore_grade:
            return None
        return {"grade": obj.nutriscore_grade, "version": obj.nutriscore_version}


class ProductDetailSerializer(ProductSummarySerializer):
    revision = serializers.IntegerField(source="current_revision.number")
    revision_created_at = serializers.DateTimeField(source="current_revision.created_at")
    data = serializers.JSONField(source="current_revision.data")
    field_sources = serializers.JSONField(source="current_revision.field_sources")
    indicators = serializers.SerializerMethodField()
    foodo_score = serializers.SerializerMethodField()
    foodo_score_public = serializers.SerializerMethodField()
    sync_status = serializers.CharField(source="sync_label")
    off_synced_at = serializers.DateTimeField()
    identifiers = serializers.SerializerMethodField()
    images = serializers.SerializerMethodField()

    class Meta(ProductSummarySerializer.Meta):
        fields = [
            *ProductSummarySerializer.Meta.fields,
            "revision",
            "revision_created_at",
            "data",
            "field_sources",
            "indicators",
            "foodo_score",
            "foodo_score_public",
            "sync_status",
            "off_synced_at",
            "identifiers",
            "images",
        ]

    def get_indicators(self, obj: Product) -> list[dict]:
        snapshots = list(obj.current_revision.assessments.all())
        result = []
        for s in snapshots:
            result.append(
                {
                    "indicator": s.indicator,
                    "origin": s.origin,
                    "data_status": s.data_status,
                    "sync_status": obj.sync_label,
                    "current": not s.invalidated_by_local_change,
                    "value": s.value,
                    "algorithm_version": s.algorithm_version,
                    "warnings": s.warnings,
                    "computed_at": s.computed_at,
                }
            )
        return result

    def _score_public(self) -> bool:
        from assessments.score import public_enabled

        if not hasattr(self, "_public"):
            self._public = public_enabled()
        return self._public

    def get_foodo_score_public(self, obj: Product) -> bool:
        return self._score_public()

    def get_foodo_score(self, obj: Product) -> dict | None:
        """``score.v2`` (docs/assessment-rules.md): grade and points are null unless ``ok``.
        ``null`` while the Foodo Score is not public, except for staff (for checking); the
        official Nutri-Score stays in ``nutriscore`` and ``indicators``."""
        from assessments.score import score_for_product

        request = self.context.get("request")
        staff = bool(request and getattr(request.user, "is_staff", False))
        if not (self._score_public() or staff):
            return None
        return score_for_product(obj)

    def get_identifiers(self, obj: Product) -> list[dict]:
        return [
            {"value": i.normalized, "kind": i.kind, "format": i.detected_format}
            for i in obj.identifiers.all()
            if not i.context.startswith("country:")
        ]

    def get_images(self, obj: Product) -> list[dict]:
        from django.urls import reverse

        return [
            {
                "kind": img.kind,
                "url": reverse("off-image", args=[img.id])
                if img.remote_url
                else reverse("public-image", args=[img.id]),
                "original_url": img.remote_url or None,
                "source": img.source,
                "license": img.license,
                "attribution": img.attribution,
            }
            for img in presenters.public_images(obj)
        ]
