"""Django auth models re-registered with the admin theme (django-unfold)."""

from django.contrib import admin
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import Group, User
from unfold.admin import ModelAdmin
from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm

admin.site.unregister(User)
admin.site.unregister(Group)


@admin.register(User)
class UserAdmin(BaseUserAdmin, ModelAdmin):
    form = UserChangeForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm


@admin.register(Group)
class GroupAdmin(BaseGroupAdmin, ModelAdmin):
    pass


# ---------- Learn (CMS) ----------
from core.models import LearnArticle, LearnTopic  # noqa: E402


@admin.register(LearnTopic)
class LearnTopicAdmin(ModelAdmin):
    list_display = ["title", "key", "icon", "order"]
    list_editable = ["order"]
    prepopulated_fields = {"key": ["title"]}


@admin.register(LearnArticle)
class LearnArticleAdmin(ModelAdmin):
    list_display = ["title", "topic", "minutes", "featured", "published", "updated_at"]
    list_filter = ["topic", "featured", "published", "noindex"]
    list_editable = ["featured", "published"]
    search_fields = ["title", "summary", "body", "seo_title", "meta_description"]
    prepopulated_fields = {"slug": ["title"]}
    readonly_fields = ["created_at", "updated_at"]
    view_on_site = True
    fieldsets = [
        (
            "Content",
            {
                "fields": ["title", "slug", "topic", "summary", "body", "minutes"],
                "description": "Body format: blank line = new paragraph · ## Heading · "
                "- bullet · 1. numbered · **bold** · *italic* · "
                "[text](/path or https://…). HTML is not allowed.",
            },
        ),
        ("Image", {"fields": ["image", "image_static", "image_alt"]}),
        (
            "SEO",
            {
                "fields": ["seo_title", "meta_description", "canonical_url", "noindex"],
                "description": "Leave empty to use the title and summary. "
                "Keep the title under 70 and the description under 160 characters.",
            },
        ),
        ("Publishing", {"fields": ["published", "featured", "order", "created_at", "updated_at"]}),
    ]

    def get_view_on_site_url(self, obj=None):
        from django.urls import reverse

        return reverse("learn-article", args=[obj.slug]) if obj else None
