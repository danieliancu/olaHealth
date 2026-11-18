"""UI helpers: Lucide icons from the local SVG sprite."""

from django import template
from django.templatetags.static import static
from django.utils.html import format_html

register = template.Library()

ICONS = {
    "scan-barcode",
    "flashlight",
    "zoom-in",
    "search",
    "git-compare-arrows",
    "bookmark",
    "camera",
    "chevron-left",
    "chevron-down",
    "x",
    "info",
    "plus",
    "check",
    "trash-2",
    "image-plus",
    "arrow-right",
    "refresh-cw",
    "triangle-alert",
    "droplet",
    "droplets",
    "candy",
    "cuboid",
    "wheat",
    "leaf",
    "egg",
    "flame",
    "flask-conical",
    "salad",
    "circle-check",
    "user-round",
    "book-open",
}


@register.simple_tag
def icon(name: str, size: int = 20, cls: str = "") -> str:
    """Decorative icon (aria-hidden). The surrounding control must carry the accessible name."""
    if name not in ICONS:
        raise template.TemplateSyntaxError(f"Unknown icon {name!r}")
    return format_html(
        '<svg class="icon {}" width="{}" height="{}" aria-hidden="true" focusable="false">'
        '<use href="{}#{}"></use></svg>',
        cls,
        size,
        size,
        static("vendor/lucide/sprite.svg"),
        name,
    )


@register.simple_tag
def asset(name: str) -> str:
    from core.assets import asset_url

    return asset_url(name)


@register.filter
def img(value: str) -> str:
    from core.assets import image_url

    return image_url(value or "")
