from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from myola.forms import DeleteAccountForm


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
    return render(request, "myola/delete_account.html", {"form": form, "nav_active": "ask-foodo"})
