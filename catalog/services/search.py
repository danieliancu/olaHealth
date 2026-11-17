"""Search, filters and sorting over the local catalogue, entirely in PostgreSQL."""

from __future__ import annotations

from dataclasses import dataclass

from django.contrib.postgres.search import TrigramWordSimilarity
from django.db.models import F, Func, Q, QuerySet, Value
from django.db.models.functions import Greatest, Upper

from catalog.models import Product
from catalog.services.revisions import fold

SORTS = {
    "relevance": "Relevance",
    "name": "Name",
    "nutriscore": "Nutri-Score (A first)",
    "completeness": "Most complete data",
    "updated": "Recently updated",
}
COMPLETENESS = {"high": (80, 101), "medium": (50, 80), "low": (0, 50)}


@dataclass
class SearchParams:
    q: str = ""
    category: str = ""
    country: str = ""
    nutriscore: str = ""
    completeness: str = ""
    sort: str = "relevance"
    include_demo: bool = False


def search_products(params: SearchParams) -> QuerySet[Product]:
    qs = Product.objects.filter(current_revision__isnull=False)
    qs = qs.filter(is_demo=params.include_demo) if params.include_demo else qs.filter(is_demo=False)
    if params.category:
        qs = qs.filter(categories__contains=[params.category])
    if params.country:
        qs = qs.filter(countries__contains=[params.country])
    if params.nutriscore in ("a", "b", "c", "d", "e"):
        qs = qs.filter(nutriscore_grade=params.nutriscore)
    elif params.nutriscore == "none":
        qs = qs.filter(nutriscore_grade="")
    if params.completeness in COMPLETENESS:
        low, high = COMPLETENESS[params.completeness]
        qs = qs.filter(completeness__gte=low, completeness__lt=high)

    term = fold(params.q.strip())[:100]
    sort = params.sort if params.sort in SORTS else "relevance"
    if term:
        # Upper() matches the expression index (gin_trgm_ops on UPPER(search_text)).
        # Both predicates can use that index: LIKE '%..%' and the <% word-similarity operator.
        upper = term.upper()
        qs = qs.annotate(
            search_upper=Upper("search_text"),
            similarity=Greatest(
                TrigramWordSimilarity(Value(upper), Upper("search_text")), Value(0.0)
            ),
        ).filter(
            Q(search_upper__contains=upper)
            | Q(search_upper__trigram_word_similar=upper)
            | Q(similarity__gte=0.35)  # tolerant fallback for typos (e.g. yoghurt/yogurt)
        )
    if sort == "relevance" and term:
        qs = qs.order_by("-similarity", "name", "id")
    elif sort == "nutriscore":
        # Empty grade (unknown / not current) sorts last; ties broken by version, then name.
        qs = qs.annotate(
            grade_sort=Func(F("nutriscore_grade"), Value(""), function="NULLIF")
        ).order_by(F("grade_sort").asc(nulls_last=True), "nutriscore_version", "name", "id")
    elif sort == "completeness":
        qs = qs.order_by("-completeness", "name", "id")
    elif sort == "updated":
        qs = qs.order_by("-updated_at", "id")
    else:
        qs = qs.order_by("name", "id")
    return qs.select_related("current_revision").only(
        "id",
        "name",
        "brand",
        "quantity",
        "nutriscore_grade",
        "nutriscore_version",
        "nova_group",
        "completeness",
        "front_image_url",
        "is_demo",
        "categories",
        "updated_at",
        "current_revision__id",
        "current_revision__created_at",
    )


def facet_values(field: str, limit: int = 200) -> list[str]:
    """Distinct tags used by published products (unnest in SQL)."""
    assert field in ("categories", "countries")
    qs = (
        Product.objects.filter(current_revision__isnull=False, is_demo=False)
        .annotate(tag=Func(F(field), function="unnest"))
        .values_list("tag", flat=True)
        .distinct()
        .order_by("tag")
    )
    return list(qs[:limit])
