"""Today: the food diary, daily summary, water and activity, and the “how much?” step.

Server-rendered. Every action is a plain form POST that redirects back to the day; with htmx
the same POST returns the day body (``#today``) to swap in place, with a short status toast.
Deleted records can be put back once (“Undo”): a copy is kept in the session.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.http import urlencode
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from catalog.presenters import format_number
from myola import diary_target
from myola.forms_today import ActivityForm, AddFoodForm, EntryForm, QuickAddForm, WaterForm
from myola.models import LIMITS, ActivityEntry, DiaryEntry, Meal, WaterEntry
from myola.preferences import ACTIVITIES, ALLERGENS, DIETS, picker_context
from myola.services import diary
from myola.services.lists import record_visit
from myola.services.profiles import derive_prefs, get_profile, local_today, parse_day, user_tz

UNDO_KEY = "today_undo"
UNDO_SHOW_KEY = "today_undo_show"
MEAL_LABELS = dict(Meal.choices)
STATUS_TEXT = {
    "compatible": "Confirmed by the source",
    "incompatible": "Not compatible",
    "may_contain": "May contain (traces)",
    "unknown": "Cannot be confirmed",
    "contains": "Contains",
    "not_declared": "Not declared",
}
LIMIT_TEXT = {
    "diary": "Your diary is full ({n} foods). Delete older entries to add more.",
    "water": "Your water log is full ({n} entries). Delete older entries to add more.",
    "activity": "Your activity log is full ({n} entries). Delete older entries to add more.",
}


def _htmx(request: HttpRequest) -> bool:
    return request.headers.get("HX-Request") == "true"


def _today_url(day: dt.date | str, **extra) -> str:
    params = {"date": str(day), **{k: v for k, v in extra.items() if v}}
    return f"{reverse('today')}?{urlencode(params)}"


def _limit_text(key: str) -> str:
    return _(LIMIT_TEXT[key]).format(n=LIMITS[key])


def _amount(value) -> Decimal | None:
    try:
        number = Decimal(str(value).replace(",", "."))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() and 0 < number <= 10000 else None


# ---------- The day ----------
def _context(request: HttpRequest, profile, day: dt.date, **extra) -> dict:
    user = request.user
    today = local_today(profile)
    groups, day_totals = diary.day_diary(user, day)
    show_energy = profile.show_energy and not profile.eating_disorder
    targets = profile.targets if profile.show_targets else {}
    quick = extra.pop("quick", None)  # (meal, bound form) after a failed quick add
    edit = extra.pop("edit", None)  # (entry id, form)
    for g in groups:
        target = {"add": g.key, "date": day.isoformat()}
        g.scan_url = f"{reverse('scan')}?{diary_target.query(target)}"
        g.search_url = f"{reverse('products')}?{diary_target.query(target)}"
        if quick and quick[0] == g.key:
            g.quick_form, g.quick_open = quick[1], True
        else:
            g.quick_form = QuickAddForm(
                prefix=g.key, today=today, initial={"date": day, "meal": g.key}
            )
            g.quick_open = False
        for e in g.entries:
            e.amount_display = format_number(e.amount)
            e.kcal_display = diary.fmt(e.values["energy-kcal"])
            if edit and str(edit[0]) == str(e.pk):
                e.edit_form = edit[1]
                e.pv = diary.preview_rows(
                    diary.entry_per100(e), e.amount, e.basis_known, show_energy
                )
    water = list(WaterEntry.objects.filter(user=user, date=day))
    activity = list(ActivityEntry.objects.filter(user=user, date=day))
    for a in activity:
        a.label = ACTIVITIES.get(a.kind, ACTIVITIES["other"])[0]
    scan_target = {"add": "auto", "date": day.isoformat()}
    rows = diary.target_rows(day_totals, targets, show_energy)
    return {
        "profile": profile,
        "picker": picker_context(),
        "nav_active": "today",
        "day": day,
        "day_iso": day.isoformat(),
        "today": today,
        "is_today": day == today,
        "day_title": diary.day_title(day, today),
        "day_sub": date_format(day, "j F Y"),
        "week": diary.week(user, day, today),
        "groups": groups,
        "entry_count": day_totals.count,
        "rows": rows,
        "any_partial": bool(day_totals.count) and any(r["partial"] for r in rows),
        "show_energy": show_energy,
        "has_targets": any(profile.targets.values()),
        "scan_url": f"{reverse('scan')}?{diary_target.query(scan_target)}",
        "water": water,
        "water_total": diary.water_display(sum(w.ml for w in water)),
        "water_form": WaterForm(today=today, initial={"date": day}),
        "activity": activity,
        "activity_total": f"{diary.fmt(sum(a.minutes for a in activity))} min",
        "activity_form": ActivityForm(
            today=today, initial={"date": day, "kind": next(iter(ACTIVITIES))}
        ),
        "tzname": profile.timezone,
        # With energy hidden (settings or an eating-disorder history) kcal is not asked either.
        "nutrient_fields": ("kcal_100",) * show_energy + ("protein_100", "carbs_100", "fat_100"),
        **extra,
    }


def _render_day(request: HttpRequest, profile, day: dt.date, **extra) -> HttpResponse:
    """The whole page, or only the day body for htmx."""
    context = _context(request, profile, day, **extra)
    if _htmx(request):
        return render(request, "myola/partials/today_body.html", context)
    return render(request, "myola/today.html", context)


def _done(request: HttpRequest, profile, day: dt.date, text: str, undo: dict | None = None):
    """After a change: the day body with a toast (htmx), or a redirect with a message."""
    if undo:
        request.session[UNDO_KEY] = undo
    if _htmx(request):
        toast = {"text": text, "undo": undo["pk"] if undo else ""}
        return _render_day(request, profile, day, toast=toast)
    if undo:
        request.session[UNDO_SHOW_KEY] = True
        return redirect(_today_url(day))
    messages.success(request, text)
    return redirect(_today_url(day))


def _post_day(request: HttpRequest, profile) -> dt.date:
    return parse_day(request.POST.get("date"), profile)


@login_required
@require_GET
def today(request: HttpRequest) -> HttpResponse:
    profile = get_profile(request.user)
    day = parse_day(request.GET.get("date"), profile)
    extra = {}
    edit_id = request.GET.get("edit", "")
    if edit_id:
        entry = DiaryEntry.objects.filter(user=request.user, date=day, pk=_uuid(edit_id)).first()
        if entry:
            extra["edit"] = (entry.pk, _entry_form(entry, local_today(profile)))
    undo = request.session.get(UNDO_KEY)
    if request.session.pop(UNDO_SHOW_KEY, False) and undo:
        extra["toast"] = {"text": _undo_text(undo), "undo": undo["pk"]}
    return _render_day(request, profile, day, **extra)


def _uuid(value: str):
    import uuid

    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


# ---------- Diary entries ----------
def _entry_form(entry: DiaryEntry, today: dt.date, data=None) -> EntryForm:
    form = EntryForm(
        data,
        prefix=f"e{entry.pk.hex[:8]}",
        today=today,
        initial={
            "amount": format_number(entry.amount),
            "unit": entry.unit,
            "meal": entry.meal,
            "date": entry.date,
        },
    )
    if entry.product_id:
        form.fields["unit"].disabled = True
    form.fields["amount"].widget.attrs.update(
        {
            "hx-get": reverse("today-entry-preview", args=[entry.pk]),
            "hx-trigger": "input changed delay:200ms",
            "hx-target": f"#pv-{entry.pk}",
        }
    )
    return form


@login_required
@require_POST
def entry_add(request: HttpRequest) -> HttpResponse:
    """Quick add: a food typed in by hand for one meal."""
    profile = get_profile(request.user)
    today = local_today(profile)
    meal = request.POST.get("quick", "")
    if meal not in MEAL_LABELS:
        return redirect(_today_url(_post_day(request, profile)))
    form = QuickAddForm(request.POST, prefix=meal, today=today)
    if form.is_valid():
        d = form.cleaned_data
        values = {k: d[k] for k in ("kcal_100", "protein_100", "carbs_100", "fat_100")}
        try:
            diary.add_entry(
                request.user,
                date=d["date"],
                meal=meal,
                name=d["name"].strip(),
                amount=diary.round_amount(d["amount"]),
                unit=d["unit"],
                basis_known=d["basis_known"],
                **values,
            )
        except diary.LimitReached:
            form.add_error(None, _limit_text("diary"))
        else:
            text = _("Added to %(meal)s.") % {"meal": MEAL_LABELS[meal]}
            return _done(request, profile, d["date"], text)
    day = form.cleaned_data.get("date") or _post_day(request, profile)
    return _render_day(request, profile, day, quick=(meal, form))


@login_required
@require_http_methods(["GET", "POST"])
def entry(request: HttpRequest, pk) -> HttpResponse:
    """Edit or move one entry: inline form (htmx) or the day page with the form open."""
    profile = get_profile(request.user)
    today = local_today(profile)
    e = get_object_or_404(DiaryEntry, pk=pk, user=request.user)
    if request.method == "GET":
        if _htmx(request):
            e.pv = diary.preview_rows(
                diary.entry_per100(e), e.amount, e.basis_known, _show_energy(profile)
            )
            context = {"e": e, "form": _entry_form(e, today), "day_iso": e.date.isoformat()}
            return render(request, "myola/partials/diary_entry_form.html", context)
        return redirect(f"{_today_url(e.date, edit=str(e.pk))}#entry-{e.pk}")
    form = _entry_form(e, today, request.POST)
    old_day = e.date
    if not form.is_valid():
        return _render_day(request, profile, old_day, edit=(e.pk, form))
    d = form.cleaned_data
    amount = diary.round_amount(d["amount"])
    if amount != e.amount:
        e.portion = ""
    e.amount, e.meal, e.date = amount, d["meal"], d["date"]
    if not e.product_id and d.get("unit"):
        e.unit = d["unit"]
    e.save()
    if e.date != old_day:
        text = _("Moved to %(day)s.") % {"day": date_format(e.date, "l j F")}
    else:
        text = _("Entry updated.")
    return _done(request, profile, old_day, text)


def _show_energy(profile) -> bool:
    return profile.show_energy and not profile.eating_disorder


@login_required
@require_GET
def entry_preview(request: HttpRequest, pk) -> HttpResponse:
    profile = get_profile(request.user)
    e = get_object_or_404(DiaryEntry, pk=pk, user=request.user)
    amount = _amount(request.GET.get(f"e{e.pk.hex[:8]}-amount") or request.GET.get("amount"))
    pv = diary.preview_rows(diary.entry_per100(e), amount, e.basis_known, _show_energy(profile))
    return render(request, "myola/partials/diary_preview.html", {"pv": pv, "unit": e.unit})


@login_required
@require_POST
def entry_delete(request: HttpRequest, pk) -> HttpResponse:
    profile = get_profile(request.user)
    e = get_object_or_404(DiaryEntry, pk=pk, user=request.user)
    undo = diary.undo_data("entry", e)
    undo["label"] = e.name
    day = e.date
    e.delete()
    return _done(request, profile, day, _undo_text(undo), undo=undo)


def _undo_text(undo: dict) -> str:
    return _("%(name)s deleted.") % {"name": undo.get("label") or _("Entry")}


@login_required
@require_POST
def undo(request: HttpRequest) -> HttpResponse:
    """Put back the record deleted last (once)."""
    profile = get_profile(request.user)
    data = request.session.get(UNDO_KEY)
    if not data or data.get("pk") != request.POST.get("token"):
        day = _post_day(request, profile)
        return _done(request, profile, day, _("There is nothing to undo."))
    del request.session[UNDO_KEY]
    try:
        restored = diary.restore(request.user, data)
    except diary.LimitReached:
        restored = None
    day = dt.date.fromisoformat(data["date"])
    return _done(request, profile, day, _("Undone.") if restored else _("Could not undo."))


# ---------- Water and activity ----------
@login_required
@require_POST
def water_add(request: HttpRequest) -> HttpResponse:
    profile = get_profile(request.user)
    form = WaterForm(request.POST, today=local_today(profile))
    if form.is_valid():
        ml, day = form.cleaned_data["ml"], form.cleaned_data["date"]
        try:
            diary.add_water(request.user, day, ml)
        except diary.LimitReached:
            form.add_error(None, _limit_text("water"))
        else:
            return _done(request, profile, day, _("%(ml)s ml of water added.") % {"ml": ml})
    day = form.cleaned_data.get("date") or _post_day(request, profile)
    return _render_day(request, profile, day, water_form=form)


@login_required
@require_POST
def water_delete(request: HttpRequest, pk: int) -> HttpResponse:
    profile = get_profile(request.user)
    w = get_object_or_404(WaterEntry, pk=pk, user=request.user)
    undo = diary.undo_data("water", w)
    undo["label"] = f"{w.ml} ml"
    day = w.date
    w.delete()
    return _done(request, profile, day, _("%(ml)s ml removed.") % {"ml": w.ml}, undo=undo)


@login_required
@require_POST
def activity_add(request: HttpRequest) -> HttpResponse:
    profile = get_profile(request.user)
    form = ActivityForm(request.POST, today=local_today(profile))
    if form.is_valid():
        d = form.cleaned_data
        try:
            diary.add_activity(request.user, d["date"], d["kind"], d["minutes"])
        except diary.LimitReached:
            form.add_error(None, _limit_text("activity"))
        else:
            label = ACTIVITIES[d["kind"]][0]
            text = _("%(activity)s, %(minutes)s min added.") % {
                "activity": label,
                "minutes": d["minutes"],
            }
            return _done(request, profile, d["date"], text)
    day = form.cleaned_data.get("date") or _post_day(request, profile)
    return _render_day(request, profile, day, activity_form=form)


@login_required
@require_POST
def activity_delete(request: HttpRequest, pk: int) -> HttpResponse:
    profile = get_profile(request.user)
    a = get_object_or_404(ActivityEntry, pk=pk, user=request.user)
    label = ACTIVITIES.get(a.kind, ACTIVITIES["other"])[0]
    undo = diary.undo_data("activity", a)
    undo["label"] = str(label)
    day = a.date
    a.delete()
    return _done(request, profile, day, _("%(activity)s removed.") % {"activity": label}, undo)


# ---------- “How much?” for a catalogue product ----------
def _product_parts(request: HttpRequest, pk):
    from catalog.models import Product
    from myola.page import facts_from_product
    from myola.portions import BASIS_UNIT, portions

    product = get_object_or_404(
        Product.objects.select_related("current_revision"), pk=pk, current_revision__isnull=False
    )
    facts = facts_from_product(product, list(product.current_revision.assessments.all()))
    basis, values = facts.per_100()
    per100 = {k: (values[k].value if k in values else None) for k in diary.NUTRIENTS}
    return product, facts, BASIS_UNIT.get(basis or ""), per100, portions(facts.data, basis)


def _warnings(profile, facts) -> list[dict]:
    """The user's strict restrictions that the source does not confirm (check the label)."""
    from myola.restrictions import all_statuses, blocking

    prefs = derive_prefs(profile)
    if not prefs["diets"] and not prefs["allergens"]:
        return []
    out = []
    for b in blocking(all_statuses(facts), prefs["diets"], prefs["allergens"]):
        label = DIETS[b["key"]][0] if b["kind"] == "diet" else ALLERGENS[b["key"]]
        out.append({**b, "label": label, "status_text": _(STATUS_TEXT[b["status"]])})
    return out


