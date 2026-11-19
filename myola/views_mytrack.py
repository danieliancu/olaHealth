"""My Track: overview, profile summary, notes and memories, saved lists, account, Ask Foodo.

Every page works as plain HTML forms (POST, then redirect); htmx only swaps the part that
changed (``HX-Request``), with the confirmation messages swapped out of band.
"""

from __future__ import annotations

import datetime as dt
import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme, urlencode
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from catalog.models import Product
from core.coordination import rate_limited
from myola import preferences as P
from myola.forms_mytrack import NOTE_KIND_LABELS, ChatForm, MemoryForm, NoteForm
from myola.models import LIMITS, DiaryEntry, Memory, Note, ProductMark, ProductVisit
from myola.services import chat, lists
from myola.services import memories as memory_service
from myola.services.profiles import derive_prefs, get_profile

TABS = ("overview", "profile", "notes", "saved", "account")
TAB_LABELS = {
    "overview": gettext_lazy("Overview"),
    "profile": gettext_lazy("Profile"),
    "notes": gettext_lazy("Notes"),
    "saved": gettext_lazy("Saved"),
    "account": gettext_lazy("Account"),
}
SAVED_KINDS = {
    "buy_often": ProductMark.Kind.BUY_OFTEN,
    "avoid": ProductMark.Kind.AVOID,
}


# ---------- Helpers ----------
def _hx(request: HttpRequest) -> bool:
    return bool(request.headers.get("HX-Request"))


def _tab_url(tab: str, anchor: str = "") -> str:
    url = f"{reverse('my-ola')}?tab={tab}"
    return f"{url}#{anchor}" if anchor else url


