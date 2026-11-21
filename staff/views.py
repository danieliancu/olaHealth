"""Staff panel. Server-rendered; htmx only swaps list results and user rows.

Every page works without JavaScript (forms POST and redirect). See ``staff.access`` for
the permission each section needs.
"""

from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal
from typing import Any

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.db.models import (
    Count,
    DecimalField,
    Exists,
    IntegerField,
    OuterRef,
    Q,
    Subquery,
    Sum,
    Value,
)
from django.db.models.functions import Coalesce
from django.http import HttpRequest, HttpResponse, QueryDict
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST

from catalog.models import Product, ProductIdentifier, ProductRevision, ReviewFlag
from catalog.services.search import SearchParams, search_products
from contributions.models import Contribution, ModerationAudit, OCRRun
from contributions.services import moderation
from off.mapping import DATA_FIELDS
from staff import access, services
from staff.access import staff_view
from staff.forms import (
    AnnouncementForm,
    ContributionFilterForm,
    DecisionForm,
    DeleteUserForm,
    FlagActionForm,
    FlagFilterForm,
    LearnArticleForm,
    LearnTopicForm,
    SiteSettingsForm,
    UserFilterForm,
)
from staff.models import Announcement, SiteSettings, StaffAction

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

    from ai.models import AIUsage
    from myola.models import DiaryEntry, Note

    month_start = timezone.localdate().replace(day=1)

    def ai_spend(since):
        rows = AIUsage.objects.filter(user=OuterRef("pk"))
        if since is not None:
            rows = rows.filter(day__gte=since)
        money = DecimalField(max_digits=12, decimal_places=6)
        return Coalesce(
            Subquery(
                rows.order_by().values("user").annotate(s=Sum("cost_usd")).values("s"),
                output_field=money,
            ),
            Value(Decimal(0), output_field=money),
        )

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
            # AI spend, billed in US dollars (shown in pounds in the table).
            ai_usd_month=ai_spend(month_start),
            ai_usd_total=ai_spend(None),
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
    scope = ("ai_send_ingredients", "ai_general_questions")
    context = {
        "form": form,
        "obj": obj,
        "main_fields": [f for f in form.visible_fields() if f.name not in scope],
        "scope_fields": [form[name] for name in scope],
        "answer_map": _answer_map(obj),
    }
    return staff_render(request, "staff/settings.html", context, "settings")


def _answer_map(site) -> list[dict]:
    """What Ask Foodo answers, where and how, with the saved settings (Settings, right column)."""
    ai = site.ai_enabled
    rules = {"label": "Rules", "tone": "rules", "note": "Always the app's own answer. £0."}
    return [
        {
            "where": "Product page",
            "what": "Label questions: values, sugar, salt, Nutri-Score, NOVA, the Foodo score",
            "how": {"label": "AI", "tone": "ai", "note": "From this product's facts. ~£0.0001."}
            if ai
            else {"label": "Rules", "tone": "rules", "note": "AI is off: the app's own answer."},
        },
        {
            "where": "Product page",
            "what": "Ingredients and additives (“What is in it?”, “What is E471?”)",
            "how": {"label": "AI", "tone": "ai", "note": "The ingredient list is sent too."}
            if ai and site.ai_send_ingredients
            else {"label": "Not known", "tone": "off", "note": "Switch on the option below."},
        },
        {
            "where": "Anywhere",
            "what": "Allergies and diets (“Is it gluten-free?”, “Are alergeni?”)",
            "how": {**rules, "note": "Never sent to the AI, in any language."},
        },
        {
            "where": "General chat",
            "what": "Your lists, scans, memories and preferences",
            "how": {**rules, "note": "From your account. Never sent to the AI."},
        },
        {
            "where": "General chat",
            "what": "Find a product by name or barcode",
            "how": {
                "label": "Rules",
                "tone": "rules",
                "note": "Our catalogue, then Open Food Facts (free).",
            },
        },
        {
            "where": "General chat",
            "what": "General food questions (“Are carbohydrates good for you?”)",
            "how": {
                "label": "AI",
                "tone": "ai",
                "note": "Only the question is sent. General information, not advice. ~£0.0001.",
            }
            if ai and site.ai_general_questions
            else {"label": "Not answered", "tone": "off", "note": "“I can't answer that yet”."},
        },
        {
            "where": "General chat",
            "what": "Personal health (conditions, medication, pregnancy)",
            "how": {
                "label": "AI, limited",
                "tone": "warn",
                "note": "One general sentence, then GP, pharmacist or NHS 111.",
            }
            if ai and site.ai_general_questions
            else {"label": "Not answered", "tone": "off", "note": "“I can't answer that yet”."},
        },
    ]


@staff_view(access.PERM_MANAGE_SETTINGS)
@require_GET
def ai_models(request: HttpRequest) -> HttpResponse:
    """The Model menu for the provider just picked in Settings (htmx)."""
    from staff.forms import model_choices, models_note

    provider = request.GET.get("ai_provider", "")
    site = SiteSettings.load()
    current = site.ai_model if provider == site.ai_provider else ""
    return render(
        request,
        "staff/partials/ai_model_select.html",
        {
            "choices": model_choices(provider, current),
            "current": current,
            "note": models_note(provider),
        },
    )


