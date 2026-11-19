"""Cross-process rate limiting and leases backed by PostgreSQL.

Both use single autocommit statements so no row lock outlives the statement.
"""

from __future__ import annotations

import functools
import secrets
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from django.conf import settings
from django.db import connection
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render

from .models import Lease, RateCounter


@dataclass(frozen=True)
class RateDecision:
    allowed: bool
    count: int
    limit: int
    retry_after: int


def hit(key: str, limit: int, window_seconds: int, now: float | None = None) -> RateDecision:
    """Count one event for ``key`` in the current fixed window."""
    now = time.time() if now is None else now
    window_start = int(now // window_seconds) * window_seconds
    table = RateCounter._meta.db_table
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            INSERT INTO {table} (key, window_start, count) VALUES (%s, %s, 1)
            ON CONFLICT (key, window_start) DO UPDATE SET count = {table}.count + 1
            RETURNING count
            """,  # noqa: S608 - table name is a constant from model meta
            [key[:200], window_start],
        )
        count = cursor.fetchone()[0]
    retry_after = max(1, int(window_start + window_seconds - now))
    return RateDecision(count <= limit, count, limit, retry_after)


def purge_rate_counters(older_than_seconds: int = 2 * 3600) -> int:
    cutoff = int(time.time()) - older_than_seconds
    deleted, _ = RateCounter.objects.filter(window_start__lt=cutoff).delete()
    return deleted


def client_ip(request: HttpRequest) -> str:
    if settings.TRUST_X_FORWARDED_FOR:
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "unknown")


def check_request_limit(request: HttpRequest, action: str) -> RateDecision:
    """Apply the configured abuse limit for ``action`` per session and per IP."""
    limit, window = settings.RATE_LIMITS[action]
    if not request.session.session_key:
        request.session.save()
    by_ip = hit(f"{action}:ip:{client_ip(request)}", limit * 3, window)
    by_session = hit(f"{action}:s:{request.session.session_key}", limit, window)
    worst = by_session if not by_session.allowed else by_ip
    return worst if not worst.allowed else by_session


def rate_limited(action: str):
    """Decorator: apply ``RATE_LIMITS[action]`` to a view (Django or DRF function/method).

    Over the limit the view is not called: JSON clients and ``/api/`` paths get a JSON 429,
    pages get the friendly 429 page; both carry ``Retry-After``.
    """

    def decorator(view):
        @functools.wraps(view)
        def wrapper(*args, **kwargs):
            request = next(a for a in args if hasattr(a, "META") or hasattr(a, "_request"))
            raw = getattr(request, "_request", request)
            decision = check_request_limit(raw, action)
            if decision.allowed:
                return view(*args, **kwargs)
            return too_many_requests(raw, decision)

        return wrapper

    return decorator


def too_many_requests(request: HttpRequest, decision: RateDecision) -> HttpResponse:
    message = "Too many requests. Please wait a moment and try again."
    if request.path.startswith("/api/") or "application/json" in request.headers.get("Accept", ""):
        response: HttpResponse = JsonResponse(
            {"status": "error", "error": "rate_limited", "message": message}, status=429
        )
    else:
        context = {"retry_after": decision.retry_after, "message": message}
        response = render(request, "429.html", context, status=429)
    response["Retry-After"] = str(decision.retry_after)
    return response


def acquire_lease(key: str, seconds: int) -> str | None:
    """Return an owner token if the lease was acquired, otherwise ``None``."""
    owner = secrets.token_hex(16)
    table = Lease._meta.db_table
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            INSERT INTO {table} (key, owner, expires_at)
            VALUES (%s, %s, now() + make_interval(secs => %s))
            ON CONFLICT (key) DO UPDATE
                SET owner = EXCLUDED.owner, expires_at = EXCLUDED.expires_at
                WHERE {table}.expires_at < now()
            RETURNING owner
            """,  # noqa: S608 - table name is a constant from model meta
            [key[:200], owner, seconds],
        )
        row = cursor.fetchone()
    return owner if row and row[0] == owner else None


def release_lease(key: str, owner: str) -> None:
    Lease.objects.filter(key=key, owner=owner).delete()


@contextmanager
def lease(key: str, seconds: int) -> Iterator[bool]:
    owner = acquire_lease(key, seconds)
    try:
        yield owner is not None
    finally:
        if owner:
            release_lease(key, owner)


def purge_expired_leases() -> int:
    from django.utils import timezone

    deleted, _ = Lease.objects.filter(expires_at__lt=timezone.now()).delete()
    return deleted
