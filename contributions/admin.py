from __future__ import annotations

import json
from typing import Any

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html

from catalog.models import ProductRevision
from contributions.models import Contribution, ContributionImage, ModerationAudit, OCRRun
from contributions.services import moderation
from off.mapping import DATA_FIELDS


class ModerationForm(forms.Form):
    decision = forms.ChoiceField(
        choices=[
            ("approve", "Approve"),
            ("reject", "Reject"),
            ("rebase", "Re-base onto current revision"),
        ],
        widget=forms.RadioSelect,
    )
    reason = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3}),
        required=False,
        help_text="Required for reject and re-base. Stored in the audit log.",
    )


def _pretty(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=1)


class ImageInline(admin.TabularInline):
    model = ContributionImage
    extra = 0
    can_delete = False
    fields = ("preview", "kind", "lang", "width", "height", "is_public")
    readonly_fields = fields

    def preview(self, obj: ContributionImage) -> str:
        url = reverse("staff-image", args=[obj.pk])
        return format_html(
            '<a href="{}" target="_blank" rel="noopener"><img src="{}" alt="{} photo" '
            'style="max-height:120px"></a>',
            url,
            url,
            obj.kind,
        )

    def has_add_permission(self, request, obj=None) -> bool:
        return False


class AuditInline(admin.TabularInline):
    model = ModerationAudit
    extra = 0
    can_delete = False
    fields = ("created_at", "actor", "action", "reason", "details")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None) -> bool:
        return False


@admin.register(Contribution)
class ContributionAdmin(admin.ModelAdmin):
    list_display = (
        "short_id",
        "problem_type",
        "status",
        "product_link",
        "code",
        "submitted_at",
        "warnings_count",
        "moderate_link",
    )
    list_filter = (
        "status",
        "problem_type",
        ("submitted_at", admin.DateFieldListFilter),
        ("created_at", admin.DateFieldListFilter),
    )
    search_fields = ("code", "product__name", "product__brand")
    date_hierarchy = "submitted_at"
    ordering = ("-submitted_at",)
    inlines = [ImageInline, AuditInline]
    readonly_fields = [f.name for f in Contribution._meta.fields if f.name != "access_token"]
    exclude = ("access_token",)
    list_select_related = ("product",)

    def get_queryset(self, request: HttpRequest):
        return super().get_queryset(request).exclude(status=Contribution.Status.DRAFT)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj=None) -> bool:
        return False  # changes only through the moderation view (audited)

    def has_moderate_permission(self, request: HttpRequest) -> bool:
        return request.user.has_perm("contributions.moderate_contribution")

    @admin.display(description="ID")
    def short_id(self, obj: Contribution) -> str:
        return str(obj.pk)[:8]

    @admin.display(description="Warnings")
    def warnings_count(self, obj: Contribution) -> int:
        return len(obj.validation_warnings or [])

    @admin.display(description="Product")
    def product_link(self, obj: Contribution) -> str:
        if not obj.product_id:
            return "new"
        return format_html(
            '<a href="{}">{}</a>',
            reverse("product", args=[obj.product_id]),
            obj.product.name or obj.product_id,
        )

    @admin.display(description="")
    def moderate_link(self, obj: Contribution) -> str:
        return format_html(
            '<a href="{}">Review</a>',
            reverse("admin:contributions_contribution_moderate", args=[obj.pk]),
        )

    def get_urls(self):
        return [
            path(
                "<uuid:pk>/moderate/",
                self.admin_site.admin_view(self.moderate_view),
                name="contributions_contribution_moderate",
            ),
            *super().get_urls(),
        ]

    def moderate_view(self, request: HttpRequest, pk) -> HttpResponse:
        if not self.has_view_permission(request):
            raise PermissionDenied
        c = get_object_or_404(
            Contribution.objects.select_related("product__current_revision", "base_revision"), pk=pk
        )
        can_moderate = self.has_moderate_permission(request)
        form = ModerationForm(request.POST or None)
        if request.method == "POST":
            if not can_moderate:
                raise PermissionDenied
            if form.is_valid():
                decision = form.cleaned_data["decision"]
                reason = form.cleaned_data["reason"]
                try:
                    if decision == "approve":
                        outcome = moderation.approve(c.pk, request.user, reason)
                        if outcome.status == "conflict":
                            messages.warning(
                                request,
                                "The product changed since this contribution was made, in: "
                                + ", ".join(outcome.conflicting_fields)
                                + ". Nothing was published. "
                                "Review the diff, then re-base or reject.",
                            )
                        else:
                            messages.success(request, "Approved and published.")
                    elif decision == "reject":
                        moderation.reject(c.pk, request.user, reason)
                        messages.success(request, "Rejected.")
                    else:
                        moderation.rebase(c.pk, request.user, reason)
                        messages.success(request, "Re-based onto the current revision.")
                except moderation.ModerationError as exc:
                    messages.error(request, str(exc))
                return redirect("admin:contributions_contribution_moderate", pk=c.pk)

        current = c.product.current_revision if c.product else None
        base = c.base_revision
        rows = []
        for name in DATA_FIELDS:
            proposed = c.proposed.get(name, None) if name in (c.proposed or {}) else None
            base_value = base.data.get(name) if base else None
            current_value = current.data.get(name) if current else None
            rows.append(
                {
                    "field": name,
                    "proposed": name in (c.proposed or {}),
                    "base": _pretty(base_value),
                    "current": _pretty(current_value),
                    "new": _pretty(proposed) if name in (c.proposed or {}) else "",
                    "changed_since_base": bool(base and current and base_value != current_value),
                }
            )
        runs = OCRRun.objects.filter(image__contribution=c).select_related("image")
        context = {
            **self.admin_site.each_context(request),
            "title": f"Review contribution {str(c.pk)[:8]}",
            "c": c,
            "rows": rows,
            "images": c.images.all(),
            "runs": runs,
            "form": form,
            "can_moderate": can_moderate,
            "base": base,
            "current": current,
            "opts": self.model._meta,
            "revisions": ProductRevision.objects.filter(product=c.product).only(
                "number", "source", "created_at"
            )[:10]
            if c.product
            else [],
        }
        return TemplateResponse(request, "admin/contributions/moderate.html", context)


@admin.register(OCRRun)
class OCRRunAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "image",
        "status",
        "engine_version",
        "lang",
        "attempts",
        "duration_ms",
        "created_at",
    )
    list_filter = ("status", ("created_at", admin.DateFieldListFilter))
    readonly_fields = [f.name for f in OCRRun._meta.fields]

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False


@admin.register(ModerationAudit)
class ModerationAuditAdmin(admin.ModelAdmin):
    list_display = ("created_at", "actor", "action", "contribution", "reason")
    list_filter = ("action", ("created_at", admin.DateFieldListFilter))
    readonly_fields = [f.name for f in ModerationAudit._meta.fields]

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False
