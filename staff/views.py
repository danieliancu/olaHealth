"""Staff panel. Server-rendered; htmx only swaps list results and user rows.

Every page works without JavaScript (forms POST and redirect). See ``staff.access`` for
the permission each section needs.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.db.models import Count, Exists, IntegerField, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce
from django.http import HttpRequest, HttpResponse, QueryDict
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from catalog.models import Product, ProductIdentifier, ProductRevision, ReviewFlag
from catalog.services.search import SearchParams, search_products
from contributions.models import Contribution, ModerationAudit, OCRRun
from contributions.services import moderation
from off.mapping import DATA_FIELDS
from staff import access, services
from staff.access import staff_view
from staff.forms import (
    ContributionFilterForm,
    DecisionForm,
    DeleteUserForm,
    FlagActionForm,
    FlagFilterForm,
    SiteSettingsForm,
    UserFilterForm,
)
from staff.models import SiteSettings, StaffAction

PAGE_SIZE = 25


# ---------- Helpers ----------


def is_htmx(request: HttpRequest) -> bool:
    return request.headers.get("HX-Request") == "true" and not request.headers.get("HX-Boosted")


def page_of(request: HttpRequest, qs):
    return Paginator(qs, PAGE_SIZE).get_page(request.GET.get("page"))


def query_without_page(request: HttpRequest) -> str:
    q: QueryDict = request.GET.copy()
    q.pop("page", None)
    return q.urlencode()


def staff_render(
    request: HttpRequest, template: str, context: dict[str, Any], section: str, partial: str = ""
) -> HttpResponse:
    """Render a panel page, or only ``partial`` for htmx requests."""
    if partial and is_htmx(request):
        response = render(request, partial, context)
        response["Vary"] = "HX-Request"
        return response
    context = {**context, "section": section, "nav": access.sections(request.user)}
    response = render(request, template, context)
    if partial:
        response["Vary"] = "HX-Request"
    return response


def safe_next(request: HttpRequest, default: str) -> str:
    target = request.POST.get("next") or ""
    if target and url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return target
    return default


def pretty(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=1)


# ---------- Dashboard ----------


@staff_view()
def dashboard(request: HttpRequest) -> HttpResponse:
    return staff_render(
        request, "staff/dashboard.html", {"stats": services.dashboard_stats()}, "dashboard"
    )


# ---------- Moderation ----------


@staff_view(access.PERM_VIEW_CONTRIBUTIONS)
def contributions(request: HttpRequest) -> HttpResponse:
    form = ContributionFilterForm(request.GET or None)
    data = form.cleaned_data if form.is_valid() else {}
    status = data.get("status") or "submitted"
    qs = Contribution.objects.exclude(status=Contribution.Status.DRAFT).select_related("product")
    if status != "all":
        qs = qs.filter(status=status)
    if data.get("problem_type"):
        qs = qs.filter(problem_type=data["problem_type"])
    if data.get("age"):
        qs = qs.filter(submitted_at__lt=timezone.now() - timedelta(days=int(data["age"])))
    qs = qs.annotate(photos=Count("images")).order_by(
        "submitted_at" if status in ("submitted", "conflict") else "-submitted_at", "pk"
    )
    context = {
        "form": form,
        "page": page_of(request, qs),
        "query": query_without_page(request),
        "status": status,
    }
    return staff_render(
        request,
        "staff/contributions.html",
        context,
        "moderation",
        "staff/partials/contribution_results.html",
    )


def _diff_rows(c: Contribution) -> list[dict[str, Any]]:
    current = c.product.current_revision if c.product else None
    base = c.base_revision
    proposed = c.proposed or {}
    rows = []
    for name in DATA_FIELDS:
        base_value = base.data.get(name) if base else None
        current_value = current.data.get(name) if current else None
        changed = bool(base and current and base_value != current_value)
        if name not in proposed and not changed:
            continue
        rows.append(
            {
                "field": name,
                "proposed": name in proposed,
                "base": pretty(base_value),
                "current": pretty(current_value),
                "new": pretty(proposed.get(name)) if name in proposed else "",
                "changed_since_base": changed,
                "conflict": changed and name in proposed,
            }
        )
    return rows


@staff_view(access.PERM_VIEW_CONTRIBUTIONS)
def contribution_detail(request: HttpRequest, pk) -> HttpResponse:
    c = get_object_or_404(
        Contribution.objects.exclude(status=Contribution.Status.DRAFT).select_related(
            "product__current_revision", "base_revision", "moderator", "resulting_revision"
        ),
        pk=pk,
    )
    can_moderate = request.user.has_perm(access.PERM_MODERATE)
    form = DecisionForm(request.POST or None)
    if request.method == "POST":
        access.require_perm(request.user, access.PERM_MODERATE)
        if form.is_valid():
            _decide(request, c, form.cleaned_data["decision"], form.cleaned_data["reason"])
        else:
            messages.error(request, "Choose approve, reject or re-base.")
        return redirect("staff:contribution", pk=c.pk)

    context = {
        "c": c,
        "rows": _diff_rows(c),
        "images": c.images.all().order_by("created_at"),
        "runs": OCRRun.objects.filter(image__contribution=c).select_related("image"),
        "form": form,
        "can_moderate": can_moderate,
        "base": c.base_revision,
        "current": c.product.current_revision if c.product else None,
        "audit": c.audit.select_related("actor"),
        "proposed_facts": ((c.proposed or {}).get("nutrition") or {}).get("facts") or [],
        "open": c.status in (Contribution.Status.SUBMITTED, Contribution.Status.CONFLICT),
    }
    return staff_render(request, "staff/contribution.html", context, "moderation")


def _decide(request: HttpRequest, c: Contribution, decision: str, reason: str) -> None:
    try:
        if decision == "approve":
            outcome = moderation.approve(c.pk, request.user, reason)
            if outcome.status == "conflict":
                messages.warning(
                    request,
                    "The product changed since this contribution was made, in: "
                    + ", ".join(outcome.conflicting_fields)
                    + ". Nothing was published. Review the differences, then re-base or reject.",
                )
            else:
                messages.success(request, "Approved and published.")
        elif decision == "reject":
            moderation.reject(c.pk, request.user, reason)
            messages.success(request, "Rejected.")
        else:
            moderation.rebase(c.pk, request.user, reason)
            messages.success(request, "Re-based onto the current revision. You can approve it now.")
    except moderation.ModerationError as exc:
        messages.error(request, str(exc))


# ---------- Review flags ----------


@staff_view(access.PERM_VIEW_FLAGS)
def flags(request: HttpRequest) -> HttpResponse:
    form = FlagFilterForm(request.GET or None)
    data = form.cleaned_data if form.is_valid() else {}
    status = data.get("status") or "open"
    qs = ReviewFlag.objects.select_related("product", "resolved_by")
    if status != "all":
        qs = qs.filter(status=status)
    if data.get("kind"):
        qs = qs.filter(kind=data["kind"])
    qs = qs.order_by("created_at" if status == "open" else "-created_at", "pk")
    context = {"form": form, "page": page_of(request, qs), "query": query_without_page(request)}
    return staff_render(
        request, "staff/flags.html", context, "flags", "staff/partials/flag_results.html"
    )


@staff_view(access.PERM_VIEW_FLAGS)
def flag_detail(request: HttpRequest, pk: int) -> HttpResponse:
    flag = get_object_or_404(
        ReviewFlag.objects.select_related("product__current_revision", "resolved_by"), pk=pk
    )
    if request.method == "POST":
        access.require_perm(request.user, access.PERM_MODERATE)
        form = FlagActionForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Unknown action.")
            return redirect("staff:flag", pk=flag.pk)
        action, note = form.cleaned_data["action"], form.cleaned_data["note"]
        try:
            if action == "accept_off":
                services.accept_off_value(flag.pk, request.user, note)
                messages.success(request, "The Open Food Facts value is now published.")
            else:
                services.close_flag(
                    flag.pk,
                    request.user,
                    action="flag_keep_local" if action == "keep" else "flag_close",
                    note=note,
                )
                messages.success(
                    request, "Kept the local value." if action == "keep" else "Flag closed."
                )
        except services.StaffError as exc:
            messages.error(request, str(exc))
        return redirect("staff:flag", pk=flag.pk)

    product = flag.product
    current = product.current_revision if product else None
    context = {
        "flag": flag,
        "product": product,
        "details": pretty(flag.details),
        "local_value": pretty(current.data.get(flag.field)) if current and flag.field else "",
        "off_value": (
            pretty((current.off_data or {}).get(flag.field))
            if current and current.off_data is not None and flag.field
            else ""
        ),
        "locked": bool(product and flag.field in product.locked_fields),
        "is_open": flag.status == ReviewFlag.Status.OPEN,
        "can_act": request.user.has_perm(access.PERM_MODERATE),
        "contribution_id": (flag.details or {}).get("contribution"),
        "other_product": (flag.details or {}).get("other_product"),
    }
    return staff_render(request, "staff/flag.html", context, "flags")


# ---------- Products ----------


@staff_view(access.PERM_VIEW_PRODUCTS)
def products(request: HttpRequest) -> HttpResponse:
    q = (request.GET.get("q") or "").strip()[:100]
    if q.isdigit():
        ids = ProductIdentifier.objects.filter(
            Q(normalized__endswith=q) | Q(off_code=q) | Q(raw_value=q)
        ).values("product_id")
        qs = Product.objects.filter(Q(off_code=q) | Q(pk__in=ids)).order_by("-updated_at", "pk")
    else:
        qs = search_products(SearchParams(q=q, sort="relevance" if q else "updated"))
    context = {"q": q, "page": page_of(request, qs), "query": query_without_page(request)}
    return staff_render(
        request, "staff/products.html", context, "products", "staff/partials/product_results.html"
    )


@staff_view(access.PERM_VIEW_PRODUCTS)
def product_detail(request: HttpRequest, pk) -> HttpResponse:
    product = get_object_or_404(Product.objects.select_related("current_revision"), pk=pk)
    context = {
        "product": product,
        "revisions": ProductRevision.objects.filter(product=product)
        .only("number", "source", "created_at", "contribution_id", "product_id")
        .order_by("-number")[:50],
        "identifiers": product.identifiers.all(),
        "flags": product.flags.filter(status=ReviewFlag.Status.OPEN),
        "contributions": product.contributions.exclude(status=Contribution.Status.DRAFT).order_by(
            "-submitted_at"
        )[:10],
        "can_refresh": request.user.has_perm(access.PERM_MODERATE) and bool(product.off_code),
    }
    return staff_render(request, "staff/product.html", context, "products")


@require_POST
@staff_view(access.PERM_MODERATE)
def product_refresh(request: HttpRequest, pk) -> HttpResponse:
    product = get_object_or_404(Product, pk=pk)
    try:
        if services.queue_refresh(product, request.user):
            messages.success(request, "Refresh from Open Food Facts queued.")
        else:
            messages.info(request, "A refresh for this product is already queued.")
    except services.StaffError as exc:
        messages.error(request, str(exc))
    return redirect("staff:product", pk=product.pk)


# ---------- Users ----------


def _users_queryset():
    from allauth.account.models import EmailAddress

    from myola.models import DiaryEntry, Note

    def count_of(model):
        return Coalesce(
            Subquery(
                model.objects.filter(user=OuterRef("pk"))
                .order_by()
                .values("user")
                .annotate(n=Count("pk"))
                .values("n"),
                output_field=IntegerField(),
            ),
            Value(0),
        )

    return (
        get_user_model()
        .objects.only(
            "id",
            "email",
            "username",
            "date_joined",
            "last_login",
            "is_active",
            "is_staff",
            "is_superuser",
        )
        .annotate(
            diary_count=count_of(DiaryEntry),
            notes_count=count_of(Note),
            email_verified=Exists(EmailAddress.objects.filter(user=OuterRef("pk"), verified=True)),
        )
    )


@staff_view(access.PERM_MANAGE_USERS)
def users(request: HttpRequest) -> HttpResponse:
    form = UserFilterForm(request.GET or None)
    data = form.cleaned_data if form.is_valid() else {}
    qs = _users_queryset()
    if data.get("q"):
        qs = qs.filter(email__icontains=data["q"].strip())
    status = data.get("status")
    if status == "active":
        qs = qs.filter(is_active=True)
    elif status == "inactive":
        qs = qs.filter(is_active=False)
    elif status == "unverified":
        qs = qs.filter(email_verified=False)
    elif status == "staff":
        qs = qs.filter(Q(is_staff=True) | Q(is_superuser=True))
    context = {
        "form": form,
        "page": page_of(request, qs.order_by("-date_joined", "pk")),
        "query": query_without_page(request),
    }
    return staff_render(
        request, "staff/users.html", context, "users", "staff/partials/user_results.html"
    )


def _user_row(request: HttpRequest, pk: int, note: str = "", error: str = "") -> HttpResponse:
    row = _users_queryset().get(pk=pk)
    return render(
        request,
        "staff/partials/user_row.html",
        {"u": row, "row_note": note, "row_error": error, "next": request.POST.get("next", "")},
    )


@require_POST
@staff_view(access.PERM_MANAGE_USERS)
def user_action(request: HttpRequest, pk: int, action: str) -> HttpResponse:
    target = get_object_or_404(get_user_model(), pk=pk)
    note = error = ""
    try:
        if action in ("activate", "deactivate"):
            services.set_active(target, request.user, action == "activate")
            note = "Account activated." if action == "activate" else "Account deactivated."
        elif action == "resend":
            email = services.resend_confirmation(request, target)
            note = f"Confirmation email sent to {email}."
        else:
            error = "Unknown action."
    except services.StaffError as exc:
        error = str(exc)
    if is_htmx(request):
        return _user_row(request, target.pk, note, error)
    if error:
        messages.error(request, error)
    else:
        messages.success(request, note)
    return redirect(safe_next(request, reverse("staff:users")))


@staff_view(access.PERM_MANAGE_USERS)
def user_delete(request: HttpRequest, pk: int) -> HttpResponse:
    target = get_object_or_404(_users_queryset(), pk=pk)
    form = DeleteUserForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            services.delete_user(target, request.user, form.cleaned_data["confirm"])
        except services.StaffError as exc:
            form.add_error("confirm", str(exc))
        else:
            messages.success(request, f"Account {target.email} deleted.")
            return redirect("staff:users")
    blocked = ""
    if target.is_superuser:
        blocked = "Superusers cannot be deleted from the staff panel."
    elif target.pk == request.user.pk:
        blocked = "You cannot delete your own account here."
    return staff_render(
        request, "staff/user_delete.html", {"u": target, "form": form, "blocked": blocked}, "users"
    )


# ---------- Settings ----------


@staff_view(access.PERM_MANAGE_SETTINGS)
def site_settings(request: HttpRequest) -> HttpResponse:
    obj = SiteSettings.load()
    before = {name: getattr(obj, name) for name in SiteSettingsForm.Meta.fields}
    form = SiteSettingsForm(request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        obj.updated_by = request.user
        obj.save()
        changes = {
            name: [str(before[name]), str(getattr(obj, name))]
            for name in SiteSettingsForm.Meta.fields
            if before[name] != getattr(obj, name)
        }
        if changes:
            services.log(request.user, "settings_update", "site settings", changes=changes)
            messages.success(request, "Settings saved.")
        else:
            messages.info(request, "Nothing changed.")
        return redirect("staff:settings")
    return staff_render(request, "staff/settings.html", {"form": form, "obj": obj}, "settings")


# ---------- Audit log ----------


@staff_view(access.PERM_VIEW_AUDIT)
def audit(request: HttpRequest) -> HttpResponse:
    action = (request.GET.get("action") or "").strip()[:60]
    qs = StaffAction.objects.select_related("actor")
    if action:
        qs = qs.filter(action=action)
    actions = StaffAction.objects.order_by("action").values_list("action", flat=True).distinct()
    context = {
        "page": page_of(request, qs),
        "query": query_without_page(request),
        "action": action,
        "actions": list(actions),
        "moderation": ModerationAudit.objects.select_related("actor", "contribution")[:PAGE_SIZE]
        if request.user.has_perm("contributions.view_moderationaudit")
        or request.user.has_perm(access.PERM_VIEW_CONTRIBUTIONS)
        else None,
    }
    return staff_render(
        request, "staff/audit.html", context, "audit", "staff/partials/audit_results.html"
    )