# ---------- Announcements (in-app notifications to every opted-in user) ----------


@staff_view(access.PERM_MANAGE_SETTINGS)
def announcements(request: HttpRequest) -> HttpResponse:
    form = AnnouncementForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        a = form.save(commit=False)
        a.created_by = request.user
        a.save()
        from django_q.tasks import async_task

        async_task("myola.tasks.fan_out_announcement", a.pk)
        services.log(request.user, "announcement_send", a.title, announcement=a.pk)
        messages.success(
            request, "Announcement sent. It appears in everyone's notifications shortly."
        )
        return redirect("staff:announcements")
    recent = Announcement.objects.select_related("created_by")[:20]
    return staff_render(
        request, "staff/announcements.html", {"form": form, "recent": recent}, "announcements"
    )


# ---------- Learn CMS (articles and topics) ----------


@staff_view(access.PERM_MANAGE_LEARN)
def learn_articles(request: HttpRequest) -> HttpResponse:
    from core.models import LearnArticle, LearnTopic

    q = (request.GET.get("q") or "").strip()[:80]
    status = request.GET.get("status", "")
    topic = request.GET.get("topic", "")
    qs = (
        LearnArticle.objects.select_related("topic")
        .annotate(n_products=Count("products", distinct=True))
        .order_by("topic__order", "order", "title")
    )
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(summary__icontains=q) | Q(slug__icontains=q))
    if status == "published":
        qs = qs.filter(published=True)
    elif status == "draft":
        qs = qs.filter(published=False)
    if topic:
        qs = qs.filter(topic__key=topic)
    ctx = {
        "articles": qs,
        "topics": LearnTopic.objects.all(),
        "q": q,
        "status": status,
        "topic": topic,
    }
    return staff_render(request, "staff/learn_articles.html", ctx, "learn")


@staff_view(access.PERM_MANAGE_LEARN)
def learn_article_edit(request: HttpRequest, pk: int | None = None) -> HttpResponse:
    from core.models import LearnArticle

    article = get_object_or_404(LearnArticle, pk=pk) if pk else None
    form = LearnArticleForm(request.POST or None, request.FILES or None, instance=article)
    if request.method == "POST" and form.is_valid():
        saved = form.save()
        services.log(
            request.user, "learn_save", saved.title, article=saved.pk, created=article is None
        )
        msg = "Article saved."
        if saved.published and saved.notified_at is None:
            from django.utils import timezone
            from django_q.tasks import async_task

            if saved.published_at <= timezone.now():
                async_task("myola.tasks.notify_new_articles")
                msg = "Article published. Its title appears in everyone's notifications shortly."
            else:
                msg = "Article saved. Notifications go out on its publication date."
        messages.success(request, msg)
        return redirect("staff:learn-article", pk=saved.pk)
    from core.markup import render as render_markup

    body = form["body"].value() or ""
    ctx = {"form": form, "article": article, "body_html": render_markup(body)}
    if article:
        ctx["lp_attached"] = list(article.products.all())
    return staff_render(request, "staff/learn_article.html", ctx, "learn")


@staff_view(access.PERM_MANAGE_LEARN)
@require_POST
def learn_article_delete(request: HttpRequest, pk: int) -> HttpResponse:
    from core.models import LearnArticle

    article = get_object_or_404(LearnArticle, pk=pk)
    title = article.title
    if article.image:
        article.image.delete(save=False)
    article.delete()
    services.log(request.user, "learn_delete", title)
    messages.success(request, "Article deleted.")
    return redirect("staff:learn")


def _learn_products_ctx(article, q: str = "", source: str = "local", results=None, error=""):
    return {
        "article": article,
        "attached": list(article.products.all()),
        "q": q,
        "source": source,
        "results": results,
        "error": error,
    }


@staff_view(access.PERM_MANAGE_LEARN)
def learn_products_search(request: HttpRequest, pk: int) -> HttpResponse:
    """Find products to link: our catalogue (default) or Open Food Facts (by name)."""
    from core.models import LearnArticle

    article = get_object_or_404(LearnArticle, pk=pk)
    q = (request.GET.get("q") or "").strip()[:100]
    source = "off" if request.GET.get("source") == "off" else "local"
    results, error = [], ""
    if len(q) >= 2:
        if source == "local":
            attached = list(article.products.values_list("pk", flat=True))
            found = search_products(SearchParams(q=q)).exclude(pk__in=attached)[:10]
            results = [
                {"pk": p.pk, "name": p.name, "brand": p.brand, "image": p.front_image_url}
                for p in found
            ]
        else:
            from off.client import OFFError, default_client

            try:
                rows = default_client().search_text(q, page_size=10)
            except OFFError as exc:
                rows = []
                error = "Open Food Facts is not answering right now. Try again in a minute."
                if exc.kind == "rate_limited":
                    error = "Too many searches on Open Food Facts. Wait a minute."
            results = [
                {
                    "code": r.get("code", ""),
                    "name": r.get("product_name") or "Unnamed product",
                    "brand": r.get("brands", ""),
                    "image": r.get("image_front_small_url", ""),
                }
                for r in rows
            ]
    ctx = _learn_products_ctx(article, q, source, results, error)
    return render(request, "staff/partials/learn_products.html", ctx)


