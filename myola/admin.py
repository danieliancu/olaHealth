"""Superuser-only views of personal data (staff use /staff/, which never shows diaries)."""

from django.contrib import admin

from myola import models


@admin.register(models.Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "onboarded", "created_at")
    search_fields = ("user__email",)
    raw_id_fields = ("user",)

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser
