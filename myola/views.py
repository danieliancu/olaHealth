from __future__ import annotations

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from myola.preferences import picker_context


def _page(request: HttpRequest, template: str, nav: str) -> HttpResponse:
    return render(request, template, {"picker": picker_context(), "nav_active": nav})


@require_GET
def my_ola(request: HttpRequest) -> HttpResponse:
    return _page(request, "myola/my_ola.html", "my-ola")


@require_GET
def start(request: HttpRequest) -> HttpResponse:
    """Onboarding: four short steps, answers kept in this browser only."""
    return _page(request, "myola/start.html", "")


@require_GET
def today(request: HttpRequest) -> HttpResponse:
    return _page(request, "myola/today.html", "today")


@require_GET
def progress(request: HttpRequest) -> HttpResponse:
    return _page(request, "myola/progress.html", "progress")


@require_GET
def add_food(request: HttpRequest, pk) -> HttpResponse:
    """The short “how much?” step reached from the Food diary (scan or search)."""
    from django.shortcuts import get_object_or_404

    from catalog.models import Product
    from myola.page import facts_from_product, ola_payload

    product = get_object_or_404(
        Product.objects.select_related("current_revision"), pk=pk, current_revision__isnull=False
    )
    snapshots = list(product.current_revision.assessments.all())
    meal = request.GET.get("meal", "")
    date = request.GET.get("date", "")
    return render(
        request,
        "myola/add.html",
        {
            "product": product,
            "data": product.current_revision.data,
            "ola": ola_payload(facts_from_product(product, snapshots)),
            "meal": meal if meal in ("breakfast", "lunch", "dinner", "snacks") else "",
            "date": date if len(date) == 10 else "",
            "scanned": request.GET.get("scanned", "")[:32],
            "nav_active": "today",
        },
    )
