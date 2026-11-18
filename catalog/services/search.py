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
    "nutriscore": "Nutri-Score (A first)",
    "completeness": "Most complete data",
    "updated": "Recently updated",
}
COMPLETENESS = {"high": (80, 101), "medium": (50, 80), "low": (0, 50)}
# Every possible value of the fixed facets; only those with products are shown.
NUTRISCORE_OPTIONS = {"a": "A", "b": "B", "c": "C", "d": "D", "e": "E", "none": "No Nutri-Score"}
COMPLETENESS_OPTIONS = {"high": "80% or more", "medium": "50–79%", "low": "Below 50%"}


@dataclass
class SearchParams:
    q: str = ""
    category: str = ""
    country: str = ""
    nutriscore: str = ""
    completeness: str = ""
    sort: str = "relevance"


def _filtered(params: SearchParams, skip: str = "") -> QuerySet[Product]:
    """Published products matching every filter except ``skip`` (used for facets)."""
    qs = Product.objects.filter(current_revision__isnull=False)
    if params.category and skip != "category":
        qs = qs.filter(categories__contains=[params.category])
    if params.country and skip != "country":
        qs = qs.filter(countries__contains=[params.country])
    if skip != "nutriscore":
        if params.nutriscore in ("a", "b", "c", "d", "e"):
            qs = qs.filter(nutriscore_grade=params.nutriscore)
        elif params.nutriscore == "none":
            qs = qs.filter(nutriscore_grade="")
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
        "categories",
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
    grades = dict(
        _filtered(params, "nutriscore")
        .values_list("nutriscore_grade")
        .annotate(n=Count("id"))
        .order_by()
    )
    nutriscore = [
        FacetOption(
            value,
            label,
            grades.get("" if value == "none" else value, 0),
            params.nutriscore == value,
        )
        for value, label in NUTRISCORE_OPTIONS.items()
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
        "nutriscore": [o for o in nutriscore if o.count or o.selected],
        "completeness": [o for o in completeness if o.count or o.selected],
    }


def tag_names(tags: list[str], model) -> dict[str, str]:
    """Readable names for taxonomy tags (falls back to the tag itself)."""
    names = dict(model.objects.filter(tag__in=tags).values_list("tag", "name"))
    return {t: names.get(t) or _tag_label(t) for t in tags}
