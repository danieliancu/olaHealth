from __future__ import annotations

import datetime as dt
import uuid
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.http import FileResponse, Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme, urlencode
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy, ngettext
from django.views.decorators.http import require_GET, require_POST

from assessments.services.compare import ComparisonError
from assessments.services.compare import compare as compare_products
from catalog import explanations, presenters
from catalog.countries import country_from_accept_language
from catalog.forms import FORMAT_CHOICES, LookupForm, ScanPhotoForm, SearchForm
from catalog.models import Category, Country, Product
from catalog.services.lookup import lookup
from catalog.services.search import SearchParams, facets, search_products, tag_names
from core.barcodes import decode_product_barcodes
from core.coordination import check_request_limit, rate_limited
from core.images import ImageRejected, validate_and_reencode
from myola import diary_target
from myola import preferences as P
from myola.models import LIMITS, Note, ProductMark, ProductVisit, Profile
from myola.page import facts_from_product
from myola.restrictions import all_statuses, blocking
from myola.services import lists
from myola.services.profiles import derive_prefs

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

# The camera reports formats as "ean_13" (BarcodeDetector) or "EAN13" (zxing).
_FORMATS = {v for v, _label in FORMAT_CHOICES}
_FORMAT_ALIASES = {"itf": "itf14"}

COMPARE_SESSION_KEY = "compare_ids"
COMPARE_MAX = 3
# A reload of the same product (and the same scanned code) is not a new visit.
VISIT_DEDUP = dt.timedelta(minutes=10)

STATUS_TEXT = {
    "compatible": gettext_lazy("Confirmed by the source"),
    "incompatible": gettext_lazy("Not compatible"),
    "may_contain": gettext_lazy("May contain (traces)"),
    "unknown": gettext_lazy("Cannot be confirmed"),
    "contains": gettext_lazy("Contains"),
    "not_declared": gettext_lazy("Not declared"),
}
PROFILE_RESTRICTIONS_URL = "/my-ola/profile/#restrictions"


def _country(request: HttpRequest) -> str:
    """Automatic, from the browser language/region. Never limits the global lookup."""
    return country_from_accept_language(request.META.get("HTTP_ACCEPT_LANGUAGE", ""))


def _htmx(request: HttpRequest) -> bool:
    return request.headers.get("HX-Request") == "true"


def _login_url(next_url: str) -> str:
    return f"{reverse('account_login')}?{urlencode({'next': next_url})}"


