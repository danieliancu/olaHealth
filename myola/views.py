from __future__ import annotations

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from myola.preferences import picker_context


@require_GET
def my_ola(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "myola/my_ola.html",
        {"picker": picker_context(), "nav_active": "my-ola"},
    )
