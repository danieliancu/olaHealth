"""Ask Foodo: find products in our catalogue and, when needed, in Open Food Facts.

* A barcode in the question (8-14 digits) is looked up like a scan: our catalogue first, then
  Open Food Facts (the product is imported, so its page opens at once).
* "find / search / look up / look for / caută …" searches our catalogue by name or brand and,
  when nothing matches, Open Food Facts.

Only the typed words or the barcode are sent to Open Food Facts - never anything about the
user. Answers are built from facts only (rule-based), never by the AI provider.
"""

from __future__ import annotations

import re

from django.urls import reverse
from django.utils.http import urlencode

BARCODE = re.compile(r"(?<!\d)(\d{8,14})(?!\d)")
SEARCH = re.compile(
    r"^\s*(?:please\s+)?(?:find|search(?:\s+for)?|look\s+(?:up|for)|show\s+me|is\s+there|"
    r"do\s+you\s+have|cauta|caută|gaseste|găsește)\s+(?P<terms>.+?)\s*[?.!]*\s*$",
    re.IGNORECASE,
)
PROCESSING = {
    1: "unprocessed or minimally processed",
    2: "processed culinary ingredient",
    3: "processed food",
    4: "ultra-processed",
}
MAX_RESULTS = 3


def wants_product(text: str) -> bool:
    return bool(BARCODE.search(text or "") or SEARCH.match(text or ""))


def _describe(product) -> str:
    """One product as a few calm lines, with a link to its page."""
    name = product.name or "Unnamed product"
    brand = f" ({product.brand})" if product.brand else ""
    lines = [f"{name}{brand}"]
    details = []
    if getattr(product, "foodo_value", None) is not None:
        details.append(f"Foodo Score {product.foodo_value}/100")
    nova = getattr(product, "nova_group", None)
    if nova in PROCESSING:
        details.append(PROCESSING[nova])
    if details:
        lines.append(" · ".join(details))
    lines.append(f"Open it: {reverse('product', args=[product.pk])}")
    return "\n".join(lines)


def _by_barcode(code: str) -> str:
    from catalog.services.lookup import lookup

    result = lookup(code, schedule=False)
    if result.product is not None:
        return (
            "Here is what I found for that barcode:\n\n"
            + _describe(result.product)
            + "\n\nCheck it against your diet and allergies on the product page (“For you”)."
        )
    if result.status == "not_found":
        return "Open Food Facts doesn't know this barcode yet. You can add the product from Scan."
    return "I couldn't check this barcode right now. Please try again in a minute."


def _local(terms: str):
    from catalog.services.search import SearchParams, search_products

    return list(search_products(SearchParams(q=terms))[:MAX_RESULTS])


def _off(terms: str) -> tuple[list[dict], str]:
    from off.client import OFFError, default_client

    try:
        return default_client().search_text(terms, page_size=MAX_RESULTS), ""
    except OFFError as exc:
        if exc.kind == "rate_limited":
            return [], "Too many searches on Open Food Facts right now. Try again in a minute."
        return [], "Open Food Facts is not answering right now. Try again in a minute."


def _by_text(terms: str) -> str:
    terms = terms.strip()[:100]
    found = _local(terms)
    if found:
        blocks = "\n\n".join(_describe(p) for p in found)
        return f"In our catalogue:\n\n{blocks}"
    rows, problem = _off(terms)
    if problem:
        return f"Nothing in our catalogue for “{terms}”. {problem}"
    if not rows:
        return (
            f"I found nothing for “{terms}”, here or in Open Food Facts. Try a brand or a barcode."
        )
    lines = []
    for r in rows:
        name = r.get("product_name") or "Unnamed product"
        brand = f" ({r['brands']})" if r.get("brands") else ""
        url = f"{reverse('lookup')}?{urlencode({'code': r['code']})}"
        lines.append(f"{name}{brand}\nOpen it: {url}")
    return "Not in our catalogue yet; from Open Food Facts:\n\n" + "\n\n".join(lines)


def answer(text: str) -> str:
    """The reply to a product question (barcode or search), or "" when it isn't one."""
    match = BARCODE.search(text or "")
    if match:
        return _by_barcode(match.group(1))
    search = SEARCH.match(text or "")
    if search:
        return _by_text(search.group("terms"))
    return ""
