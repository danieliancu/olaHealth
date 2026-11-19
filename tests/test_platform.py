"""Rate limits, request ids, JSON logs and the Sentry scrubber."""

from __future__ import annotations

import json
import logging

import pytest
from django.urls import reverse

from core.observability import JsonFormatter, RequestIdFilter, scrub_event


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("action", "url"),
    [
        ("api_read", "/api/v1/products/"),
        ("suggest", "/api/v1/suggest/?q=yo"),
        ("compare", "/api/v1/compare/?ids="),
    ],
)
def test_api_endpoints_are_rate_limited(client, settings, action, url):
    settings.RATE_LIMITS = {**settings.RATE_LIMITS, action: (2, 60)}
    for _ in range(2):
        assert client.get(url).status_code != 429
    r = client.get(url)
    assert r.status_code == 429
    assert r["Retry-After"].isdigit()
    assert r.json()["error"] == "rate_limited"


@pytest.mark.django_db
def test_rate_limited_page_is_friendly_html(rf, settings):
    from django.contrib.sessions.backends.db import SessionStore
    from django.http import HttpResponse

    from core.coordination import rate_limited

    settings.RATE_LIMITS = {**settings.RATE_LIMITS, "search": (1, 60)}

    @rate_limited("search")
    def view(request):
        return HttpResponse("ok")

    request = rf.get("/products/")
    request.session = SessionStore()
    assert view(request).status_code == 200
    r = view(request)
    assert r.status_code == 429 and b"Too many requests" in r.content


@pytest.mark.django_db
def test_every_response_has_a_request_id(client):
    r = client.get(reverse("healthz"))
    assert len(r["X-Request-ID"]) == 32
    r = client.get(reverse("healthz"), HTTP_X_REQUEST_ID="proxy-id-12345678")
    assert r["X-Request-ID"] == "proxy-id-12345678"
    r = client.get(reverse("healthz"), HTTP_X_REQUEST_ID="<bad id>")
    assert r["X-Request-ID"] != "<bad id>"


def test_json_log_lines():
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "hello %s", ("world",), None)
    RequestIdFilter().filter(record)
    line = json.loads(JsonFormatter().format(record))
    assert line["msg"] == "hello world" and line["level"] == "INFO" and "request_id" in line


def test_sentry_events_carry_no_personal_data():
    event = {
        "request": {
            "url": "https://foodotrack.example/today/",
            "query_string": "date=2026-11-19",
            "cookies": {"sessionid": "secret"},
            "data": {"name": "Pizza"},
            "headers": {"Cookie": "sessionid=secret", "User-Agent": "x"},
        },
        "user": {"email": "member@example.com"},
    }
    out = scrub_event(event)
    assert "cookies" not in out["request"] and "data" not in out["request"]
    assert "query_string" not in out["request"] and "user" not in out
    assert out["request"]["headers"] == {"User-Agent": "x"}


@pytest.mark.django_db
def test_openapi_schema_documents_every_endpoint(client):
    r = client.get(reverse("api-schema"), HTTP_ACCEPT="application/json")
    assert r.status_code == 200
    schema = json.loads(r.content)
    paths = set(schema["paths"])
    for path in ("lookup/", "products/", "products/{id}/", "suggest/", "compare/"):
        assert f"/api/v1/{path}" in paths, path
    params = {p["name"] for p in schema["paths"]["/api/v1/lookup/"]["get"]["parameters"]}
    assert {"code", "format"} <= params


@pytest.mark.django_db
def test_api_docs_page_has_no_inline_script(client):
    import re

    html = client.get(reverse("api-docs")).content.decode()
    assert "swagger" in html.lower()
    for tag in re.findall(r"<script[^>]*>", html):
        assert "src=" in tag, tag
