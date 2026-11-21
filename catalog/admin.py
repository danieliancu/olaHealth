from django.contrib import admin
from django.utils import timezone
from django.utils.html import format_html
from unfold.admin import ModelAdmin, TabularInline

from assessments.models import AssessmentSnapshot
from catalog.models import (
    Category,
    Country,
    Product,
    ProductIdentifier,
    ProductImage,
    ProductRevision,
    ReviewFlag,
)


class IdentifierInline(TabularInline):
    model = ProductIdentifier
    extra = 0
    readonly_fields = ("raw_value", "detected_format", "normalized", "off_code", "kind", "context")
    can_delete = False

    def has_add_permission(self, request, obj=None) -> bool:
        return False


@admin.register(Product)
class ProductAdmin(ModelAdmin):
    list_display = (
        "name",
        "brand",
        "off_code",
        "nutriscore_grade",
        "completeness",
        "off_sync_status",
        "updated_at",
    )
    list_filter = ("off_sync_status", "nutriscore_grade")
    search_fields = ("name", "brand", "off_code", "identifiers__normalized")
    readonly_fields = [f.name for f in Product._meta.fields]
    inlines = [IdentifierInline]

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False


class SnapshotInline(TabularInline):
    model = AssessmentSnapshot
    extra = 0
    can_delete = False
    readonly_fields = (
        "indicator",
        "origin",
        "data_status",
        "value",
        "algorithm_version",
        "invalidated_by_local_change",
        "computed_at",
    )
    fields = readonly_fields

    def has_add_permission(self, request, obj=None) -> bool:
        return False


@admin.register(ProductRevision)
class ProductRevisionAdmin(ModelAdmin):
    list_display = ("product", "number", "source", "created_at")
    list_filter = ("source",)
    readonly_fields = [f.name for f in ProductRevision._meta.fields]
    inlines = [SnapshotInline]

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False


@admin.register(ProductImage)
class ProductImageAdmin(ModelAdmin):
    list_display = ("product", "kind", "source", "license", "is_active", "created_at")
    list_filter = ("source", "kind", "is_active")
    readonly_fields = [f.name for f in ProductImage._meta.fields]

    def has_add_permission(self, request) -> bool:
        return False


@admin.register(ReviewFlag)
class ReviewFlagAdmin(ModelAdmin):
    list_display = ("kind", "field", "product_link", "status", "created_at")
    list_filter = ("kind", "status", ("created_at", admin.DateFieldListFilter))
    readonly_fields = (
        "product",
        "kind",
        "field",
        "fingerprint",
        "details",
        "created_at",
        "resolved_at",
        "resolved_by",
        "status",
    )
    actions = ["mark_resolved"]

    @admin.display(description="Product")
    def product_link(self, obj: ReviewFlag) -> str:
        if not obj.product_id:
            return "—"
        return format_html('<a href="/p/{}/">{}</a>', obj.product_id, obj.product)

    @admin.action(description="Mark selected flags as resolved", permissions=["moderate"])
    def mark_resolved(self, request, queryset) -> None:
        queryset.filter(status=ReviewFlag.Status.OPEN).update(
            status=ReviewFlag.Status.RESOLVED, resolved_at=timezone.now(), resolved_by=request.user
        )

    def has_moderate_permission(self, request) -> bool:
        return request.user.has_perm("contributions.moderate_contribution")

    def has_add_permission(self, request) -> bool:
        return False


@admin.register(Category)
class CategoryAdmin(ModelAdmin):
    """Reference data imported by ``sync_taxonomies`` (read only)."""

    list_display = ("name", "tag")
    search_fields = ("name", "tag")
    readonly_fields = ("tag", "name", "parents")

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False


@admin.register(Country)
class CountryAdmin(ModelAdmin):
    list_display = ("name", "code", "tag")
    search_fields = ("name", "tag", "code")
    readonly_fields = ("tag", "name", "code")

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False
