"""Better alternatives for a product (V2, docs/mvp-api-contracts.md §3).

* Reference category: the product's most specific category, then its parent; broad ones
  ("plant-based foods", "spreads", …) are never used.
* Every candidate is checked before it is shown: it must be filed under that category and
  have a usable name and nutrition data. Same-category candidates rank before the parent's.
* Candidates: published local products (a current revision exists, so contributions still
  under review are never used) and an Open Food Facts search in that category for the UK,
  cached per (category, country) for 24 h. OFF results are normalised with
  ``off.mapping.map_product`` into the same shape as ``revision.data``.
* Python decides: strict restrictions and the Avoid list exclude candidates (never sent to
  OFF). Only a strictly better official Nutri-Score grade in the same Nutri-Score version
  (Foodo Score ``score.v2``, ``assessments.score.is_better``) is kept; NOVA and additives
  never rank, they are listed as advantages or trade-offs.
* On any OFF error the local candidates are still used.
* Once the local catalogue is active (``catalog.services.catalog_mode``: a verified OFF dump
  import), candidates come from it only: no OFF search, and more local products are read.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlencode

from django.conf import settings
from django.core.cache import cache
from django.urls import reverse

from assessments.score import (
    foodo_value,
    indicators_from_snapshots,
    is_better,
    score_for_revision,
    score_from_off,
)
from catalog import quality
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


def _depth(tag: str, parents_of: dict[str, list[str]]) -> int:
    """How many ancestors ``tag`` has in the taxonomy (first parent chain, bounded)."""
    depth, seen = 0, {tag}
    while (parents := parents_of.get(tag)) and depth < 12:
        tag = parents[0]
        if tag in seen:
            break
        seen.add(tag)
        depth += 1
    return depth


def reference_categories(product: Product) -> list[str]:
    """The product's most specific category, then its parent (when known), never a broad one.

    Broad tags ("plant-based foods", "spreads", …: ``quality.BROAD_CATEGORIES``) say nothing
    about what kind of food it is, so they are never used: peanut butter must not be matched
    with garlic or passata. With the OFF taxonomy synced the deepest tag wins; otherwise the
    last one (OFF lists tags from general to specific)."""
    tags = [
        t
        for t in (product.data or {}).get("categories") or product.categories or []
        if not quality.is_broad(t)
    ]
    if not tags:
        return []
    parents_of: dict[str, list[str]] = {}
    pending = set(tags)
    for _ in range(12):  # load the ancestors of the product's tags, level by level
        rows = dict(Category.objects.filter(tag__in=pending).values_list("tag", "parents"))
        parents_of.update(rows)
        pending = {p for ps in rows.values() for p in ps or []} - set(parents_of)
        if not pending:
            break
    # Deepest tag in the taxonomy wins; without it, the last one (OFF: general to specific).
    deepest = max(reversed(tags), key=lambda t: _depth(t, parents_of))
    specific = deepest if parents_of else tags[-1]
    parents = [p for p in parents_of.get(specific) or [] if not quality.is_broad(p)]
    if parents:
        # Prefer a parent the product itself is filed under.
        parent = next((p for p in parents if p in tags), parents[0])
    else:
        earlier = [t for t in tags if t != specific]
        parent = earlier[-1] if earlier and specific not in parents_of else None
    return [specific] + ([parent] if parent and parent != specific else [])


def search_off_cached(
    category: str, country: str | None = UK, *, client: OFFClient | None = None
) -> list[dict[str, Any]]:
    """Raw OFF search results for (category, country), cached 24 h. ``[]`` on OFF errors.
    Without a country (not detected), all countries."""
    from catalog.services.lookup import off_circuit_open

    # v2: results now carry the Nutri-Score version; older cached results did not.
    key = f"alternatives:off-search:v2:{country or 'world'}:{category}"[:250]
    cached = cache.get(key)
    if cached is not None:
        return cached
    if off_circuit_open():
        return []
    try:
        products = (client or default_client()).search(
            category, country or "", page_size=SEARCH_PAGE_SIZE
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
        "score": score_for_revision(p.current_revision),
        "category": category,
    }


def _off_candidate(raw: dict[str, Any], category: str) -> dict[str, Any] | None:
    code = str(raw.get("code") or "").strip()
    if not code.isdigit():
        return None
    # Never trust the search alone: the result must really be filed under that category.
    if category not in (raw.get("categories_tags") or []):
        return None
    code = off_normalize(code)
    data = map_product(raw)
    if not quality.listable(quality.issues(data, has_front_image=True)):
        return None  # no usable name or nutrition data: never suggested
    image = (image_urls(raw, settings.OFF_IMAGE_HOSTS) or {}).get("front", "")
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
        "score": score_from_off(raw),
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
    country: str | None = UK,
    client: OFFClient | None = None,
) -> list[dict[str, Any]]:
    """Up to ``limit`` better products listed for ``country`` on Open Food Facts.

    ``by="foodo"`` (Foodo Score shown): only the product's own commercial category, the same
    Nutri-Score version and calculation category, a strictly higher Foodo Score (a tie after
    rounding is excluded), highest first. ``by="grade"`` (public, before the Foodo Score is
    public): a strictly better official letter, same version and category; the parent
    category is tried when the category has too few.

    Each item: ``source`` ("local" or "off"), ``product_id`` (local) / ``off_code``,
    ``name``, ``brand``, ``quantity``, ``image``, ``url``, ``foodo`` (0–100), ``grade`` and
    ``reference_grade`` (official letters), ``nutriscore_version``, ``score_detail`` (the full
    result), ``category``, ``advantage`` (main one), ``advantages`` and ``trade_offs``.
    """
    from catalog.services.catalog_mode import local_catalogue_active
    from myola.page import facts_from_product

    if product.current_revision is None or limit <= 0:
        return []
    local_only = local_catalogue_active()
    local_limit = settings.OFF_LOCAL_ALTERNATIVES_LIMIT if local_only else LOCAL_LIMIT
    ref_snapshots = list(product.current_revision.assessments.all())
    ref_ind = indicators_from_snapshots(ref_snapshots)
    ref_score = score_for_revision(product.current_revision)
    if ref_score["status"] != "ok":
        return []  # nothing can be shown as better than an unknown score
    ref_facts = facts_from_product(product, ref_snapshots)
    prefs, avoid_ids, avoid_codes = _user_filters(user)
    own_code = off_normalize(product.off_code) if product.off_code else None

    categories = reference_categories(product)[:1]  # the same commercial category only
    chosen: dict[str, dict[str, Any]] = {}
    seen: set[str] = {str(product.pk)} | ({own_code} if own_code else set())
    for level, category in enumerate(categories):
        candidates: list[dict[str, Any]] = []
        local = Product.objects.filter(
            categories__contains=[category], current_revision__isnull=False, listable=True
        )
        if country:
            local = local.filter(countries__contains=[country])
        local = (
            local.exclude(pk=product.pk)
            .select_related("current_revision")
            .prefetch_related("current_revision__assessments")
            .order_by("-completeness", "name")[:local_limit]
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
        for raw in [] if local_only else search_off_cached(category, country, client=client):
            cand = _off_candidate(raw, category)
            if cand is None or cand["off_code"] in seen:
                continue
            seen.add(cand["off_code"])
            if cand["off_code"] in avoid_codes:
                continue
            candidates.append(cand)

        for cand in candidates:
            if not is_better(cand["score"], ref_score):
                continue
            if _blocked(cand["facts"], prefs):
                continue
            key = cand["product_id"] or cand["off_code"]
            cand["level"] = level  # 0: same category, 1: its parent
            chosen.setdefault(key, cand)
        if len(chosen) >= limit:
            break

    ranked = sorted(
        chosen.values(),
        key=lambda c: (-foodo_value(c["score"]), c["facts"].name.lower(), c["off_code"] or ""),
    )[:limit]
    results = []
    for cand in ranked:
        facts: ProductFacts = cand["facts"]
        advantages, trade_offs = compare(ref_facts, ref_ind, facts, cand["indicators"])
        grade = cand["score"]["nutriscore"]["grade"]
        ref_grade = ref_score["nutriscore"]["grade"]
        if not advantages:
            advantages = [
                f"Foodo Score {foodo_value(cand['score'])}/100 vs {foodo_value(ref_score)}/100"
            ]
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
                "foodo": foodo_value(cand["score"]),
                "reference_foodo": foodo_value(ref_score),
                "grade": grade,
                "reference_grade": ref_grade,
                "nutriscore_version": ref_score["nutriscore"]["version"],
                "score_detail": cand["score"],
                "category": cand["category"],
                "advantage": advantages[0],
                "advantages": advantages,
                "trade_offs": trade_offs,
            }
        )
    return results


__all__ = ["alternatives", "compare", "reference_categories", "search_off_cached"]
