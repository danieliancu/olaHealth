from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_http_methods

from myola.forms import DeleteAccountForm
from myola.preferences import picker_context


def _page(request: HttpRequest, template: str, nav: str, **extra) -> HttpResponse:
    return render(request, template, {"picker": picker_context(), "nav_active": nav, **extra})


@login_required
@require_GET
def my_ola(request: HttpRequest) -> HttpResponse:
    return _page(request, "myola/my_ola.html", "my-ola")


@login_required
@require_GET
def start(request: HttpRequest) -> HttpResponse:
    """Onboarding: four short steps."""
    return _page(request, "myola/start.html", "")


@login_required
@require_GET
def today(request: HttpRequest) -> HttpResponse:
    return _page(request, "myola/today.html", "today")


@login_required
@require_GET
def progress(request: HttpRequest) -> HttpResponse:
    return _page(request, "myola/progress.html", "progress")


@login_required
@require_http_methods(["GET", "POST"])
def delete_account(request: HttpRequest) -> HttpResponse:
    """Delete the account and everything attached to it, after the password is confirmed."""
    form = DeleteAccountForm(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = request.user
        logout(request)
        user.delete()
        messages.success(request, _("Your account and all its data have been deleted."))
        return redirect("home")
    return render(request, "myola/delete_account.html", {"form": form, "nav_active": "my-ola"})


@login_required
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
