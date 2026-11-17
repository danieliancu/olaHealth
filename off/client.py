"""Minimal Open Food Facts read adaptor (API v2) on httpx.

Why not the official ``openfoodfacts`` SDK: it is built on ``requests`` (plus pydantic
and tqdm), exposes a single timeout, and does not surface Retry-After or a typed error
taxonomy. We need distinct connect/read timeouts, a shared cross-process rate gate,
explicit error kinds and a host allow-list, so a ~150-line adaptor is simpler.

Read only: this module never writes to Open Food Facts.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger(__name__)

ErrorKind = Literal[
    "invalid_code",
    "not_found",
    "timeout",
    "rate_limited",
    "upstream_unavailable",
    "invalid_response",
]

PRODUCT_FIELDS = (
    "code,product_name,product_name_en,generic_name,brands,quantity,product_quantity,"
    "product_quantity_unit,serving_size,categories_tags,countries_tags,lang,lc,"
    "ingredients_text,ingredients_lang,allergens_tags,traces_tags,additives_tags,"
    "nutriments,nutrition_data_per,nutrition_data_prepared_per,no_nutrition_data,"
    "nutriscore_grade,nutriscore_score,nutriscore_version,nutriscore,nova_group,"
    "nova_groups_tags,nutrient_levels,ingredients_analysis_tags,misc_tags,"
    "data_quality_warnings_tags,states_tags,image_front_url,image_ingredients_url,"
    "image_nutrition_url,last_modified_t"
)


class OFFError(Exception):
    def __init__(self, kind: ErrorKind, detail: str = "", retry_after: int | None = None):
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind
        self.detail = detail
        self.retry_after = retry_after


@dataclass
class OFFClient:
    base_url: str
    user_agent: str
    connect_timeout: float = 3.0
    read_timeout: float = 8.0
    max_retries: int = 2
    retry_after_cap: int = 5
    rate_gate: Callable[[], None] | None = None  # raises OFFError("rate_limited") if denied
    transport: httpx.BaseTransport | None = None
    sleep: Callable[[float], None] = time.sleep
    requests_made: int = field(default=0, init=False)

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self.base_url,
            headers={"User-Agent": self.user_agent, "Accept": "application/json"},
            timeout=httpx.Timeout(
                self.read_timeout, connect=self.connect_timeout, read=self.read_timeout
            ),
            follow_redirects=False,
            transport=self.transport,
        )

    def get_product(self, off_code: str) -> dict[str, Any]:
        """Return the OFF ``product`` object or raise :class:`OFFError`."""
        if not off_code.isdigit():
            raise OFFError("invalid_code", "non-numeric code")
        path = f"/api/v2/product/{off_code}.json"
        attempt = 0
        with self._client() as client:
            while True:
                attempt += 1
                try:
                    return self._attempt(client, path, off_code)
                except OFFError as exc:
                    delay = self._retry_delay(exc, attempt)
                    if delay is None:
                        raise
                    logger.info("OFF %s, retrying in %.1fs (attempt %d)", exc.kind, delay, attempt)
                    self.sleep(delay)

    def _retry_delay(self, exc: OFFError, attempt: int) -> float | None:
        if attempt > self.max_retries:
            return None
        if exc.kind in ("timeout", "upstream_unavailable"):
            if exc.retry_after is not None:
                return float(exc.retry_after) if exc.retry_after <= self.retry_after_cap else None
            return min(0.5 * 2 ** (attempt - 1), float(self.retry_after_cap))
        if exc.kind == "rate_limited" and exc.retry_after is not None:
            # Upstream asked us to wait: honour it only when the wait is short.
            return float(exc.retry_after) if exc.retry_after <= self.retry_after_cap else None
        return None

    def _attempt(self, client: httpx.Client, path: str, off_code: str) -> dict[str, Any]:
        if self.rate_gate is not None:
            self.rate_gate()
        self.requests_made += 1
        try:
            response = client.get(path, params={"fields": PRODUCT_FIELDS})
        except httpx.TimeoutException as exc:
            raise OFFError("timeout", type(exc).__name__) from exc
        except httpx.TransportError as exc:
            raise OFFError("upstream_unavailable", type(exc).__name__) from exc

        status = response.status_code
        retry_after = parse_retry_after(response.headers.get("Retry-After"))
        if status == 429:
            raise OFFError("rate_limited", "HTTP 429", retry_after)
        if status in (500, 502, 503, 504):
            raise OFFError("upstream_unavailable", f"HTTP {status}", retry_after)
        if 300 <= status < 400:
            location = response.headers.get("Location", "")
            raise OFFError(
                "invalid_response", f"unexpected redirect to {urlsplit(location).netloc}"
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise OFFError("invalid_response", f"HTTP {status}, body is not JSON") from exc
        if not isinstance(payload, dict):
            raise OFFError("invalid_response", "JSON root is not an object")

        # OFF answers "not found" with status 0 (HTTP 404 on current servers, 200 on older).
        if payload.get("status") == 0 and status in (200, 404):
            raise OFFError("not_found", str(payload.get("status_verbose", "")))
        if status != 200:
            raise OFFError("invalid_response", f"HTTP {status}")
        product = payload.get("product")
        if payload.get("status") != 1 or not isinstance(product, dict):
            raise OFFError("invalid_response", "missing product object")
        returned = str(product.get("code") or payload.get("code") or "")
        if returned and returned.lstrip("0") != off_code.lstrip("0"):
            raise OFFError("invalid_response", "product code does not match the request")
        return product


def parse_retry_after(value: str | None) -> int | None:
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return int(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max(0, int(when.timestamp() - time.time()))


def default_client() -> OFFClient:
    from django.conf import settings

    from core.coordination import hit

    def gate() -> None:
        decision = hit("off:product-read", settings.OFF_RATE_LIMIT_PER_MINUTE, 60)
        if not decision.allowed:
            raise OFFError("rate_limited", "local shared limit", decision.retry_after)

    return OFFClient(
        base_url=settings.OFF_BASE_URL,
        user_agent=settings.OFF_USER_AGENT,
        connect_timeout=settings.OFF_CONNECT_TIMEOUT,
        read_timeout=settings.OFF_READ_TIMEOUT,
        max_retries=settings.OFF_MAX_RETRIES,
        retry_after_cap=settings.OFF_RETRY_AFTER_CAP,
        rate_gate=gate,
    )
