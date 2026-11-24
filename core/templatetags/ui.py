"""UI helpers: Lucide icons from the local SVG sprite."""

import re

from django import template
from django.templatetags.static import static
from django.utils.html import format_html

register = template.Library()

ICONS = {
    "menu",
    "calculator",
    "thumbs-up",
    "thumbs-down",
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
    # My Ola, preferences and lists
    "heart",
    "shopping-basket",
    "ban",
    "notebook-pen",
    "message-circle",
    "history",
    "sparkles",
    "vegan",
    "wheat-off",
    "milk-off",
    "ham",
    "shield-alert",
    "send",
    "pencil",
    # Onboarding, Today and Progress
    "fish",
    "nut",
    "shell",
    "scale",
    "dumbbell",
    "biceps-flexed",
    "apple",
    "circle-off",
    "hand-heart",
    "ellipsis",
    "calendar-days",
    "glass-water",
    "activity",
    "chevron-right",
    "footprints",
    "bike",
    "waves",
    "person-standing",
    "undo-2",
    "chart-line",
    "minus",
    "settings",
    "coffee",
    "sun",
    "moon",
    "cookie",
    "heart-pulse",
    "sandwich",
    "soup",
    "utensils",
    "pause",
    "play",
    # Accounts
    "log-in",
    "log-out",
    "mail",
    "key-round",
    "lock",
    # Dashboard, Learn and achievements
    "award",
    "clock",
    "house",
    "bell",
    "megaphone",
    "newspaper",
    "heart-fill",
}


SUGGESTION_ICONS = {
    "remember": "sparkles",
    "avoid": "ban",
    "scans": "scan-barcode",
    "prefs": "heart",
    "values": "chart-line",
    "indicators": "info",
    "restrictions": "shield-alert",
}


@register.filter
def suggestion_icon(key: str) -> str:
    """Icon for an Ask Foodo suggestion chip (falls back to a neutral one)."""
    return SUGGESTION_ICONS.get(key, "message-circle")


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
def processing(group) -> str:
    """Plain words for a processing group (no group number shown)."""
    from catalog.presenters import NOVA_SHORT

    try:
        return NOVA_SHORT.get(int(group), "")
    except (TypeError, ValueError):
        return ""


@register.filter
def img(value: str) -> str:
    from core.assets import image_url

    return image_url(value or "")


_CHAT_LINK = re.compile(r"(/p/[0-9a-f-]{36}/|/lookup/\?code=\d{8,14})")


@register.filter
def chat_links(text: str):
    """Escape a chat message and turn the app's own product links into real links."""
    from django.utils.html import escape
    from django.utils.safestring import mark_safe

    return mark_safe(  # noqa: S308 - the text is escaped first; only matched paths become links
        _CHAT_LINK.sub(lambda m: f'<a href="{m.group(1)}">{m.group(1)}</a>', escape(text or ""))
    )


@register.filter
def source_groups(message) -> list[tuple[str, str, list[dict]]]:
    """The sources under an Ask Foodo answer as card groups: ``(title, kind, cards)`` for
    "Products" then "From the Magazine", titled in the answer's language, each result once,
    in the order the answer gave them; a group only when it has results."""
    from myola.services import chat_text

    lang = (getattr(message, "details", None) or {}).get("lang") or "en"
    seen: set[str] = set()
    groups: dict[str, list[dict]] = {"products": [], "magazine": []}
    for source in getattr(message, "sources", None) or []:
        url = source.get("url")
        if not url or url in seen:
            continue
        seen.add(url)
        groups["magazine" if source.get("kind") == "article" else "products"].append(dict(source))
    _current_images(groups)
    return [(chat_text.group_title(k, lang), k, cards) for k, cards in groups.items() if cards]


def _current_images(groups: dict[str, list[dict]]) -> None:
    """The photo of each card as it is now: the product's front photo, the article's cover
    (also for answers saved before cards had photos). Unchanged when not found."""
    import uuid

    from catalog.models import Product
    from core.models import LearnArticle

    ids = {}
    for card in groups["products"]:
        m = re.match(r"^/p/([0-9a-f-]{36})/$", card.get("url") or "")
        if m:
            ids[card["url"]] = uuid.UUID(m.group(1))
    photos = dict(Product.objects.filter(pk__in=ids.values()).values_list("pk", "front_image_url"))
    for card in groups["products"]:
        photo = photos.get(ids.get(card.get("url")))
        if photo:
            card["image"] = photo
    slugs = {}
    for card in groups["magazine"]:
        m = re.match(r"^/learn/([\w-]+)/$", card.get("url") or "")
        if m:
            slugs[card["url"]] = m.group(1)
    articles = {a.slug: a for a in LearnArticle.objects.filter(slug__in=slugs.values())}
    for card in groups["magazine"]:
        article = articles.get(slugs.get(card.get("url")))
        if article is not None and article.image_url():
            card["image"] = article.image_url()


@register.filter
def gbp(usd) -> str:
    """A US-dollar cost (what AI providers bill) as pounds with 3 decimals: "£0.012"."""
    from decimal import Decimal, InvalidOperation

    from staff.models import SiteSettings

    try:
        amount = SiteSettings.load().to_gbp(Decimal(usd or 0))
    except (InvalidOperation, TypeError, ValueError):
        return "£0.000"
    return f"£{amount.quantize(Decimal('0.001')):,}"


@register.filter
def pounds(value) -> str:
    """A set amount already in pounds (e.g. the budget), with 2 decimals: "£15.00"."""
    from decimal import Decimal, InvalidOperation

    try:
        return f"£{Decimal(value or 0).quantize(Decimal('0.01')):,}"
    except (InvalidOperation, TypeError, ValueError):
        return "£0.00"


# Long single words that may not fit a narrow column: a soft hyphen where they may split.
_SOFT_BREAKS = {"Carbohydrate": "Carbo­hydrate", "Carbohydrates": "Carbo­hydrates"}


@register.filter
def breakable(text) -> str:
    """``text`` with a soft hyphen in long nutrient words (Carbo-hydrate) so they wrap."""
    out = str(text or "")
    for word, soft in _SOFT_BREAKS.items():
        out = re.sub(rf"\b{word}\b", soft, out)
    return out


@register.filter
def two_lines(text):
    """A title of more than one word on two lines, split where the two lines are most even
    ("Reading the label" -> "Reading<br>the label"); one word stays as it is. Escaped."""
    from django.utils.html import escape

    words = str(text or "").split()
    if len(words) < 2:
        return escape(" ".join(words))
    total = len(" ".join(words))
    best = min(
        range(1, len(words)),
        key=lambda i: (abs(len(" ".join(words[:i])) * 2 - total), i),
    )
    return format_html("{}<br>{}", " ".join(words[:best]), " ".join(words[best:]))


@register.filter
def notif_parts(body) -> tuple[str, str]:
    """A notification body split into what every screen shows and the detail shown only on
    wider screens: '“Title”: a 4-minute read in Topic.' -> ('“Title”', ': a 4-minute read…')."""
    text = str(body or "")
    if text.startswith("“") and "”: " in text:
        lead, _sep, rest = text.partition("”: ")
        return f"{lead}”", f": {rest}"
    return text, ""
