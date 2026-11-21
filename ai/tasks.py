"""Scheduled jobs for Ask Foodo's AI (see ``setup_schedules``)."""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from ai.models import AIAnswer


def purge_old_ai_answers() -> int:
    """Delete AI answer logs older than ``AIAnswer.RETENTION_DAYS``."""
    cutoff = timezone.now() - timedelta(days=AIAnswer.RETENTION_DAYS)
    return AIAnswer.objects.filter(created_at__lt=cutoff).delete()[0]
