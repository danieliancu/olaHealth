"""Small coordination tables shared by web and worker processes (PostgreSQL only, no Redis)."""

from django.db import models


class RateCounter(models.Model):
    """Fixed-window counter, incremented atomically with INSERT ... ON CONFLICT."""

    key = models.CharField(max_length=200)
    window_start = models.BigIntegerField()
    count = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["key", "window_start"], name="uniq_rate_window")
        ]

    def __str__(self) -> str:
        return f"{self.key}@{self.window_start}"


class Lease(models.Model):
    """Expiring exclusive lease. Acquired with one autocommit statement, never held in a
    transaction while doing network I/O."""

    key = models.CharField(max_length=200, unique=True)
    owner = models.CharField(max_length=64)
    expires_at = models.DateTimeField()

    def __str__(self) -> str:
        return self.key