def _local_now(profile) -> dt.datetime:
    return timezone.now().astimezone(user_tz(profile))


@login_required
@require_http_methods(["GET", "POST"])
def add_food(request: HttpRequest, pk) -> HttpResponse:
    """The short “how much?” step reached from the Food diary (scan or search) and from the
    product page. Saves a snapshot of the product's per-100 values."""
    profile = get_profile(request.user)
    today = local_today(profile)
    product, facts, unit, per100, portion_data = _product_parts(request, pk)
    options = portion_data["options"]
    if request.method == "POST":
        form = AddFoodForm(request.POST, today=today)
        if not unit:
            form.fields["unit"].required = True
        if form.is_valid():
            d = form.cleaned_data
            amount = diary.round_amount(d["amount"])
            chosen = next((o for o in options if o["key"] == d["portion"]), None)
            portion = chosen["label"] if chosen and _amount(chosen["amount"]) == amount else ""
            try:
                diary.add_entry(
                    request.user,
                    date=d["date"],
                    meal=d["meal"],
                    product=product,
                    name=facts.name or _("Unnamed product"),
                    brand=facts.brand[:200],
                    image=(facts.image or "")[:500],
                    amount=amount,
                    unit=unit or d["unit"],
                    basis_known=bool(unit),
                    portion=str(portion)[:80],
                    **{attr: per100[key] for key, attr in diary.NUTRIENTS.items()},
                )
            except diary.LimitReached:
                form.add_error(None, _limit_text("diary"))
            else:
                record_visit(request.user, product, d["scanned"])
                messages.success(
                    request, _("Added to %(meal)s.") % {"meal": MEAL_LABELS[d["meal"]]}
                )
                return redirect(_today_url(d["date"], added=d["meal"]))
        amount = _amount(request.POST.get("amount"))
        portion_key = request.POST.get("portion", "")
    else:
        meal = request.GET.get("meal", "")
        if meal not in MEAL_LABELS:
            meal = diary.default_meal(_local_now(profile))
        portion_key = request.GET.get("portion", "")
        amount = _amount(request.GET.get("amount"))
        chosen = next((o for o in options if o["key"] == portion_key), None)
        if chosen and amount is None:
            amount = _amount(chosen["amount"])
        form = AddFoodForm(
            today=today,
            initial={
                "meal": meal,
                "date": parse_day(request.GET.get("date"), profile),
                "amount": format_number(amount) if amount else None,
                "unit": unit or "g",
                "portion": portion_key if chosen else "",
                "scanned": request.GET.get("scanned", "")[:32],
            },
        )
    if unit:
        form.fields["unit"].disabled = True
    form.fields["amount"].widget.attrs.update(
        {
            "hx-get": reverse("add-food-preview", args=[product.pk]),
            "hx-trigger": "input changed delay:200ms",
            "hx-target": "#add-preview",
        }
    )
    day = form["date"].value()
    day = parse_day(str(day), profile) if day else today
    query = {
        "meal": form["meal"].value(),
        "date": day.isoformat(),
        "scanned": form["scanned"].value() or "",
    }
    base = reverse("add-food", args=[product.pk])
    for o in options:
        o["url"] = f"{base}?{urlencode({**query, 'portion': o['key']})}"
        o["selected"] = o["key"] == portion_key and _amount(o["amount"]) == amount
    return render(
        request,
        "myola/add.html",
        {
            "product": product,
            "data": product.current_revision.data,
            "facts": facts,
            "form": form,
            "unit": unit,
            "options": options,
            "warnings": _warnings(profile, facts),
            "day": day,
            "day_label": _("Today") if day == today else date_format(day, "l j M"),
            "back_url": _today_url(day),
            "pv": diary.preview_rows(per100, amount, bool(unit), _show_energy(profile)),
            "nav_active": "today",
        },
    )


@login_required
@require_GET
def add_food_preview(request: HttpRequest, pk) -> HttpResponse:
    profile = get_profile(request.user)
    _product, _facts, unit, per100, _portions = _product_parts(request, pk)
    amount = _amount(request.GET.get("amount"))
    pv = diary.preview_rows(per100, amount, bool(unit), _show_energy(profile))
    return render(request, "myola/partials/diary_preview.html", {"pv": pv, "unit": unit})
