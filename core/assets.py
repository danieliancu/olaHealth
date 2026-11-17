"""Optional raster artwork with SVG fallbacks.

Drop ``<name>.webp`` (or ``.png``/``.jpg``) next to the shipped ``<name>.svg`` under
``static/`` and it is used automatically. Products may reference such artwork with the
marker ``static:<name>`` (used only by fictional demo products).
"""

from __future__ import annotations

from functools import lru_cache

from django.conf import settings
from django.contrib.staticfiles import finders
from django.templatetags.static import static

RASTER = (".webp", ".png", ".jpg")


def _resolve(name: str) -> str:
    for ext in RASTER:
        if finders.find(name + ext):
            return static(name + ext)
    return static(name + ".svg")


_cached = lru_cache(maxsize=64)(_resolve)


def asset_url(name: str) -> str:
    return _resolve(name) if settings.DEBUG else _cached(name)


def image_url(value: str) -> str:
    """Product image URL: remote/served URL as is, ``static:`` markers resolved."""
    if value.startswith("static:"):
        return asset_url(value.removeprefix("static:"))
    return value
