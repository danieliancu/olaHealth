from __future__ import annotations

import uuid
from pathlib import Path

from django.conf import settings
from django.core.paginator import Paginator
from django.http import FileResponse, Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import urlencode
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST

from assessments.services.compare import ComparisonError
from assessments.services.compare import compare as compare_products
from catalog import presenters
from catalog.countries import country_from_accept_language
from catalog.forms import LookupForm, ScanPhotoForm, SearchForm
from catalog.models import Category, Country, Product
from catalog.services.lookup import lookup
from catalog.services.search import SearchParams, facets, search_products, tag_names
from core.barcodes import decode_product_barcodes
from core.coordination import check_request_limit
from core.images import ImageRejected, validate_and_reencode

OFF_DOWN = (
    "The product database isn't responding right now (a temporary problem on its side). "
    "Please try again in a few minutes."
)

ERROR_MESSAGES = {
    "invalid_code": "This is not a valid barcode.",
    "not_found": "This product is not in our database yet.",
    "timeout": OFF_DOWN,
    "rate_limited": "Too many lookups right now. Please wait a moment and try again.",
    "upstream_unavailable": OFF_DOWN,
    "invalid_response": "The product database returned data we could not read.",
}


def _country(request: HttpRequest) -> str:
    """Automatic, from the browser language/region. Never limits the global lookup."""
    return country_from_accept_language(request.META.get("HTTP_ACCEPT_LANGUAGE", ""))


@require_GET
def home(request: HttpRequest) -> HttpResponse:
    return render(request, "catalog/home.html")


@require_GET
def scan(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "catalog/scan.html",
        {
            "form": LookupForm(),
            "photo_form": ScanPhotoForm(),
        },
    )


@require_GET
def lookup_view(request: HttpRequest) -> HttpResponse:
    form = LookupForm(request.GET)
    if not form.is_valid():
        return render(
            request,
            "catalog/scan.html",
            {
                "form": form,
                "photo_form": ScanPhotoForm(),
            },
            status=400,
        )
    decision = check_request_limit(request, "lookup")
    if not decision.allowed:
        return render(
            request,
            "catalog/lookup_result.html",
            {
                "error": "rate_limited",
                "message": _(ERROR_MESSAGES["rate_limited"]),
                "code": form.cleaned_data["code"],
            },
            status=429,
        )
    code = form.cleaned_data["code"]
    result = lookup(
        code,
        form.cleaned_data.get("format") or "manual",
        country=_country(request),
        allow_bad_check=form.cleaned_data.get("allow_bad_check") or False,
    )
    if result.status == "found":
        url = reverse("product", args=[result.product.pk])
        return redirect(f"{url}?{urlencode({'scanned': code})}")
    context = {
        "code": code,
        "format": form.cleaned_data.get("format") or "manual",
        "status": result.status,
        "error": result.error,
        "message": result.message or _(ERROR_MESSAGES.get(result.error or result.status, "")),
        "parsed": result.code,
        "can_force": result.detail == "bad_check_digit",
    }
    status = {
        "not_found": 404,
        "pending": 202,
        "error": 400 if result.error == "invalid_code" else 503,
    }
    return render(request, "catalog/lookup_result.html", context, status=status[result.status])


@require_POST
def scan_photo(request: HttpRequest) -> HttpResponse:
    form = ScanPhotoForm(request.POST, request.FILES)
    ctx = {"form": LookupForm()}
    if not form.is_valid():
        return render(request, "catalog/scan.html", {**ctx, "photo_form": form}, status=400)
    decision = check_request_limit(request, "scan_photo")
    if not decision.allowed:
        form.add_error("photo", _("Too many photos. Please wait and try again."))
        return render(request, "catalog/scan.html", {**ctx, "photo_form": form}, status=429)
    try:
        # Processed in memory only; the photo is never written to storage.
        clean = validate_and_reencode(form.cleaned_data["photo"])
        found = decode_product_barcodes(clean.image)
    except ImageRejected as exc:
        form.add_error("photo", str(exc))
        return render(request, "catalog/scan.html", {**ctx, "photo_form": form}, status=400)
    finally:
        form.cleaned_data["photo"].close()
    if len(found) == 1:
        return redirect(
            f"{reverse('lookup')}?{urlencode({'code': found[0].text, 'format': found[0].format})}"
        )
    if not found:
        form.add_error(
            "photo",
            _(
                "No product barcode was found in this photo. Try a sharper, closer photo or "
                "type the number."
            ),
        )
        return render(request, "catalog/scan.html", {**ctx, "photo_form": form}, status=422)
    return render(request, "catalog/photo_choices.html", {"codes": found})


