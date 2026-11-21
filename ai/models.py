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


class AIAnswer(models.Model):
    """One call to the AI provider for Ask Foodo: the question, what came back and what the
    user thought of it. Kept for quality review in the staff panel (shown without the user's
    identity) and deleted after ``RETENTION_DAYS``."""

    RETENTION_DAYS = 30

    class Kind(models.TextChoices):
        PRODUCT = "product", "Product chat"
        GENERAL = "general", "General chat"

    class Outcome(models.TextChoices):
        ANSWERED = "answered", "Answered by the AI"
        BLOCKED = "blocked", "Blocked by the safety check"
        ROUTED = "routed", "Handed to the rules"
        FAILED = "failed", "Provider failed"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="ai_answers"
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    kind = models.CharField(max_length=10, choices=Kind.choices)
    context = models.CharField(max_length=60, blank=True)
    question = models.TextField(max_length=2000)
    answer = models.TextField(blank=True)
    outcome = models.CharField(max_length=10, choices=Outcome.choices)
    reason = models.CharField(max_length=80, blank=True)  # guard rule, routed intent or error
    model = models.CharField(max_length=80, blank=True)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    cost_usd = models.DecimalField(max_digits=10, decimal_places=6, default=Decimal(0))
    rating = models.SmallIntegerField(null=True, blank=True)  # 1 helpful, -1 not helpful

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "context", "created_at"], name="aianswer_ctx")]

    def __str__(self) -> str:
        return f"{self.kind} {self.outcome}: {self.question[:60]}"
