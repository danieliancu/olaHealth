"""Account data rights: download everything stored for the signed-in user."""

from __future__ import annotations

import json

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.serializers.json import DjangoJSONEncoder
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.decorators.http import require_GET

from core.coordination import rate_limited
from myola import models as m


def _rows(qs, fields: list[str]) -> list[dict]:
    return list(qs.values(*fields))


def export_data(user) -> dict:
    """Every personal record of ``user`` (the account, profile, diary, lists, notes, chats)."""
    profile = m.Profile.objects.filter(user=user).values().first()
    if profile:
        profile.pop("id", None)
        profile.pop("user_id", None)
    data = {
        "format": "foodotrack-export-v1",
        "exported_at": timezone.now(),
        "account": {
            "email": user.email,
            "date_joined": user.date_joined,
            "last_login": user.last_login,
        },
        "profile": profile,
        "diary": _rows(
            m.DiaryEntry.objects.filter(user=user),
            [
                "id",
                "date",
                "meal",
                "product_id",
                "name",
                "brand",
                "amount",
                "unit",
                "basis_known",
                "portion",
                "kcal_100",
                "protein_100",
                "carbs_100",
                "fat_100",
                "created_at",
            ],
        ),
        "water": _rows(m.WaterEntry.objects.filter(user=user), ["date", "ml", "created_at"]),
        "activity": _rows(
            m.ActivityEntry.objects.filter(user=user), ["date", "kind", "minutes", "created_at"]
        ),
        "weights": _rows(m.WeightEntry.objects.filter(user=user), ["date", "kg"]),
        "product_marks": _rows(
            m.ProductMark.objects.filter(user=user),
            ["product_id", "product__name", "kind", "created_at"],
        ),
        "product_visits": _rows(
            m.ProductVisit.objects.filter(user=user),
            ["product_id", "product__name", "code", "created_at"],
        ),
        "my_products": _rows(
            m.MyProduct.objects.filter(user=user),
            [
                "product_id",
                "product__name",
                "first_at",
                "last_at",
                "last_origin",
                "scanned",
                "opened",
                "saved",
                "eaten",
            ],
        ),
        "notes": _rows(
            m.Note.objects.filter(user=user),
            ["kind", "text", "product_id", "created_at", "updated_at"],
        ),
        "memories": _rows(
            m.Memory.objects.filter(user=user),
            ["text", "source", "suggestion_key", "active", "confirmed_at", "created_at"],
        ),
        "chat_messages": _rows(
            m.ChatMessage.objects.filter(user=user), ["context", "role", "text", "created_at"]
        ),
        "notifications": _rows(
            m.Notification.objects.filter(user=user),
            ["kind", "title", "body", "url", "created_at", "read_at"],
        ),
    }
    try:
        from ai.models import AIUsage

        data["ai_usage"] = _rows(
            AIUsage.objects.filter(user=user), ["day", "messages", "input_tokens", "output_tokens"]
        )
    except (ImportError, LookupError):
        pass
    return data


@login_required
@require_GET
@rate_limited("personal_write")
def download_my_data(request: HttpRequest) -> HttpResponse:
    body = json.dumps(export_data(request.user), cls=DjangoJSONEncoder, indent=2, default=str)
    response = HttpResponse(body, content_type="application/json; charset=utf-8")
    stamp = timezone.now().strftime("%Y%m%d")
    response["Content-Disposition"] = f'attachment; filename="foodotrack-data-{stamp}.json"'
    response["Cache-Control"] = "no-store"
    return response


class NameForm(forms.Form):
    first_name = forms.CharField(
        label=gettext_lazy("Your name"),
        max_length=30,
        required=False,
        help_text=gettext_lazy("Used only to greet you, for example “Good afternoon, Ana”."),
        widget=forms.TextInput(attrs={"autocomplete": "given-name", "maxlength": 30}),
    )


@login_required
@rate_limited("personal_write")
def account_name(request: HttpRequest) -> HttpResponse:
    """The name Foodo greets you with (optional; empty removes it)."""
    form = NameForm(request.POST or None, initial={"first_name": request.user.first_name})
    if request.method == "POST" and form.is_valid():
        request.user.first_name = " ".join(form.cleaned_data["first_name"].split())
        request.user.save(update_fields=["first_name"])
        messages.success(request, _("Name saved."))
        return redirect(f"{reverse('ask-foodo')}?tab=account")
    return render(request, "myola/account_name.html", {"form": form, "nav_active": "ask-foodo"})