@staff_view(access.PERM_MANAGE_LEARN)
@require_POST
def learn_products_add(request: HttpRequest, pk: int) -> HttpResponse:
    """Link a product: one from our catalogue (id) or one imported from OFF (barcode)."""
    from catalog.services.lookup import lookup
    from core.models import LearnArticle

    article = get_object_or_404(LearnArticle, pk=pk)
    error = ""
    product = None
    if request.POST.get("product"):
        product = Product.objects.filter(pk=request.POST["product"]).first()
    elif request.POST.get("code"):
        product = lookup(request.POST["code"], schedule=False).product
        if product is None:
            error = "This product could not be imported from Open Food Facts."
    if product is not None:
        article.products.add(product)
        services.log(request.user, "learn_product_add", article.title, product=str(product.pk))
    ctx = _learn_products_ctx(article, error=error)
    return render(request, "staff/partials/learn_products.html", ctx)


@staff_view(access.PERM_MANAGE_LEARN)
@require_POST
def learn_products_remove(request: HttpRequest, pk: int, product_pk) -> HttpResponse:
    from core.models import LearnArticle

    article = get_object_or_404(LearnArticle, pk=pk)
    article.products.remove(product_pk)
    services.log(request.user, "learn_product_remove", article.title, product=str(product_pk))
    return render(request, "staff/partials/learn_products.html", _learn_products_ctx(article))


@staff_view(access.PERM_MANAGE_LEARN)
def learn_topics(request: HttpRequest, pk: int | None = None) -> HttpResponse:
    from core.models import LearnTopic

    topic = get_object_or_404(LearnTopic, pk=pk) if pk else None
    form = LearnTopicForm(request.POST or None, instance=topic)
    if request.method == "POST" and form.is_valid():
        saved = form.save()
        services.log(request.user, "learn_topic_save", saved.title, topic=saved.pk)
        messages.success(request, "Topic saved.")
        return redirect("staff:learn-topics")
    topics = LearnTopic.objects.annotate(n=Count("articles"))
    ctx = {"form": form, "topic": topic, "topics": topics}
    return staff_render(request, "staff/learn_topics.html", ctx, "learn")


# ---------- AI answers ----------


@staff_view(access.PERM_MANAGE_SETTINGS)
@require_GET
def ai_answers(request: HttpRequest) -> HttpResponse:
    """What Ask Foodo's AI answered in the last 30 days, without the users' identity: for
    checking quality, safety blocks and the helpful / not helpful ratings."""
    from ai.models import AIAnswer

    kind = request.GET.get("kind", "")
    outcome = request.GET.get("outcome", "")
    rating = request.GET.get("rating", "")
    qs = AIAnswer.objects.only(
        "created_at", "kind", "question", "answer", "outcome", "reason", "model", "cost_usd",
        "rating", "input_tokens", "output_tokens",
    )  # fmt: skip
    if kind in AIAnswer.Kind.values:
        qs = qs.filter(kind=kind)
    if outcome in AIAnswer.Outcome.values:
        qs = qs.filter(outcome=outcome)
    if rating in ("up", "down", "none"):
        qs = qs.filter(rating={"up": 1, "down": -1, "none": None}[rating])
    totals = AIAnswer.objects.aggregate(
        total=Count("pk"),
        answered=Count("pk", filter=Q(outcome=AIAnswer.Outcome.ANSWERED)),
        blocked=Count("pk", filter=Q(outcome=AIAnswer.Outcome.BLOCKED)),
        routed=Count("pk", filter=Q(outcome=AIAnswer.Outcome.ROUTED)),
        failed=Count("pk", filter=Q(outcome=AIAnswer.Outcome.FAILED)),
        up=Count("pk", filter=Q(rating=1)),
        down=Count("pk", filter=Q(rating=-1)),
        cost=Coalesce(Sum("cost_usd"), Value(Decimal(0)), output_field=DecimalField()),
    )
    context = {
        "page": page_of(request, qs),
        "query": query_without_page(request),
        "kind": kind,
        "outcome": outcome,
        "rating": rating,
        "kinds": AIAnswer.Kind.choices,
        "outcomes": AIAnswer.Outcome.choices,
        "totals": totals,
        "retention_days": AIAnswer.RETENTION_DAYS,
    }
    return staff_render(
        request,
        "staff/ai_answers.html",
        context,
        "ai-answers",
        "staff/partials/ai_answer_results.html",
    )


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
