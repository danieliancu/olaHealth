"""Better alternatives for a product (V2, docs/mvp-api-contracts.md §3).

* Reference category: the product's most specific category, then its parent.
* Candidates: published local products (a current revision exists, so contributions still
  under review are never used) and an Open Food Facts search in that category for the UK,
  cached per (category, country) for 24 h. OFF results are normalised with
  ``off.mapping.map_product`` into the same shape as ``revision.data``.
* Python decides: strict restrictions and the Avoid list exclude candidates (never sent to
  OFF), the Foodo Score ranks them, and only a strictly better score is kept.
* On any OFF error the local candidates are still used.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlencode

from django.conf import settings
from django.core.cache import cache
from django.urls import reverse

from assessments.score import foodo_score, indicators_from_snapshots
from catalog.models import Category, Product
from catalog.nutrition import BASIS_LABELS
from catalog.presenters import format_value
from myola.facts import ProductFacts
from off.client import OFFClient, OFFError, default_client
from off.codes import off_normalize
from off.mapping import image_urls, map_product

logger = logging.getLogger(__name__)

UK = "en:united-kingdom"
CACHE_SECONDS = 24 * 3600
LOCAL_LIMIT = 50
SEARCH_PAGE_SIZE = 24
GRADES = "abcde"
# (nutrient key, words used in the comparison sentence); lower is better for each.
COMPARED_NUTRIENTS = (
    ("sugars", "sugar"),
    ("salt", "salt"),
    ("saturated-fat", "saturated fat"),
)


def reference_categories(product: Product) -> list[str]:
    """Most specific category first, then its parent (when known)."""
    tags = list((product.data or {}).get("categories") or product.categories or [])
    if not tags:
        return []
    specific = tags[-1]  # OFF orders category tags from general to specific
    parents = Category.objects.filter(tag=specific).values_list("parents", flat=True).first()
    parent = None
    if parents:
        # Prefer a parent the product itself is filed under.
        parent = next((p for p in parents if p in tags), parents[0])
    elif len(tags) > 1:
        parent = tags[-2]
    return [specific] + ([parent] if parent and parent != specific else [])


def search_off_cached(
    category: str, country: str = UK, *, client: OFFClient | None = None
) -> list[dict[str, Any]]:
    """Raw OFF search results for (category, country), cached 24 h. ``[]`` on OFF errors."""
    from catalog.services.lookup import off_circuit_open

    key = f"alternatives:off-search:v1:{country}:{category}"[:250]
    cached = cache.get(key)
    if cached is not None:
        return cached
    if off_circuit_open():
        return []
    try:
        products = (client or default_client()).search(
            category, country, page_size=SEARCH_PAGE_SIZE
        )
    except OFFError as exc:
        logger.info("OFF search failed for %s/%s: %s", category, country, exc.kind)
        return []
    cache.set(key, products, CACHE_SECONDS)
    return products


def _local_candidate(p: Product, category: str) -> dict[str, Any]:
    from myola.page import facts_from_product

    snapshots = list(p.current_revision.assessments.all())
    facts = facts_from_product(p, snapshots)
    indicators = indicators_from_snapshots(snapshots)
    return {
        "source": "local",
        "product_id": str(p.pk),
        "off_code": p.off_code or None,
        "facts": facts,
        "indicators": indicators,
        "score": foodo_score(indicators),
        "category": category,
    }


def _off_candidate(raw: dict[str, Any], category: str) -> dict[str, Any] | None:
    code = str(raw.get("code") or "").strip()
    if not code.isdigit():
        return None
    code = off_normalize(code)
    data = map_product(raw)
    image = image_urls(raw, settings.OFF_IMAGE_HOSTS).get("front", "")
    facts = ProductFacts(
        id=code,
        name=data.get("name") or "",
        data=data,
        labels=[t for t in raw.get("labels_tags") or [] if isinstance(t, str)],
        analysis=[t for t in raw.get("ingredients_analysis_tags") or [] if isinstance(t, str)],
        brand=data.get("brand") or "",
        quantity=data.get("quantity") or "",
        image=image,
        # via=alternative: opening a suggestion is a visit, not a scan (no "scanned" badge).
        url=f"{reverse('lookup')}?{urlencode({'code': code, 'via': 'alternative'})}",
    )
    return {
        "source": "off",
        "product_id": None,
        "off_code": code,
        "facts": facts,
        "indicators": raw,
        "score": foodo_score(raw),
        "category": category,
    }


def _grade(indicators: dict[str, Any]) -> str:
    grade = str(indicators.get("nutriscore_grade") or "").lower()
    return grade if grade in GRADES else ""


def _nova(indicators: dict[str, Any]) -> int | None:
    group = indicators.get("nova_group")
    if isinstance(group, str) and group.isdigit():
        group = int(group)
    return group if isinstance(group, int) and 1 <= group <= 4 else None


def compare(
    ref_facts: ProductFacts,
    ref_ind: dict[str, Any],
    cand_facts: ProductFacts,
    cand_ind: dict[str, Any],
) -> tuple[list[str], list[str]]:
    """Verifiable advantages and trade-offs of the candidate against the reference."""
    advantages: list[str] = []
    trade_offs: list[str] = []
    rg, cg = _grade(ref_ind), _grade(cand_ind)
    if rg and cg and rg != cg:
        text = f"Nutri-Score {cg.upper()} vs {rg.upper()}"
        (advantages if cg < rg else trade_offs).append(text)
    rn, cn = _nova(ref_ind), _nova(cand_ind)
    if rn and cn and rn != cn:
        text = f"NOVA group {cn} vs {rn}"
        (advantages if cn < rn else trade_offs).append(text)
    ref_basis, ref_values = ref_facts.per_100()
    cand_basis, cand_values = cand_facts.per_100()
    if ref_basis and ref_basis == cand_basis:
        per = BASIS_LABELS.get(ref_basis, "")
        for key, words in COMPARED_NUTRIENTS:
            r, c = ref_values.get(key), cand_values.get(key)
            if r is None or c is None or r.unit != c.unit or r.value == c.value:
                continue
            values = f"{format_value(c.value, c.unit)} vs {format_value(r.value, r.unit)}"
            if c.value < r.value:
                advantages.append(f"less {words} {per}: {values}")
            else:
                trade_offs.append(f"more {words} {per}: {values}")
    return advantages, trade_offs


def _blocked(facts: ProductFacts, prefs: dict[str, list[str]]) -> bool:
    from myola.restrictions import all_statuses, blocking

    if not prefs["diets"] and not prefs["allergens"]:
        return False
    return bool(blocking(all_statuses(facts), prefs["diets"], prefs["allergens"]))


def _user_filters(user) -> tuple[dict[str, list[str]], set[str], set[str]]:
    """(strict preferences, avoided product ids, avoided OFF codes). Stays on the server."""
    empty = {"goals": [], "diets": [], "allergens": []}
    if user is None or not getattr(user, "is_authenticated", False):
        return empty, set(), set()
    from myola.models import ProductMark, Profile
    from myola.services.profiles import derive_prefs

    prefs = derive_prefs(Profile.objects.filter(user=user).first())
    marks = ProductMark.objects.filter(user=user, kind=ProductMark.Kind.AVOID).values_list(
        "product_id", "product__off_code"
    )
    ids = {str(pid) for pid, _ in marks}
    codes = {code for _, code in marks if code}
    return prefs, ids, codes


def alternatives(
    product: Product,
    user=None,
    limit: int = 3,
    *,
    country: str = UK,
    client: OFFClient | None = None,
) -> list[dict[str, Any]]:
    """Up to ``limit`` products with a strictly better Foodo Score, best first.

    Each item: ``source`` ("local" or "off"), ``product_id`` (local) / ``off_code``,
    ``name``, ``brand``, ``quantity``, ``image``, ``url``, ``score`` (int), ``score_detail``,
    ``reference_score``, ``category``, ``advantage`` (main one), ``advantages`` and
    ``trade_offs`` (lists of short sentences).
    """
    from myola.page import facts_from_product

    if product.current_revision is None or limit <= 0:
        return []
    ref_snapshots = list(product.current_revision.assessments.all())
    ref_ind = indicators_from_snapshots(ref_snapshots)
    ref_score = foodo_score(ref_ind)
    if ref_score["status"] != "ok":
        return []  # nothing can be shown as better than an unknown score
    ref_facts = facts_from_product(product, ref_snapshots)
    prefs, avoid_ids, avoid_codes = _user_filters(user)
    own_code = off_normalize(product.off_code) if product.off_code else None

    chosen: dict[str, dict[str, Any]] = {}
    seen: set[str] = {str(product.pk)} | ({own_code} if own_code else set())
    for category in reference_categories(product):
        candidates: list[dict[str, Any]] = []
        local = (
            Product.objects.filter(
                categories__contains=[category],
                countries__contains=[country],
                current_revision__isnull=False,
            )
            .exclude(pk=product.pk)
            .select_related("current_revision")
            .prefetch_related("current_revision__assessments")
            .order_by("-completeness", "name")[:LOCAL_LIMIT]
        )
        for p in local:
            if str(p.pk) in seen:
                continue
            seen.add(str(p.pk))
            if p.off_code:
                seen.add(off_normalize(p.off_code))
            if str(p.pk) in avoid_ids:
                continue
            candidates.append(_local_candidate(p, category))
        for raw in search_off_cached(category, country, client=client):
            cand = _off_candidate(raw, category)
            if cand is None or cand["off_code"] in seen:
                continue
            seen.add(cand["off_code"])
            if cand["off_code"] in avoid_codes:
                continue
            candidates.append(cand)

        for cand in candidates:
            score = cand["score"]
            if score["status"] != "ok" or score["score"] <= ref_score["score"]:
                continue
            if _blocked(cand["facts"], prefs):
                continue
            key = cand["product_id"] or cand["off_code"]
            chosen.setdefault(key, cand)
        if len(chosen) >= limit:
            break

    ranked = sorted(
        chosen.values(),
        key=lambda c: (-c["score"]["score"], c["facts"].name.lower(), c["off_code"] or ""),
    )[:limit]
    results = []
    for cand in ranked:
        facts: ProductFacts = cand["facts"]
        advantages, trade_offs = compare(ref_facts, ref_ind, facts, cand["indicators"])
        if not advantages:
            advantages = [f"Foodo Score {cand['score']['score']} vs {ref_score['score']}"]
        results.append(
            {
                "source": cand["source"],
                "product_id": cand["product_id"],
                "off_code": cand["off_code"],
                "name": facts.name,
                "brand": facts.brand,
                "quantity": facts.quantity,
                "image": facts.image,
                "url": facts.url,
                "score": cand["score"]["score"],
                "score_detail": cand["score"],
                "reference_score": ref_score["score"],
                "category": cand["category"],
                "advantage": advantages[0],
                "advantages": advantages,
                "trade_offs": trade_offs,
            }
        )
    return results


__all__ = ["alternatives", "compare", "reference_categories", "search_off_cached"]
