from decimal import Decimal

from django.conf import settings
from django.db import models


class AIUsage(models.Model):
    """Ask Foodo AI usage per user per day (messages, tokens and cost)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="ai_usage"
    )
    day = models.DateField()
    messages = models.PositiveIntegerField(default=0)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    cost_usd = models.DecimalField(max_digits=10, decimal_places=6, default=Decimal(0))

    class Meta:
        verbose_name = "AI usage"
        verbose_name_plural = "AI usage"
        ordering = ["-day"]
        constraints = [models.UniqueConstraint(fields=["user", "day"], name="uniq_ai_usage_day")]

    def __str__(self) -> str:
        return f"{self.user_id} {self.day}: {self.messages}"