def _next_url(request: HttpRequest, default: str) -> str:
    nxt = request.POST.get("next") or request.GET.get("next") or ""
    if nxt and url_has_allowed_host_and_scheme(
        nxt, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return nxt
    return default


def _fragment(request: HttpRequest, template: str, context: dict, status: int = 200):
    """A partial plus the pending messages, swapped out of band into the status region."""
    html = render_to_string(template, context, request=request)
    html += render_to_string("myola/partials/mytrack_messages.html", {"oob": True}, request)
    return HttpResponse(html, status=status)


def _hx_redirect(url: str) -> HttpResponse:
    return HttpResponse(status=204, headers={"HX-Redirect": url})


def _product_row(product) -> dict:
    return {
        "id": product.pk,
        "name": product.name or _("Unnamed product"),
        "brand": product.brand,
        "quantity": product.quantity,
        "image": product.front_image_url,
        "url": reverse("product", args=[product.pk]),
    }


# ---------- Context builders ----------
def _prefs_chips(profile) -> list[str]:
    prefs = derive_prefs(profile)
    chips = [str(P.GOALS[profile.goal][0])] if profile.goal in P.GOALS else []
    chips += [str(P.DIETS[d][0]) for d in prefs["diets"]]
    chips += [_("Allergy: %(name)s") % {"name": P.ALLERGENS[a]} for a in prefs["allergens"]]
    return chips


def _overview(user, profile) -> dict:
    scans = lists.recent(user, scans_only=True, limit=3)
    all_scans = ProductVisit.objects.filter(user=user).exclude(code="")
    week = all_scans.filter(created_at__gte=timezone.now() - dt.timedelta(days=7)).count()
    marks = dict.fromkeys(ProductMark.Kind.values, 0)
    for kind in ProductMark.objects.filter(user=user).values_list("kind", flat=True):
        marks[kind] += 1
    notes = Note.objects.filter(user=user).count()
    return {
        "chips": _prefs_chips(profile),
        "recent_scans": [{**_product_row(v.product), "at": v.created_at} for v in scans],
        "scans_total": all_scans.count(),
        "scans_week": week,
        "counts": [
            (_("Days logged in the diary"), DiaryEntry.objects.filter(user=user)
             .values("date").distinct().count(), reverse("progress")),
            (_("Scans"), all_scans.count(), _tab_url("saved")),
            (_("Favourites"), marks[ProductMark.Kind.FAVORITE], reverse("saved")),
            (_("Buy often"), marks[ProductMark.Kind.BUY_OFTEN], _tab_url("saved")),
            (_("Avoid"), marks[ProductMark.Kind.AVOID], _tab_url("saved")),
            (_("Notes"), notes, _tab_url("notes")),
            (_("Memories"), memory_service.used_in_replies(user).count(),
             _tab_url("notes", "memories")),
        ],
    }  # fmt: skip


SUB_ICONS = {
    "en:peanuts": "nut",
    "en:nuts": "nut",
    "en:fish": "fish",
    "en:crustaceans": "shell",
    "en:molluscs": "shell",
}
TARGET_ICONS = {"energy": "flame", "protein": "egg", "carbs": "wheat", "fat": "droplet"}


def _num(value) -> str:
    """178.0 -> "178", 72.5 -> "72.5"."""
    return f"{value:f}".rstrip("0").rstrip(".") if "." in f"{value:f}" else f"{value:f}"


def _profile_summary(profile) -> dict:
    """The profile tab: every item carries its icon (names from core/templatetags/ui.py)."""
    restrictions = []
    for k in profile.restrictions:
        if k not in P.RESTRICTIONS:
            continue
        label, icon = str(P.RESTRICTIONS[k][0]), P.RESTRICTIONS[k][1]
        subs = []
        if k == "nuts":
            subs = [
                (str(P.NUT_TYPES[t]), SUB_ICONS[t]) for t in profile.nut_types if t in P.NUT_TYPES
            ]
        elif k == "seafood":
            subs = [
                (str(P.SEAFOOD_TYPES[t]), SUB_ICONS[t])
                for t in profile.seafood_types
                if t in P.SEAFOOD_TYPES
            ]
        restrictions.append({"key": k, "label": label, "icon": icon, "subs": subs})
    about = []
    if profile.gender in P.GENDERS:
        about.append((_("Gender"), str(P.GENDERS[profile.gender]), "user-round"))
    if profile.age:
        about.append((_("Age"), _("%(n)s years") % {"n": profile.age}, "calendar-days"))
    if profile.height:
        about.append((_("Height"), f"{_num(profile.height)} cm", "person-standing"))
    # Weight stays hidden when an eating-disorder answer turned weight tracking off.
    if profile.weight and (profile.track_weight or not profile.eating_disorder):
        about.append((_("Weight"), f"{_num(profile.weight)} kg", "scale"))
    consented = profile.health_consent_at is not None
    targets = [
        (label, value, unit, TARGET_ICONS[key])
        for key, label, value, unit in (
            ("energy", _("Energy"), profile.target_kcal if profile.show_energy else None, "kcal"),
            ("protein", _("Protein"), profile.target_protein, "g"),
            ("carbs", _("Carbs"), profile.target_carbs, "g"),
            ("fat", _("Fat"), profile.target_fat, "g"),
        )
        if value
    ]
    goal = P.GOALS.get(profile.goal)
    return {
        "goal": str(goal[0]) if goal else "",
        "goal_icon": goal[1] if goal else "apple",
        "about": about,
        "restrictions": restrictions,
        "religious_note": profile.religious_note if "religious" in profile.restrictions else "",
        "other_note": profile.other_note if "other" in profile.restrictions else "",
        "consented": consented,
        "conditions": [str(P.CONDITIONS[c]) for c in profile.conditions if c in P.CONDITIONS]
        if consented
        else [],
        "conditions_note": profile.conditions_note if consented else "",
        "targets": targets,
    }


def _known_products(user, extra=None) -> list[dict]:
    """Products the user has met: marks (not hidden) and recent visits (plus ``extra``)."""
    seen: dict = {extra.pk: extra} if extra is not None else {}
    marks = (
        ProductMark.objects.filter(user=user)
        .exclude(kind=ProductMark.Kind.HIDDEN)
        .select_related("product")
    )
    for m in marks:
        seen.setdefault(m.product_id, m.product)
    for v in lists.recent(user, limit=50):
        seen.setdefault(v.product_id, v.product)
    return [_product_row(p) for p in seen.values()]


def _notes(user, q: str = ""):
    qs = Note.objects.filter(user=user).select_related("product")
    q = (q or "").strip()
    if q:
        qs = qs.filter(Q(text__icontains=q) | Q(product__name__icontains=q))
    return qs


def _notes_list_context(user, q: str = "") -> dict:
    return {"notes": _notes(user, q), "q": (q or "").strip(), "kind_labels": NOTE_KIND_LABELS}


def _edit_form(note: Note) -> NoteForm:
    return NoteForm(initial={"text": note.text, "kind": note.kind, "product": note.product_id})


def _notes_context(user, *, form=None, q: str = "", editing=None) -> dict:
    return {
        "note_form": form or NoteForm(),
        "known_products": _known_products(user, editing.product if editing else None),
        "kind_labels": NOTE_KIND_LABELS,
        "editing": editing,
        "edit_form": _edit_form(editing) if editing else None,
        **_notes_list_context(user, q),
    }


def _memories_context(user, *, form=None, editing=None, profile=None) -> dict:
    profile = profile or get_profile(user)
    return {
        "memories": Memory.objects.filter(user=user),
        "suggestions": memory_service.suggestions(user, profile),
        "memory_form": form or MemoryForm(),
        "editing_memory": editing,
        "use_notes": profile.use_notes,
    }


def _saved_list(user, kind: str) -> dict:
    if kind == "scan":
        rows = [
            {**_product_row(v.product), "at": v.created_at, "code": v.code}
            for v in lists.recent(user, scans_only=True, limit=LIMITS["visits"])
        ]
    else:
        marks = ProductMark.objects.filter(user=user, kind=SAVED_KINDS[kind]).select_related(
            "product"
        )
        rows = [_product_row(m.product) for m in marks]
    return {"kind": kind, "rows": rows}


# ---------- Page ----------
@login_required
@require_GET
def my_ola(request: HttpRequest) -> HttpResponse:
    tab = request.GET.get("tab", "overview")
    if tab not in TABS:
        tab = "overview"
    user, profile = request.user, get_profile(request.user)
    ctx: dict = {"tab": tab, "tabs": list(TAB_LABELS.items()), "nav_active": "my-ola"}
    if tab == "overview":
        ctx.update(_overview(user, profile))
    elif tab == "profile":
        ctx["summary"] = _profile_summary(profile)
        ctx["profile"] = profile
    elif tab == "notes":
        editing = None
        if request.GET.get("edit", "").isdigit():
            editing = Note.objects.filter(user=user, pk=int(request.GET["edit"])).first()
        memory_edit = None
        if request.GET.get("memory", "").isdigit():
            memory_edit = Memory.objects.filter(user=user, pk=int(request.GET["memory"])).first()
        ctx.update(_notes_context(user, q=request.GET.get("q", ""), editing=editing))
        ctx.update(_memories_context(user, editing=memory_edit, profile=profile))
    elif tab == "saved":
        ctx["saved"] = [_saved_list(user, k) for k in ("buy_often", "avoid", "scan")]
    return render(request, "myola/my_ola.html", ctx)


# ---------- Notes ----------
@login_required
@require_GET
def notes_search(request: HttpRequest) -> HttpResponse:
    q = request.GET.get("q", "")[:200]
    if not _hx(request):
        return redirect(f"{reverse('my-ola')}?{urlencode({'tab': 'notes', 'q': q})}")
    return render(
        request, "myola/partials/mytrack_notes_list.html", _notes_list_context(request.user, q)
    )


@login_required
@require_POST
def note_create(request: HttpRequest) -> HttpResponse:
    form = NoteForm(request.POST)
    default = _tab_url("notes")
    if form.is_valid():
        if Note.objects.filter(user=request.user).count() >= LIMITS["notes"]:
            messages.error(
                request,
                _("You have %(n)s notes, the most you can keep. Delete one to add another.")
                % {"n": LIMITS["notes"]},
            )
        else:
            Note.objects.create(user=request.user, **form.cleaned_data)
            messages.success(request, _("Note saved."))
            form = None
    elif not _hx(request) and request.POST.get("next"):
        messages.error(request, _("The note was not saved: write some text first."))
        return redirect(_next_url(request, default))
    if _hx(request):
        return _fragment(
            request,
            "myola/partials/mytrack_notes.html",
            _notes_context(request.user, form=form, q=request.POST.get("q", "")),
            status=200,
        )
    if form is not None and form.errors:
        ctx = {"tab": "notes", "tabs": list(TAB_LABELS.items()), "nav_active": "my-ola"}
        ctx.update(_notes_context(request.user, form=form))
        ctx.update(_memories_context(request.user))
        return render(request, "myola/my_ola.html", ctx, status=400)
    return redirect(_next_url(request, default))


def _own_note(request, pk: int) -> Note:
    return get_object_or_404(Note.objects.select_related("product"), pk=pk, user=request.user)


@login_required
@require_GET
def note_item(request: HttpRequest, pk: int) -> HttpResponse:
    note = _own_note(request, pk)
    if not _hx(request):
        return redirect(_tab_url("notes", f"note-{pk}"))
    return render(
        request,
        "myola/partials/mytrack_note_item.html",
        {"note": note, "kind_labels": NOTE_KIND_LABELS},
    )


@login_required
@require_http_methods(["GET", "POST"])
def note_edit(request: HttpRequest, pk: int) -> HttpResponse:
    note = _own_note(request, pk)
    if request.method == "GET":
        if not _hx(request):
            return redirect(f"{_tab_url('notes')}&edit={pk}#note-{pk}")
        return render(
            request,
            "myola/partials/mytrack_note_edit.html",
            {
                "note": note,
                "form": _edit_form(note),
                "known_products": _known_products(request.user, note.product),
            },
        )
    form = NoteForm(request.POST)
    if form.is_valid():
        note.text = form.cleaned_data["text"]
        note.kind = form.cleaned_data["kind"]
        note.product = form.cleaned_data["product"]
        note.save()
        messages.success(request, _("Note updated."))
        if _hx(request):
            return _fragment(
                request,
                "myola/partials/mytrack_note_item.html",
                {"note": note, "kind_labels": NOTE_KIND_LABELS},
            )
        return redirect(_tab_url("notes", f"note-{pk}"))
    if _hx(request):
        return render(
            request,
            "myola/partials/mytrack_note_edit.html",
            {
                "note": note,
                "form": form,
                "known_products": _known_products(request.user, note.product),
            },
        )
    messages.error(request, _("The note was not saved: write some text first."))
    return redirect(f"{_tab_url('notes')}&edit={pk}#note-{pk}")


@login_required
@require_POST
def note_delete(request: HttpRequest, pk: int) -> HttpResponse:
    _own_note(request, pk).delete()
    messages.success(request, _("Note deleted."))
    if _hx(request):
        return _fragment(
            request,
            "myola/partials/mytrack_notes_list.html",
            _notes_list_context(request.user, request.POST.get("q", "")),
        )
    return redirect(_tab_url("notes"))


# ---------- Memories ----------
def _memories_response(request, *, form=None, editing=None, status: int = 200) -> HttpResponse:
    if _hx(request):
        return _fragment(
            request,
            "myola/partials/mytrack_memories.html",
            _memories_context(request.user, form=form, editing=editing),
            status=status,
        )
    return redirect(_tab_url("notes", "memories"))


@login_required
@require_POST
def memory_add(request: HttpRequest) -> HttpResponse:
    form = MemoryForm(request.POST)
    if form.is_valid():
        memory_service.add(request.user, form.cleaned_data["text"])
        messages.success(request, _("Foodo will remember this."))
        return _memories_response(request)
    if _hx(request):
        return _memories_response(request, form=form)
    for error in form.errors.get("text", []):
        messages.error(request, error)
    return _memories_response(request)


@login_required
@require_http_methods(["GET", "POST"])
def memory_edit(request: HttpRequest, pk: int) -> HttpResponse:
    memory = get_object_or_404(Memory, pk=pk, user=request.user)
    if request.method == "GET":
        if not _hx(request):
            return redirect(f"{_tab_url('notes')}&memory={pk}#memories")
        return _memories_response(request, editing=memory)
    form = MemoryForm(request.POST)
    if form.is_valid():
        memory_service.edit(memory, form.cleaned_data["text"])
        messages.success(request, _("Memory updated."))
    else:
        for error in form.errors.get("text", []):
            messages.error(request, error)
    return _memories_response(request)


@login_required
@require_POST
def memory_toggle(request: HttpRequest, pk: int) -> HttpResponse:
    memory = get_object_or_404(Memory, pk=pk, user=request.user)
    if memory_service.toggle_active(memory):
        messages.success(request, _("Memory used again."))
    else:
        messages.success(request, _("Memory paused: not used in replies."))
    return _memories_response(request)


@login_required
@require_POST
def memory_delete(request: HttpRequest, pk: int) -> HttpResponse:
    get_object_or_404(Memory, pk=pk, user=request.user).delete()
    messages.success(request, _("Memory deleted. It is no longer used in replies."))
    return _memories_response(request)


@login_required
@require_POST
def memory_confirm(request: HttpRequest) -> HttpResponse:
    if memory_service.confirm_suggestion(request.user, request.POST.get("key", "")):
        messages.success(request, _("Memory confirmed."))
    else:
        messages.error(request, _("This suggestion is no longer available."))
    return _memories_response(request)


@login_required
@require_POST
def notes_in_replies(request: HttpRequest) -> HttpResponse:
    profile = get_profile(request.user)
    profile.use_notes = request.POST.get("use_notes") == "on"
    profile.save(update_fields=["use_notes", "updated_at"])
    if profile.use_notes:
        messages.success(request, _("Relevant notes will be used in replies."))
    else:
        messages.success(request, _("Notes are no longer used in replies."))
    return _memories_response(request)


# ---------- Saved lists and data ----------
@login_required
@require_POST
def saved_remove(request: HttpRequest) -> HttpResponse:
    kind = request.POST.get("kind", "")
    try:
        pk = uuid.UUID(request.POST.get("product", ""))
    except ValueError as exc:
        raise Http404 from exc
    if kind == "scan":
        ProductVisit.objects.filter(user=request.user, product_id=pk).exclude(code="").delete()
        messages.success(request, _("Removed from scan history."))
    elif kind in SAVED_KINDS:
        ProductMark.objects.filter(
            user=request.user, product_id=pk, kind=SAVED_KINDS[kind]
        ).delete()
        messages.success(
            request,
            _("Removed from %(list)s.") % {"list": ProductMark.Kind(SAVED_KINDS[kind]).label},
        )
    else:
        raise Http404
    if _hx(request):
        return _fragment(
            request,
            "myola/partials/mytrack_saved_list.html",
            {"list": _saved_list(request.user, kind)},
        )
    return redirect(_tab_url("saved", f"saved-{kind}"))


def _back_to(request: HttpRequest, default_tab: str) -> str:
    tab = request.POST.get("tab", default_tab)
    return _tab_url(tab if tab in TABS else default_tab)


@login_required
@require_POST
def scans_clear(request: HttpRequest) -> HttpResponse:
    ProductVisit.objects.filter(user=request.user).exclude(code="").delete()
    messages.success(request, _("Scan history cleared."))
    url = _back_to(request, "saved")
    return _hx_redirect(url) if _hx(request) else redirect(url)


@login_required
@require_POST
def chat_clear(request: HttpRequest) -> HttpResponse:
    chat.clear(request.user)
    messages.success(request, _("Chat history deleted."))
    url = _back_to(request, "profile")
    return _hx_redirect(url) if _hx(request) else redirect(url)


# ---------- Ask Foodo ----------
def _chat_product(context: str):
    try:
        pid = chat.product_id(context)
    except ValueError as exc:
        raise Http404 from exc
    if pid is None:
        return None
    return get_object_or_404(
        Product.objects.select_related("current_revision"), pk=pid, current_revision__isnull=False
    )


def _chat_context(request, context: str, product) -> dict:
    return {
        "chat_context": context,
        "chat_id": context.replace(":", "-"),
        "chat_product": product,
        "chat_messages": chat.history(request.user, context),
        "chat_suggestions": chat.suggestions_for(context),
        "chat_next": request.GET.get("next", ""),
    }


@login_required
@require_GET
def chat_view(request: HttpRequest) -> HttpResponse:
    context = request.GET.get("context", chat.PERSONAL)
    product = _chat_product(context)
    ctx = _chat_context(request, context, product)
    if _hx(request):
        return render(request, "myola/partials/chat_body.html", ctx)
    ctx["nav_active"] = "my-ola"
    return render(request, "myola/partials/chat_page.html", ctx)


@login_required
@require_POST
@rate_limited("chat")
def chat_send(request: HttpRequest) -> HttpResponse:
    form = ChatForm(request.POST)
    if not form.is_valid():
        if "context" in form.errors:
            raise Http404
        if _hx(request):
            return HttpResponse(status=204)
        context = request.POST.get("context", chat.PERSONAL)
        return redirect(f"{reverse('chat')}?{urlencode({'context': context})}")
    context = form.cleaned_data["context"]
    product = _chat_product(context)
    asked, answered = chat.send(request.user, context, form.cleaned_data["text"], product)
    if _hx(request):
        return render(
            request,
            "myola/partials/chat_messages.html",
            {
                "chat_messages": [asked, answered],
                "chat_id": context.replace(":", "-"),
                "chat_context": context,
                "oob": True,
            },
        )
    query = urlencode({"context": context})
    return redirect(f"{reverse('chat')}?{query}#chat-log-{context.replace(':', '-')}")
