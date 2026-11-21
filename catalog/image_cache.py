"""Local cache of Open Food Facts product photos.

The OFF image host can be slow (several seconds per photo), so each referenced photo is
downloaded once, validated and re-encoded with Pillow, stored privately and served from our
own domain. Only https URLs on ``OFF_IMAGE_HOSTS`` are fetched (they come from the import,
never from user input); redirects are not followed; size and pixel count are bounded.
Attribution and licence stay on the ``ProductImage`` row and are shown with the photo.
"""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

import httpx
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from catalog.models import ProductImage
from core.coordination import acquire_lease, release_lease

logger = logging.getLogger(__name__)

MAX_DOWNLOAD_BYTES = 5 * 1024 * 1024
MAX_SIDE = 800


def cache_name(image: ProductImage) -> str:
    return f"offcache/{image.pk}.webp"


def allowed(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme == "https" and parts.hostname in settings.OFF_IMAGE_HOSTS


_client: httpx.Client | None = None


def _http() -> httpx.Client:
    """One keep-alive client per worker process.

    The OFF image host spends ~5 s on each TLS handshake; reusing the connection means
    only the first photo pays it, later photos take well under a second.
    """
    global _client
    if _client is None:
        _client = httpx.Client(
            # Runs in the worker only; the OFF image host can be very slow to accept.
            timeout=httpx.Timeout(30.0, connect=20.0),
            follow_redirects=False,
            headers={"User-Agent": settings.OFF_USER_AGENT},
            limits=httpx.Limits(max_keepalive_connections=4, keepalive_expiry=300),
        )
    return _client


def _download(url: str) -> bytes | None:
    with _http().stream("GET", url) as response:
        if response.status_code != 200:
            return None
        if not response.headers.get("content-type", "").startswith("image/"):
            return None
        data = bytearray()
        for chunk in response.iter_bytes():
            data.extend(chunk)
            if len(data) > MAX_DOWNLOAD_BYTES:
                return None
        return bytes(data)


def _reencode(data: bytes) -> bytes | None:
    """Validated, metadata-free WebP no wider than product photos are ever shown."""
    from core.images import PRODUCT_MAX_WIDTH, ImageRejected, decode, web_webp

    try:
        rgb, _fmt = decode(data)
    except ImageRejected:
        return None
    return web_webp(rgb, PRODUCT_MAX_WIDTH)


def cached(image: ProductImage) -> str | None:
    name = cache_name(image)
    return name if default_storage.exists(name) else None


def queue_cache(image: ProductImage) -> bool:
    """Ask the worker to download the photo (at most once per 10 minutes per image)."""
    owner = acquire_lease(f"off-image-queued:{image.pk}", 600)
    if owner is None:
        return False
    from django_q.tasks import async_task

    async_task("catalog.tasks.cache_off_image", str(image.pk), group="off-images")
    return True


def ensure_cached(image: ProductImage) -> str | None:
    """Return the storage name of the cached copy, downloading it if needed (worker)."""
    name = cache_name(image)
    if default_storage.exists(name):
        return name
    if image.source != ProductImage.Source.OFF or not allowed(image.remote_url):
        return None
    key = f"off-image:{image.pk}"
    owner = acquire_lease(key, 60)
    if owner is None:
        return None  # another process is downloading it
    try:
        if default_storage.exists(name):
            return name
        try:
            data = _download(image.remote_url)
        except httpx.HTTPError as exc:
            logger.info("OFF image download failed for %s: %s", image.pk, type(exc).__name__)
            return None
        content = _reencode(data) if data else None
        if content is None:
            return None
        return default_storage.save(name, ContentFile(content))
    finally:
        release_lease(key, owner)


def cache_product_images(product_id) -> int:
    count = 0
    for image in ProductImage.objects.filter(
        product_id=product_id, source=ProductImage.Source.OFF, is_active=True
    ):
        if ensure_cached(image):
            count += 1
    return count
