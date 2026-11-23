"""Search, filters and sorting over the local catalogue, entirely in PostgreSQL."""

from __future__ import annotations

from dataclasses import dataclass

from django.contrib.postgres.search import TrigramWordSimilarity
from django.db.models import Count, F, Func, Q, QuerySet, Value
from django.db.models.functions import Greatest, Upper

from catalog.models import Category, Country, Product
from catalog.services.revisions import fold

SORTS = {
    "relevance": "Relevance",
    "name": "Name",
    "score": "Foodo Score (highest first)",
    "completeness": "Most complete data",
    "updated": "Recently updated",
}
COMPLETENESS = {"high": (80, 101), "medium": (50, 80), "low": (0, 50)}
# Foodo Score ranges (assessments/foodo.py display bands); "none": no score.
SCORE_RANGES = {"80": (80, 101), "60": (60, 80), "40": (40, 60), "20": (20, 40), "0": (0, 20)}
# Every possible value of the fixed facets; only those with products are shown.
SCORE_OPTIONS = {
    "80": "80–100",
    "60": "60–79",
    "40": "40–59",
    "20": "20–39",
    "0": "0–19",
    "none": "No score",
}
COMPLETENESS_OPTIONS = {"high": "80% or more", "medium": "50–79%", "low": "Below 50%"}


@dataclass
class SearchParams:
    q: str = ""
    category: str = ""
    country: str = ""
    score: str = ""
    completeness: str = ""
    sort: str = "relevance"
    # Staff only: also products kept out of listings for missing name or nutrition data.
    include_unlisted: bool = False
    unlisted_only: bool = False


def _filtered(params: SearchParams, skip: str = "") -> QuerySet[Product]:
    """Published products matching every filter except ``skip`` (used for facets)."""
    qs = Product.objects.filter(current_revision__isnull=False)
    if params.unlisted_only:
        qs = qs.exclude(quality_issues=[])
    elif not params.include_unlisted:
        qs = qs.filter(listable=True)  # catalog/quality.py: no usable name or nutrition data
    if params.category and skip != "category":
        qs = qs.filter(categories__contains=[params.category])
    if params.country and skip != "country":
        qs = qs.filter(countries__contains=[params.country])
    if skip != "score":
        if params.score in SCORE_RANGES:
            low, high = SCORE_RANGES[params.score]
            qs = qs.filter(foodo_value__gte=low, foodo_value__lt=high)
        elif params.score == "none":
            qs = qs.filter(foodo_value__isnull=True)
    if params.completeness in COMPLETENESS and skip != "completeness":
        low, high = COMPLETENESS[params.completeness]
        qs = qs.filter(completeness__gte=low, completeness__lt=high)
    term = fold(params.q.strip())[:100]
    if term:
        # Upper() matches the expression index (gin_trgm_ops on UPPER(search_text)).
        # Both predicates can use that index: LIKE '%..%' and the <% word-similarity operator.
        upper = term.upper()
        qs = qs.annotate(
            search_upper=Upper("search_text"),
            similarity=Greatest(
                TrigramWordSimilarity(Value(upper), Upper("search_text")), Value(0.0)
            ),
        )
        if len(term) < 4:
            # Very short input (search-as-you-type): plain substring, no fuzzy matches.
            qs = qs.filter(search_upper__contains=upper)
        else:
            qs = qs.filter(
                Q(search_upper__contains=upper)
                | Q(search_upper__trigram_word_similar=upper)
                | Q(similarity__gte=0.35)  # tolerant fallback for typos (e.g. yoghurt/yogurt)
            )
    return qs


def search_products(params: SearchParams) -> QuerySet[Product]:
    qs = _filtered(params)
    term = fold(params.q.strip())[:100]
    sort = params.sort if params.sort in SORTS else "relevance"
    if sort == "relevance" and term:
        qs = qs.order_by("-similarity", "name", "id")
    elif sort == "score":
        # No score (unknown, not applicable, not current...) sorts last.
        qs = qs.order_by(F("foodo_value").desc(nulls_last=True), "name", "id")
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
        "foodo_value",
        "nova_group",
        "completeness",
        "front_image_url",
        "categories",
        "quality_issues",
        "listable",
        "updated_at",
        "current_revision__id",
        "current_revision__created_at",
    )


@dataclass
class FacetOption:
    value: str
    label: str
    count: int
    selected: bool = False


def _tag_label(tag: str) -> str:
    text = tag.split(":", 1)[-1].replace("-", " ")
    return text[:1].upper() + text[1:]


def _tag_facet(params: SearchParams, field: str, skip: str, names_model) -> list[FacetOption]:
    rows = (
        _filtered(params, skip)
        .annotate(tag=Func(F(field), function="unnest"))
        .values("tag")
        .annotate(n=Count("id"))
        .order_by()
    )
    counts = {r["tag"]: r["n"] for r in rows}
    selected = getattr(params, skip)
    if selected and selected not in counts:
        counts[selected] = 0
    names = dict(names_model.objects.filter(tag__in=counts).values_list("tag", "name"))
    options = [
        FacetOption(tag, names.get(tag) or _tag_label(tag), n, tag == selected)
        for tag, n in counts.items()
    ]
    return sorted(options, key=lambda o: o.label.lower())


def facets(params: SearchParams) -> dict[str, list[FacetOption]]:
    """Filter options that match the products we have now, given the other filters."""
    ranges = _filtered(params, "score").aggregate(
        none=Count("id", filter=Q(foodo_value__isnull=True)),
        **{
            f"r{key}": Count("id", filter=Q(foodo_value__gte=low, foodo_value__lt=high))
            for key, (low, high) in SCORE_RANGES.items()
        },
    )
    score = [
        FacetOption(
            value,
            label,
            ranges["none" if value == "none" else f"r{value}"],
            params.score == value,
        )
        for value, label in SCORE_OPTIONS.items()
    ]
    buckets = _filtered(params, "completeness").aggregate(
        **{
            key: Count("id", filter=Q(completeness__gte=low, completeness__lt=high))
            for key, (low, high) in COMPLETENESS.items()
        }
    )
    completeness = [
        FacetOption(key, label, buckets[key], params.completeness == key)
        for key, label in COMPLETENESS_OPTIONS.items()
    ]
    return {
        "category": _tag_facet(params, "categories", "category", Category),
        "country": _tag_facet(params, "countries", "country", Country),
        "score": [o for o in score if o.count or o.selected],
        "completeness": [o for o in completeness if o.count or o.selected],
    }


def tag_names(tags: list[str], model) -> dict[str, str]:
    """Readable names for taxonomy tags (falls back to the tag itself)."""
    names = dict(model.objects.filter(tag__in=tags).values_list("tag", "name"))
    return {t: names.get(t) or _tag_label(t) for t in tags}
