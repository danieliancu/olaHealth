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
            "is_demo",
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
