"""Site-wide settings changed from the staff panel, and the staff audit log."""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class SiteSettings(models.Model):
    """A single row (pk=1). Read with ``SiteSettings.load()``."""

    ai_enabled = models.BooleanField(default=False)
    ai_provider = models.CharField(
        max_length=20,
        choices=[("anthropic", "Anthropic (Claude)"), ("openai", "OpenAI")],
        default="anthropic",
    )
    ai_model = models.CharField(max_length=80, default="claude-haiku-5-5")
    ai_daily_messages_per_user = models.PositiveIntegerField(
        default=30, validators=[MinValueValidator(0), MaxValueValidator(1000)]
    )
    ai_monthly_budget_usd = models.DecimalField(
        max_digits=8, decimal_places=2, default=Decimal("20.00")
    )
    maintenance_message = models.CharField(max_length=300, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        verbose_name = "site settings"
        verbose_name_plural = "site settings"

    def __str__(self) -> str:
        return "Site settings"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)
        from core.context_processors import _MAINTENANCE

        _MAINTENANCE["at"] = 0.0  # show a changed banner at once in this process

    @classmethod
    def load(cls) -> SiteSettings:
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class StaffAction(models.Model):
    """Audit log of staff actions outside moderation (moderation uses ModerationAudit)."""

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    action = models.CharField(max_length=60)
    target = models.CharField(max_length=200, blank=True)
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["action", "created_at"], name="staffaction_action")]
        # Superusers have every permission. Staff can be granted these individually or via
        # a group (the default "view_staffaction" permission opens the audit log page).
        permissions = [
            ("manage_users", "Can manage user accounts in the staff panel"),
            ("manage_settings", "Can change site settings in the staff panel"),
        ]

    def __str__(self) -> str:
        return f"{self.action} {self.target}".strip()
