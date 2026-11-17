from __future__ import annotations

import uuid

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from catalog.countries import COUNTRY_CHOICES, COUNTRY_TAGS
from catalog.models import Product, ProductImage
from catalog.views import _country
from contributions.forms import (
    LANG_CHOICES,
    ContributionForm,
    PhotoForm,
    StartForm,
    initial_from,
    initial_from_proposal,
    proposal_summary,
)
from contributions.models import Contribution, ContributionImage, OCRRun
from contributions.services import drafts, ocr
from core.coordination import check_request_limit
from core.images import ImageRejected

STEPS = ("photos", "check", "submit")
PHOTO_SLOTS = (
    (ContributionImage.Kind.FRONT, "Front of pack"),
    (ContributionImage.Kind.INGREDIENTS, "Ingredients"),
    (ContributionImage.Kind.NUTRITION, "Nutrition label"),
)


def _draft(token: str) -> Contribution:
    # The token is looked up through a unique index; an unknown token is a plain 404.
    return get_object_or_404(
        Contribution.objects.select_related("product__current_revision"), access_token=token
    )


def _private_headers(response: HttpResponse) -> HttpResponse:
    response["Cache-Control"] = "private, no-store"
    response["Referrer-Policy"] = "no-referrer"
    response["X-Robots-Tag"] = "noindex"
    return response


def _step_url(c: Contribution, step: str) -> str:
    return f"{reverse('contribution', kwargs={'token': c.access_token})}?step={step}"


@require_POST
def start(request: HttpRequest) -> HttpResponse:
    form = StartForm(request.POST)
    if not form.is_valid():
        raise Http404
    decision = check_request_limit(request, "contribution")
    if not decision.allowed:
        return render(request, "contributions/limited.html", status=429)
    product = None
    if form.cleaned_data.get("product"):
        product = get_object_or_404(Product, pk=form.cleaned_data["product"])
    code = form.cleaned_data.get("code") or ""
    if product is None and not code:
        raise Http404
    c = drafts.create_draft(
        session_key=request.session.session_key or "",
        problem_type=form.cleaned_data["problem_type"],
        product=product,
        code=code,
        code_format=form.cleaned_data.get("format") or "",
        country=_country(request),
    )
    return redirect("contribution", token=c.access_token)


@never_cache
@require_http_methods(["GET", "POST"])
def edit(request: HttpRequest, token: str) -> HttpResponse:
    c = _draft(token)
    if c.status != Contribution.Status.DRAFT:
        return _private_headers(render(request, "contributions/submitted.html", {"c": c}))
    step = request.GET.get("step") if request.GET.get("step") in STEPS else "photos"
    current = c.product.current_revision.data if c.product and c.product.current_revision else {}
    runs = list(
        OCRRun.objects.filter(image__contribution=c).select_related("image").order_by("created_at")
    )
    images = list(c.images.all())
    context: dict = {"c": c, "step": step, "steps": STEPS, "runs": runs, "errors": []}

    if step == "photos":
        if request.method == "POST":
            country = request.POST.get("country", "")
            lang = request.POST.get("label_lang", "")
            drafts.save_details(
                c,
                country if country in COUNTRY_TAGS else "",
                lang if lang in dict(LANG_CHOICES) else "",
            )
            return redirect(_step_url(c, "check"))
        by_kind = {img.kind: img for img in images}
        context.update(
            slots=[(kind, label, by_kind.get(kind)) for kind, label in PHOTO_SLOTS],
            extra_images=[img for img in images if img.kind not in dict(PHOTO_SLOTS)],
            photo_form=PhotoForm(),
            countries=COUNTRY_CHOICES,
            langs=LANG_CHOICES,
        )
    elif step == "check":
        if request.method == "POST":
            form = ContributionForm(request.POST, current=current)
            if form.is_valid():
                report = drafts.save_proposal(
                    c, form.proposed(), form.cleaned_data.get("note") or ""
                )
                if report.ok:
                    return redirect(_step_url(c, "submit"))
                context["errors"] = report.errors
        else:
            use_ocr = request.GET.get("prefill") == "ocr"
            initial = initial_from(current, runs if use_ocr else [])
            if c.proposed and not use_ocr:
                initial.update(initial_from_proposal(c.proposed))
            initial["note"] = c.note
            form = ContributionForm(initial=initial, current=current)
        context.update(form=form, ocr_ready=any(r.status == "done" for r in runs))
    else:
        if request.method == "POST":
            errors = drafts.submit(c, consent=request.POST.get("consent") == "on")
            if not errors:
                return redirect("contribution", token=c.access_token)
            context["errors"] = errors
        context.update(
            summary=proposal_summary(c.proposed or {}),
            images=images,
            consent_text=drafts.CONSENT_TEXT,
        )
    return _private_headers(render(request, "contributions/edit.html", context))


