"""Site-wide settings changed from the staff panel, and the staff audit log."""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class SiteSettings(models.Model):
    """A single row (pk=1). Read with ``SiteSettings.load()``."""

    ai_enabled = models.BooleanField(default=False)
    # Choices come from ai.providers.catalog (each provider with its own key and models).
    ai_provider = models.CharField(max_length=20, default="anthropic")
    ai_model = models.CharField(max_length=80, default="claude-haiku-5-5")
    ai_daily_messages_per_user = models.PositiveIntegerField(
        default=30, validators=[MinValueValidator(0), MaxValueValidator(1000)]
    )
    # Costs are shown and set in pounds. Providers bill in US dollars: usage is counted in
    # dollars (ai.AIUsage.cost_usd) and converted with the rate below.
    ai_monthly_budget_gbp = models.DecimalField(
        max_digits=8, decimal_places=2, default=Decimal("15.00")
    )
    usd_to_gbp = models.DecimalField(
        max_digits=6, decimal_places=4, default=Decimal("0.7500"),
        validators=[MinValueValidator(Decimal("0.0001"))],
    )  # fmt: skip
    # Richer answers: the product's ingredient list and additives also go to the AI provider
    # (allergen and diet checks stay rule-based either way).
    ai_send_ingredients = models.BooleanField(default=True)
    # General food and nutrition questions in the general Ask Foodo chat (not on product pages),
    # answered by the AI from general knowledge, with strict rules for health questions.
    ai_general_questions = models.BooleanField(default=True)
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

    def to_gbp(self, usd: Decimal) -> Decimal:
        """A dollar amount (provider bills) in pounds, at the rate set in Settings."""
        return (Decimal(usd or 0) * self.usd_to_gbp).quantize(Decimal("0.000001"))

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


class Announcement(models.Model):
    """A message from the team, delivered as an in-app notification to every opted-in user."""

    title = models.CharField(max_length=160)
    body = models.CharField(max_length=500, blank=True)
    url = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.title
