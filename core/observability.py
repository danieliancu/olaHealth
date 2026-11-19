"""Request ids, JSON logs and optional Sentry error reporting.

* Every request gets an id (``X-Request-ID`` from the proxy when present and sane, otherwise a
  new one). It is returned in the response header and added to every log record.
* ``LOG_FORMAT=json`` writes one JSON object per log line (for log collectors).
* Sentry is enabled only when ``SENTRY_DSN`` is set. No personal data is sent: no cookies,
  no user identity, no request bodies, and personal paths are reduced to their route.
"""

from __future__ import annotations

import contextvars
import json
import logging
import re
import uuid

_request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
PERSONAL_PREFIXES = ("/today", "/progress", "/my-ola", "/start", "/accounts", "/staff", "/saved")


class RequestIdMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        incoming = request.headers.get("X-Request-ID", "")
        rid = incoming if _SAFE_ID.match(incoming) else uuid.uuid4().hex
        token = _request_id.set(rid)
        request.request_id = rid
        try:
            response = self.get_response(request)
        finally:
            _request_id.reset(token)
        response["X-Request-ID"] = rid
        return response


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def scrub_event(event: dict, hint: dict | None = None) -> dict | None:
    """Sentry ``before_send``: drop cookies, bodies, user data and personal query strings."""
    request = event.get("request") or {}
    request.pop("cookies", None)
    request.pop("data", None)
    headers = request.get("headers") or {}
    for name in list(headers):
        if name.lower() in {"cookie", "authorization", "x-csrftoken"}:
            headers.pop(name)
    url = request.get("url") or ""
    if any(p in url for p in PERSONAL_PREFIXES):
        request.pop("query_string", None)
    event.pop("user", None)
    return event


def init_sentry(dsn: str, environment: str, traces_sample_rate: float, release: str = "") -> None:
    import sentry_sdk
    from sentry_sdk.integrations.django import DjangoIntegration

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        release=release or None,
        integrations=[DjangoIntegration()],
        send_default_pii=False,
        traces_sample_rate=traces_sample_rate,
        before_send=scrub_event,
    )