@require_GET
def product_detail(request: HttpRequest, pk: uuid.UUID) -> HttpResponse:
    product = get_object_or_404(
        Product.objects.select_related("current_revision").prefetch_related(
            "images", "identifiers"
        ),
        pk=pk,
        current_revision__isnull=False,
    )
    revision = product.current_revision
    data = revision.data
    snapshots = list(revision.assessments.all())
    indicators = presenters.indicator_views(product, snapshots)
    packaging = next(
        (s for s in snapshots if s.origin == "packaging_declared" and s.indicator == "nutriscore"),
        None,
    )
    by_key = {i.key: i for i in indicators}
    rows, rows_basis = presenters.nutrient_rows(
        data,
        next(
            (
                s
                for s in snapshots
                if s.indicator == "nutrient_levels" and s.origin == "off_computed"
            ),
            None,
        ),
    )
    context = {
        "nutriscore": by_key.get("nutriscore"),
        "nova": by_key.get("nova"),
        "nova_label": presenters.NOVA_SHORT.get(
            int(by_key["nova"].badge) if by_key.get("nova") and by_key["nova"].badge else 0, ""
        ),
        "nutrient_rows": rows,
        "rows_basis": rows_basis,
        "product": product,
        "revision": revision,
        "data": data,
        "indicators": presenters.available_indicators(indicators),
        "ingredient_list": presenters.split_ingredients(
            (data.get("ingredients") or {}).get("text")
        ),
        "additives": next(
            (i for i in indicators if i.key == "additives" and i.data_status == "available"), None
        ),
        "packaging_nutriscore": packaging,
        "gaps": presenters.gaps(product, data, indicators),
        "flags": presenters.open_flags(product),
        "nutrition": presenters.nutrition_groups(data),
        "nutrition_status": (data.get("nutrition") or {}).get("status", "unknown"),
        "allergens": presenters.section_list(data.get("allergens")),
        "traces": presenters.section_list(data.get("traces")),
        "categories": list(tag_names(data.get("categories") or [], Category).values()),
        "countries": list(tag_names(data.get("countries") or [], Country).values()),
        "images": presenters.public_images(product),
        "scanned": request.GET.get("scanned", "")[:32],
        "off_url": f"{settings.OFF_BASE_URL}/product/{product.off_code}"
        if product.off_code
        else "",
        "restricted": any(i.kind == "restricted" for i in product.identifiers.all()),
    }
    return render(request, "catalog/product.html", context)


@require_GET
def product_list(request: HttpRequest) -> HttpResponse:
    form = SearchForm(request.GET or None)
    params = SearchParams()
    if form.is_valid():
        params = SearchParams(**form.cleaned_data)
    paginator = Paginator(search_products(params), 20)
    page = paginator.get_page(request.GET.get("page"))
    query = request.GET.copy()
    query.pop("page", None)
    mixed_versions = (
        params.sort == "nutriscore"
        and len({p.nutriscore_version for p in page.object_list if p.nutriscore_grade}) > 1
    )
    return render(
        request,
        "catalog/products.html",
        {
            "form": form,
            "page": page,
            "query": query.urlencode(),
            "mixed_versions": mixed_versions,
            "facets": facets(params),
        },
    )


def _ids(request: HttpRequest) -> list[uuid.UUID]:
    raw = request.GET.getlist("id") or request.GET.get("ids", "").split(",")
    ids: list[uuid.UUID] = []
    for value in raw:
        try:
            pk = uuid.UUID(value.strip())
        except ValueError:
            continue
        if pk not in ids:
            ids.append(pk)
    return ids[:3]


@require_GET
def compare(request: HttpRequest) -> HttpResponse:
    ids = _ids(request)
    found = {
        p.pk: p
        for p in Product.objects.select_related("current_revision").filter(
            pk__in=ids, current_revision__isnull=False
        )
    }
    products = [found[i] for i in ids if i in found]
    # Products removed from the catalogue since they were selected are reported, not hidden.
    context = {
        "products": products,
        "missing": len(ids) - len(products),
        "shown_ids": ",".join(str(p.pk) for p in products),
        "requested": bool(ids),
    }
    try:
        comparison = compare_products(products)
    except ComparisonError as exc:
        return render(request, "catalog/compare.html", {**context, "error": str(exc)})
    base = reverse("compare")
    remove_urls = {
        p.pk: f"{base}?ids=" + ",".join(str(o.pk) for o in products if o.pk != p.pk)
        for p in products
    }
    for p in comparison.products:
        p.remove_url = remove_urls[p.pk]
    return render(request, "catalog/compare.html", {**context, "c": comparison})


@require_GET
def saved(request: HttpRequest) -> HttpResponse:
    return render(request, "catalog/saved.html")


def _latest_export() -> Path | None:
    path = settings.EXPORT_ROOT / "olahealth-catalogue-latest.jsonl.gz"
    return path if path.is_file() else None


@require_GET
def sources(request: HttpRequest) -> HttpResponse:
    export = _latest_export()
    return render(
        request,
        "catalog/sources.html",
        {
            "export": export,
            "export_size": export.stat().st_size if export else 0,
            "export_mtime": export.stat().st_mtime if export else None,
        },
    )


@require_GET
def export_download(request: HttpRequest) -> FileResponse:
    export = _latest_export()
    if export is None:
        raise Http404("No export has been generated yet.")
    return FileResponse(
        export.open("rb"),
        as_attachment=True,
        filename="olahealth-catalogue.jsonl.gz",
        content_type="application/gzip",
    )


@require_GET
def off_image(request: HttpRequest, pk: uuid.UUID) -> HttpResponse:
    """OFF product photo served from the local cache (downloaded on first use)."""
    from django.core.files.storage import default_storage
    from django.http import HttpResponseRedirect

    from catalog.image_cache import allowed, cached, queue_cache
    from catalog.models import ProductImage

    image = get_object_or_404(ProductImage, pk=pk, source=ProductImage.Source.OFF)
    name = cached(image)
    if name is None:
        # Not cached yet: the worker downloads it; meanwhile the browser gets the original,
        # an allow-listed OFF URL stored at import time. No network call in this request.
        queue_cache(image)
        if allowed(image.remote_url):
            response = HttpResponseRedirect(image.remote_url)
            response["Cache-Control"] = "no-store"
            return response
        raise Http404
    response = FileResponse(default_storage.open(name, "rb"), content_type="image/jpeg")
    response["Cache-Control"] = "public, max-age=604800"
    return response