@require_POST
def upload(request: HttpRequest, token: str) -> HttpResponse:
    c = _draft(token)
    form = PhotoForm(request.POST, request.FILES)
    if not form.is_valid():
        messages.error(request, _("Choose a photo and what it shows."))
        return redirect(_step_url(c, "photos"))
    decision = check_request_limit(request, "upload")
    if not decision.allowed:
        messages.error(request, _("Too many uploads. Please wait and try again."))
        return redirect(_step_url(c, "photos"))
    try:
        drafts.add_image(
            c,
            form.cleaned_data["photo"],
            form.cleaned_data["kind"],
            form.cleaned_data.get("lang") or "",
        )
    except ImageRejected as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, _("Photo added."))
    return redirect(_step_url(c, "photos"))


@require_POST
def delete_image(request: HttpRequest, token: str, image_id: uuid.UUID) -> HttpResponse:
    c = _draft(token)
    if c.status == Contribution.Status.DRAFT:
        img = get_object_or_404(ContributionImage, pk=image_id, contribution=c)
        for f in (img.file, img.ocr_file):
            if f:
                f.delete(save=False)
        img.delete()
        messages.success(request, _("Photo removed."))
    return redirect(_step_url(c, "photos"))


@require_GET
def draft_image(request: HttpRequest, token: str, image_id: uuid.UUID) -> FileResponse:
    c = _draft(token)
    img = get_object_or_404(ContributionImage, pk=image_id, contribution=c)
    return _private_headers(FileResponse(img.file.open("rb"), content_type="image/jpeg"))


@never_cache
@require_GET
def ocr_status(request: HttpRequest, token: str) -> JsonResponse:
    c = _draft(token)
    runs = OCRRun.objects.filter(image__contribution=c).select_related("image")
    return JsonResponse(
        {
            "runs": [
                {"id": r.pk, "kind": r.image.kind, "status": r.status, "error": r.error}
                for r in runs
            ]
        }
    )


@require_POST
def ocr_retry(request: HttpRequest, token: str, run_id: int) -> HttpResponse:
    c = _draft(token)
    run = get_object_or_404(OCRRun, pk=run_id, image__contribution=c)
    decision = check_request_limit(request, "ocr_retry")
    if not decision.allowed or not ocr.retry(run):
        messages.error(
            request,
            _("Text recognition cannot be retried now. You can type the information instead."),
        )
    else:
        messages.success(request, _("Text recognition restarted."))
    return redirect(_step_url(c, "check"))


@require_GET
def public_image(request: HttpRequest, pk: uuid.UUID) -> FileResponse:
    """Approved contributed photos. Drafts and rejected photos are never served here."""
    img = get_object_or_404(
        ProductImage.objects.select_related("contribution_image"),
        pk=pk,
        source=ProductImage.Source.CONTRIBUTION,
        contribution_image__is_public=True,
    )
    response = FileResponse(img.contribution_image.file.open("rb"), content_type="image/jpeg")
    response["Cache-Control"] = "public, max-age=86400"
    return response


@staff_member_required
@require_GET
def staff_image(
    request: HttpRequest, image_id: uuid.UUID, variant: str = "original"
) -> FileResponse:
    if not request.user.has_perm("contributions.view_contribution"):
        raise PermissionDenied
    img = get_object_or_404(ContributionImage, pk=image_id)
    f = img.ocr_file if variant == "ocr" and img.ocr_file else img.file
    content_type = "image/png" if f.name.endswith(".png") else "image/jpeg"
    return _private_headers(FileResponse(f.open("rb"), content_type=content_type))