def _next_url(request: HttpRequest, default: str) -> str:
    url = request.POST.get("next") or request.GET.get("next") or ""
    if url and url_has_allowed_host_and_scheme(
        url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return url
    return default


def _login_required_response(request: HttpRequest, next_url: str) -> HttpResponse:
    login = _login_url(next_url)
    if _htmx(request):
        response = HttpResponse(status=204)
        response["HX-Redirect"] = login
        return response
    return redirect(login)


# ---------- Compare selection (session, works without an account) ----------
def compare_selection(request: HttpRequest) -> list[str]:
    out: list[str] = []
    for value in request.session.get(COMPARE_SESSION_KEY) or []:
        try:
            pk = str(uuid.UUID(str(value)))
        except ValueError:
            continue
        if pk not in out:
            out.append(pk)
    return out[:COMPARE_MAX]


def _save_compare(request: HttpRequest, ids: list[str]) -> None:
    request.session[COMPARE_SESSION_KEY] = ids[:COMPARE_MAX]


@require_GET
def home(request: HttpRequest) -> HttpResponse:
    """The homepage, for everyone (the logo links here); signed-in users get "Open Today"."""
    return render(request, "catalog/home.html")


@require_GET
def scan(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "catalog/scan.html",
        {
            "form": LookupForm(),
            "photo_form": ScanPhotoForm(),
            **_diary_context(diary_target.from_params(request.GET)),
        },
    )


def _diary_context(target) -> dict:
    """Context for the “add to diary” mode (Food diary → scan or search → how much?)."""
    if not target:
        return {}
    return {
        "diary": target,
        "diary_query": diary_target.query(target),
        "diary_label": diary_target.LABELS.get(target["add"], _("your diary")),
        "diary_back_url": diary_target.today_url(target),
    }


def _normalise_format(params):
    raw = (params.get("format") or "").strip()
    if not raw:
        return params
    value = raw.lower().replace("-", "").replace("_", "")
    value = _FORMAT_ALIASES.get(value, value)
    params = params.copy()
    params["format"] = value if value in _FORMATS else "manual"
    return params


@require_GET
def lookup_view(request: HttpRequest) -> HttpResponse:
    form = LookupForm(_normalise_format(request.GET))
    target = diary_target.from_params(request.GET)
    if not form.is_valid():
        return render(
            request,
            "catalog/scan.html",
            {
                "form": form,
                "photo_form": ScanPhotoForm(),
                **_diary_context(target),
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
        if target:
            return redirect(diary_target.add_url(result.product.pk, target, code))
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
    target = diary_target.from_params(request.POST)
    ctx = {"form": LookupForm(), **_diary_context(target)}
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
        params = {"code": found[0].text, "format": found[0].format, **(target or {})}
        return redirect(f"{reverse('lookup')}?{urlencode(params)}")
    if not found:
        form.add_error(
            "photo",
            _(
                "No product barcode was found in this photo. Try a sharper, closer photo or "
                "type the number."
            ),
        )
        return render(request, "catalog/scan.html", {**ctx, "photo_form": form}, status=422)
    return render(request, "catalog/photo_choices.html", {"codes": found, **_diary_context(target)})


# ---------- Product page ----------
def _record_visit(request: HttpRequest, product: Product, code: str) -> None:
    """History for signed-in users; a reload within a few minutes is not a new visit."""
    if not request.user.is_authenticated:
        return
    recent = ProductVisit.objects.filter(
        user=request.user,
        product=product,
        code=code,
        created_at__gte=timezone.now() - VISIT_DEDUP,
    )
    if not recent.exists():
        lists.record_visit(request.user, product, code)


def _for_you(request: HttpRequest, facts) -> dict | None:
    """Restriction states for the signed-in user's diets and allergies (server-side)."""
    if not request.user.is_authenticated:
        return None
    profile = Profile.objects.filter(user=request.user).first()
    prefs = derive_prefs(profile)
    diets, allergens = prefs["diets"], prefs["allergens"]
    statuses = all_statuses(facts)
    blocked = blocking(statuses, diets, allergens)
    keys = {(b["kind"], b["key"]) for b in blocked}
    confirmed = [
        {"kind": "diet", "key": d, **statuses["diets"][d]} for d in diets if ("diet", d) not in keys
    ] + [
        {"kind": "allergen", "key": a, **statuses["allergens"][a]}
        for a in allergens
        if ("allergen", a) not in keys
    ]
    items = []
    for item in blocked + confirmed:
        label = P.DIETS[item["key"]][0] if item["kind"] == "diet" else P.ALLERGENS[item["key"]]
        items.append({**item, "label": label, "status_text": STATUS_TEXT[item["status"]]})
    names = [str(P.DIETS[d][0]) for d in diets]
    if allergens:
        names.append(
            ngettext("%(n)d allergy", "%(n)d allergies", len(allergens)) % {"n": len(allergens)}
        )
    return {
        "items": items,
        "has_prefs": bool(diets or allergens),
        "summary": ", ".join(names),
        "edit_url": PROFILE_RESTRICTIONS_URL,
    }


def _product_actions_context(request: HttpRequest, product: Product) -> dict:
    marks = lists.marks_for(request.user, product)
    return {
        "product": product,
        "marks": marks,
        "in_compare": str(product.pk) in compare_selection(request),
        "product_url": reverse("product", args=[product.pk]),
    }


@require_GET
@rate_limited("product_view")
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
    scanned = request.GET.get("scanned", "")[:32]
    context = {
        "nutriscore": by_key.get("nutriscore"),
        "nova": by_key.get("nova"),
        "nova_label": presenters.NOVA_SHORT.get(
            int(by_key["nova"].badge) if by_key.get("nova") and by_key["nova"].badge else 0, ""
        ),
        "nutrient_rows": rows,
        "rows_basis": rows_basis,
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
        "scanned": scanned,
        "off_url": f"{settings.OFF_BASE_URL}/product/{product.off_code}"
        if product.off_code
        else "",
        "restricted": any(i.kind == "restricted" for i in product.identifiers.all()),
        **_product_actions_context(request, product),
    }
    context["quality_pills"] = presenters.quality_pills(product, data, context["flags"])
    context["explain"] = {k: explanations.indicator(k) for k in explanations.INDICATORS}
    context["for_you"] = _for_you(request, facts_from_product(product, snapshots))
    context["login_url"] = _login_url(request.get_full_path())
    context["chat_context"] = f"product:{product.pk}"
    context["note_open"] = request.GET.get("note") == "1"
    context["note_kinds"] = Note.Kind.choices
    _record_visit(request, product, scanned)
    return render(request, "catalog/product.html", context)


# ---------- Personal marks: favourite, buy often, avoid, hidden ----------
MARK_MESSAGES = {
    ProductMark.Kind.FAVORITE: (
        gettext_lazy("Removed from favourites."),
        gettext_lazy("Added to favourites."),
    ),
    ProductMark.Kind.BUY_OFTEN: (
        gettext_lazy("Removed from Buy often."),
        gettext_lazy("Added to Buy often. Press again to undo."),
    ),
    ProductMark.Kind.AVOID: (
        gettext_lazy("Removed from Avoid."),
        gettext_lazy(
            "Added to Avoid. Removed from Buy often if it was there. Press again to undo."
        ),
    ),
    ProductMark.Kind.HIDDEN: (
        gettext_lazy("Shown in your lists again."),
        gettext_lazy("Removed from your list. It stays in the catalogue for everyone else."),
    ),
}


def _oob(request: HttpRequest, *, compare: bool = False) -> str:
    """Out-of-band updates sent with every htmx response: messages, counts."""
    return render_to_string(
        "partials/oob.html",
        {"compare_oob": compare, "messages": messages.get_messages(request)},
        request=request,
    )


def _ui_response(request: HttpRequest, product: Product, *, compare: bool = False):
    """The partial that re-renders the control the user pressed (htmx only)."""
    ui = request.POST.get("ui", "")
    next_url = _next_url(request, reverse("product", args=[product.pk]))
    if ui == "card":
        product.in_compare = str(product.pk) in compare_selection(request)
        product.is_fav = ProductMark.Kind.FAVORITE in lists.marks_for(request.user, product)
        body = render_to_string(
            "partials/card_actions.html",
            {"p": product, "open": True, "next_url": next_url},
            request=request,
        )
    elif ui == "page":
        ctx = _product_actions_context(request, product)
        ctx["more_open"] = request.POST.get("more") == "1"
        ctx["next_url"] = next_url
        body = render_to_string("partials/product_actions.html", ctx, request=request)
    else:  # "row", "hide": the row is removed by the swap (hx-swap="delete")
        body = ""
    return HttpResponse(body + _oob(request, compare=compare))


@require_POST
@rate_limited("personal_write")
def product_mark(request: HttpRequest, pk: uuid.UUID, kind: str) -> HttpResponse:
    if kind not in ProductMark.Kind.values:
        raise Http404
    product = get_object_or_404(Product, pk=pk)
    product_url = reverse("product", args=[product.pk])
    if not request.user.is_authenticated:
        return _login_required_response(request, _next_url(request, product_url))
    on = lists.toggle(request.user, product, kind)
    messages.success(request, MARK_MESSAGES[kind][on])
    if _htmx(request):
        return _ui_response(request, product)
    return redirect(_next_url(request, product_url))


@require_POST
@rate_limited("personal_write")
def product_note(request: HttpRequest, pk: uuid.UUID) -> HttpResponse:
    product = get_object_or_404(Product, pk=pk)
    product_url = reverse("product", args=[product.pk])
    if not request.user.is_authenticated:
        return _login_required_response(request, f"{product_url}?note=1#note")
    text = (request.POST.get("text") or "").strip()[:2000]
    kind = request.POST.get("kind") or Note.Kind.GENERAL
    if kind not in Note.Kind.values:
        kind = Note.Kind.GENERAL
    saved = bool(text)
    if saved:
        Note.objects.create(user=request.user, product=product, kind=kind, text=text)
        stale = list(
            Note.objects.filter(user=request.user)
            .order_by("-updated_at")
            .values_list("pk", flat=True)[LIMITS["notes"] :]
        )
        if stale:
            Note.objects.filter(pk__in=stale).delete()
        messages.success(request, _("Note saved in My Track → Notes."))
    else:
        messages.error(request, _("Write a note first."))
    if _htmx(request):
        body = render_to_string(
            "partials/product_note.html",
            {
                "product": product,
                "note_open": not saved,
                "note_kinds": Note.Kind.choices,
                "note_text": "" if saved else text,
            },
            request=request,
        )
        return HttpResponse(body + _oob(request))
    if saved:
        return redirect(product_url)
    return redirect(f"{product_url}?note=1#note")


# ---------- Compare selection endpoints ----------
@require_POST
@rate_limited("personal_write")
def compare_add(request: HttpRequest, pk: uuid.UUID) -> HttpResponse:
    product = get_object_or_404(Product, pk=pk, current_revision__isnull=False)
    ids = compare_selection(request)
    if str(product.pk) not in ids:
        if len(ids) >= COMPARE_MAX:
            messages.error(request, _("You can compare at most 3 products. Remove one first."))
        else:
            ids.append(str(product.pk))
            _save_compare(request, ids)
            if len(ids) >= 2:
                messages.success(
                    request,
                    _("%(n)d products selected. Open Compare to see them.") % {"n": len(ids)},
                )
            else:
                messages.success(request, _("Added. Choose one or two more products."))
    if _htmx(request):
        return _ui_response(request, product, compare=True)
    return redirect(_next_url(request, reverse("product", args=[product.pk])))


@require_POST
@rate_limited("personal_write")
def compare_remove(request: HttpRequest, pk: uuid.UUID) -> HttpResponse:
    ids = [i for i in compare_selection(request) if i != str(pk)]
    _save_compare(request, ids)
    messages.success(request, _("Removed from comparison."))
    if _htmx(request):
        product = Product.objects.filter(pk=pk).first()
        if product is None:
            return HttpResponse(_oob(request, compare=True))
        return _ui_response(request, product, compare=True)
    return redirect(_next_url(request, reverse("compare")))


@require_POST
@rate_limited("personal_write")
def compare_clear(request: HttpRequest) -> HttpResponse:
    request.session.pop(COMPARE_SESSION_KEY, None)
    messages.success(request, _("Comparison cleared."))
    return redirect("compare")


# ---------- Catalogue ----------
def _annotate_cards(request: HttpRequest, products: list) -> None:
    selected = set(compare_selection(request))
    favourites: set = set()
    if request.user.is_authenticated and products:
        favourites = set(
            ProductMark.objects.filter(
                user=request.user,
                kind=ProductMark.Kind.FAVORITE,
                product__in=[p.pk for p in products],
            ).values_list("product_id", flat=True)
        )
    for p in products:
        p.in_compare = str(p.pk) in selected
        p.is_fav = p.pk in favourites


@require_GET
@rate_limited("search")
def product_list(request: HttpRequest) -> HttpResponse:
    form = SearchForm(request.GET or None)
    params = SearchParams()
    if form.is_valid():
        params = SearchParams(**form.cleaned_data)
    results = search_products(params)
    hidden = lists.hidden_ids(request.user)
    if hidden:
        results = results.exclude(pk__in=hidden)
    paginator = Paginator(results, 20)
    page = paginator.get_page(request.GET.get("page"))
    query = request.GET.copy()
    query.pop("page", None)
    target = diary_target.from_params(request.GET)
    if target:
        for p in page.object_list:
            p.diary_url = diary_target.add_url(p.pk, target)
    _annotate_cards(request, list(page.object_list))
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
            "hidden_count": len(hidden),
            "login_url": _login_url(request.get_full_path()),
            **_diary_context(target),
        },
    )


@require_GET
@rate_limited("suggest")
def suggest_html(request: HttpRequest) -> HttpResponse:
    """Search-as-you-type suggestions as a list of links (htmx swaps it under the box)."""
    q = (request.GET.get("q") or "").strip()[:100]
    results: list = []
    if len(q) >= 2:
        qs = search_products(SearchParams(q=q))
        hidden = lists.hidden_ids(request.user)
        if hidden:
            qs = qs.exclude(pk__in=hidden)
        results = list(qs[:6])
    target = diary_target.from_params(request.GET)
    for p in results:
        # From the Food diary: open the "how much?" step instead of the product page.
        p.link = diary_target.add_url(p.pk, target) if target else reverse("product", args=[p.pk])
    all_params = {"q": q, **{k: v for k, v in (target or {}).items() if v}}
    return render(
        request,
        "partials/suggest.html",
        {
            "q": q,
            "results": results,
            "show": len(q) >= 2,
            "all_url": f"{reverse('products')}?{urlencode(all_params)}",
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


# Horizontal bar charts drawn on the server (SVG): one small panel per nutrient.
CHART_LABEL_W = 108
CHART_BAR_W = 150
CHART_ROW_H = 26


def _short(name: str, limit: int = 16) -> str:
    return name if len(name) <= limit else name[: limit - 1] + "…"


def _chart_views(charts: list[dict]) -> list[dict]:
    groups = []
    for group in charts:
        legend = [
            {"name": name, "slot": slot % 3}
            for name, slot in zip(group["products"], group["slots"], strict=False)
        ]
        panels = []
        for panel in group["panels"]:
            known = [v for v in panel["values"] if v is not None]
            top = max(known, default=0) or 1
            bars = []
            for i, (name, slot) in enumerate(zip(group["products"], group["slots"], strict=False)):
                value = panel["values"][i]
                width = 0 if value is None else max(2, round(value / top * CHART_BAR_W, 1))
                y = i * CHART_ROW_H
                bars.append(
                    {
                        "name": _short(name),
                        "full_name": name,
                        "slot": slot % 3,
                        "y": y + 6,
                        "text_y": y + 17,
                        "width": width,
                        "value_x": CHART_LABEL_W + width + 6,
                        "display": panel["display"][i] or "",
                    }
                )
            panels.append(
                {
                    **panel,
                    "bars": bars,
                    "height": len(bars) * CHART_ROW_H + 4,
                    "label_x": CHART_LABEL_W - 6,
                    "bar_x": CHART_LABEL_W,
                    "summary": ", ".join(
                        f"{b['full_name']} {b['display'] or _('not available')}" for b in bars
                    ),
                }
            )
        groups.append({"title": group["title"], "legend": legend, "panels": panels})
    return groups


@require_GET
@rate_limited("compare")
def compare(request: HttpRequest) -> HttpResponse:
    from_query = bool(request.GET.get("ids") or request.GET.getlist("id"))
    ids = _ids(request) if from_query else [uuid.UUID(i) for i in compare_selection(request)]
    found = {
        p.pk: p
        for p in Product.objects.select_related("current_revision").filter(
            pk__in=ids, current_revision__isnull=False
        )
    }
    products = [found[i] for i in ids if i in found]
    if not from_query and len(products) != len(ids):
        # Products removed from the catalogue since they were selected leave the selection.
        _save_compare(request, [str(p.pk) for p in products])
    # Products removed from the catalogue since they were selected are reported, not hidden.
    context = {
        "products": products,
        "missing": len(ids) - len(products),
        "shown_ids": ",".join(str(p.pk) for p in products),
        "requested": bool(ids),
        "from_query": from_query,
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
        p.url = reverse("product", args=[p.pk])
    return render(
        request,
        "catalog/compare.html",
        {**context, "c": comparison, "chart_groups": _chart_views(comparison.charts)},
    )


# ---------- Saved: favourites, recently viewed, hidden ----------
@require_GET
def saved(request: HttpRequest) -> HttpResponse:
    if not request.user.is_authenticated:
        return render(request, "catalog/saved.html", {"login_url": _login_url(reverse("saved"))})
    user = request.user
    marks = ProductMark.objects.filter(
        user=user, kind__in=[ProductMark.Kind.FAVORITE, ProductMark.Kind.HIDDEN]
    ).select_related("product")
    favourites = [m.product for m in marks if m.kind == ProductMark.Kind.FAVORITE]
    hidden = [m.product for m in marks if m.kind == ProductMark.Kind.HIDDEN]
    recent = [v.product for v in lists.recent(user, limit=30)]
    return render(
        request,
        "catalog/saved.html",
        {"favourites": favourites, "recent": recent, "hidden": hidden},
    )


@require_POST
@rate_limited("personal_write")
def saved_clear(request: HttpRequest, what: str) -> HttpResponse:
    if what not in ("history", "hidden"):
        raise Http404
    if not request.user.is_authenticated:
        return _login_required_response(request, reverse("saved"))
    if what == "history":
        ProductVisit.objects.filter(user=request.user).delete()
        messages.success(request, _("History cleared."))
    else:
        ProductMark.objects.filter(user=request.user, kind=ProductMark.Kind.HIDDEN).delete()
        messages.success(request, _("Hidden products are shown again."))
    return redirect(_next_url(request, reverse("saved")))


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


@require_GET
@rate_limited("product_view")
def product_alternatives(request: HttpRequest, pk) -> HttpResponse:
    """Foodo Score of a product and up to 3 better alternatives (htmx fragment or full page)."""
    from assessments.score import score_for_product
    from catalog.services.alternatives import alternatives

    product = get_object_or_404(
        Product.objects.select_related("current_revision"), pk=pk, current_revision__isnull=False
    )
    score = score_for_product(product)
    user = request.user if request.user.is_authenticated else None
    items = alternatives(product, user=user) if score.get("status") == "ok" else []
    context = {"product": product, "score": score, "alternatives": items}
    if request.headers.get("HX-Request"):
        return render(request, "catalog/partials/alternatives.html", context)
    return render(request, "catalog/alternatives.html", context)
