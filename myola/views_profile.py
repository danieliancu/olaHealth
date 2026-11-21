"""Onboarding, profile, settings and progress pages (server-rendered, enhanced with htmx).

Every form works as a plain POST + redirect; htmx requests get the fragment to swap instead
(``HX-Request`` header, boosted requests excluded).
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.decorators.http import require_http_methods, require_POST

from myola import preferences as P
from myola.forms_profile import (
    SEX_CHOICES,
    AboutYouForm,
    ConditionsForm,
    GoalForm,
    RestrictionsForm,
    SettingsForm,
    TargetsDetailsForm,
    TargetsSaveForm,
    WeightForm,
)
from myola.models import WeightEntry
from myola.services import progress as progress_service
from myola.services import targets as targets_service
from myola.services.profiles import clear_health, finish_onboarding, get_profile, local_today

STEPS = 4
NOT_SAVED_HEALTH = gettext_lazy(
    "Your health answers were not saved, because you did not tick the consent box. "
    "You can add them later in My Track."
)


def is_htmx(request: HttpRequest) -> bool:
    return request.headers.get("HX-Request") == "true" and not request.headers.get("HX-Boosted")


def _safe_next(request: HttpRequest, default: str) -> str:
    target = request.POST.get("next") or request.GET.get("next") or ""
    if target and url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return target
    return default


# ---------- Onboarding ----------
def _step(value) -> int:
    try:
        return min(max(int(value), 1), STEPS)
    except (TypeError, ValueError):
        return 1


def _start_forms(profile, step: int, bound=None) -> dict:
    forms = {
        1: GoalForm(profile),
        2: AboutYouForm(profile),
        3: RestrictionsForm(profile, id_prefix="ob"),
        4: ConditionsForm(profile),
    }
    if bound is not None:
        forms[step] = bound
    return forms


def _render_start(request, profile, step: int, bound=None) -> HttpResponse:
    forms = _start_forms(profile, step, bound)
    context = {
        "step": step,
        "steps": range(1, STEPS + 1),
        "goal_form": forms[1],
        "about_form": forms[2],
        "restrictions_form": forms[3],
        "conditions_form": forms[4],
        "nav_active": "",
    }
    if is_htmx(request):
        response = render(request, "myola/partials/start_wizard.html", context)
        response["HX-Push-Url"] = f"{reverse('start')}?step={step}"
        return response
    return render(request, "myola/start.html", context)


@login_required
@require_http_methods(["GET", "POST"])
def start(request: HttpRequest) -> HttpResponse:
    """Onboarding: goal, about you, restrictions and allergies, health conditions."""
    profile = get_profile(request.user)
    step = _step(request.POST.get("step") or request.GET.get("step"))
    if request.method == "GET":
        return _render_start(request, profile, step)

    form_class = {1: GoalForm, 2: AboutYouForm, 3: RestrictionsForm, 4: ConditionsForm}[step]
    kwargs = {"id_prefix": "ob"} if step == 3 else {}
    form = form_class(profile, request.POST, **kwargs)
    if not form.is_valid():
        return _render_start(request, profile, step, bound=form)
    form.save()
    if step < STEPS:
        if is_htmx(request):
            return _render_start(request, profile, step + 1)
        return redirect(f"{reverse('start')}?step={step + 1}")

    finish_onboarding(profile, form.cleaned_data["conditions"])
    if form.discarded:
        messages.info(request, str(NOT_SAVED_HEALTH))
    messages.success(request, _("You are all set. Your answers are saved in your account."))
    if is_htmx(request):
        response = HttpResponse(status=204)
        response["HX-Redirect"] = reverse("today")
        return response
    return redirect("today")


# ---------- Profile ----------
PROFILE_SECTIONS = ("about", "restrictions", "conditions")


def _profile_forms(profile, section: str | None = None, bound=None) -> dict:
    forms = {
        "goal_form": GoalForm(profile, required=False),
        "about_form": AboutYouForm(profile),
        "restrictions_form": RestrictionsForm(profile, id_prefix="pr"),
        "conditions_form": ConditionsForm(profile),
    }
    if section == "about" and bound:
        forms["goal_form"], forms["about_form"] = bound
    elif section == "restrictions" and bound:
        forms["restrictions_form"] = bound
    elif section == "conditions" and bound:
        forms["conditions_form"] = bound
    return forms


def _render_profile(request, profile, section=None, bound=None, saved=False) -> HttpResponse:
    context = {
        **_profile_forms(profile, section, bound),
        "profile": profile,
        "saved": saved,
        "nav_active": "ask-foodo",
    }
    if section and is_htmx(request):
        return render(request, f"myola/partials/profile_{section}.html", context)
    return render(request, "myola/profile_edit.html", context)


@login_required
@require_http_methods(["GET", "POST"])
def profile_edit(request: HttpRequest) -> HttpResponse:
    """All onboarding answers on one page, one form per section (anchors: #about,
    #restrictions, #conditions). POST ``section`` picks the form; ``next`` (same site only)
    sends a plain POST back where it came from, e.g. the product page."""
    profile = get_profile(request.user)
    if request.method == "GET":
        return _render_profile(request, profile)
    section = request.POST.get("section", "")
    if section not in PROFILE_SECTIONS:
        return redirect("profile-edit")

    if section == "about":
        goal = GoalForm(profile, request.POST, required=False)
        about = AboutYouForm(profile, request.POST)
        if not (goal.is_valid() and about.is_valid()):
            return _render_profile(request, profile, section, (goal, about))
        goal.save()
        about.save()
        text = _("Profile saved.")
    elif section == "restrictions":
        form = RestrictionsForm(profile, request.POST, id_prefix="pr")
        if not form.is_valid():
            return _render_profile(request, profile, section, form)
        form.save()
        text = _("Restrictions and allergies saved.")
    else:
        form = ConditionsForm(profile, request.POST)
        if not form.is_valid():
            return _render_profile(request, profile, section, form)
        form.save()
        text = str(NOT_SAVED_HEALTH) if form.discarded else _("Health information saved.")

    if is_htmx(request):
        return _render_profile(request, profile, section, saved=text)
    messages.success(request, text)
    return redirect(_safe_next(request, f"{reverse('profile-edit')}#{section}"))


@login_required
@require_POST
def profile_clear_health(request: HttpRequest) -> HttpResponse:
    profile = get_profile(request.user)
    clear_health(profile)
    text = _("Health information deleted.")
    if is_htmx(request):
        return _render_profile(request, profile, "conditions", saved=text)
    messages.success(request, text)
    return redirect(f"{reverse('profile-edit')}#conditions")


# ---------- Settings ----------
@login_required
@require_http_methods(["GET", "POST"])
def settings_edit(request: HttpRequest) -> HttpResponse:
    """Daily targets, display switches, notes in Foodo's replies and the time zone."""
    profile = get_profile(request.user)
    form = SettingsForm(profile, request.POST or None)
    saved = ""
    if request.method == "POST" and form.is_valid():
        form.save()
        saved = _("Settings saved.")
        if not is_htmx(request):
            messages.success(request, saved)
            return redirect(_safe_next(request, reverse("settings-edit")))
        form = SettingsForm(profile)
    context = {"form": form, "profile": profile, "saved": saved, "nav_active": "ask-foodo"}
    if request.method == "POST" and is_htmx(request):
        return render(request, "myola/partials/profile_settings_form.html", context)
    return render(request, "myola/settings_edit.html", context)


# ---------- Daily targets: Calculate automatically ----------
def _targets_profile(request: HttpRequest):
    profile = get_profile(request.user)
    if profile.eating_disorder:
        raise Http404  # no calorie targets at all (calories are hidden everywhere)
    return profile


def _asked(request: HttpRequest, profile) -> list[str]:
    """The details this estimate asks for: what is missing, or all of them ("Change details");
    on a POST, the list the form was built with."""
    if request.method == "POST":
        return [n for n in request.POST.getlist("asked") if n in targets_service.FIELDS]
    if request.GET.get("all"):
        return list(targets_service.FIELDS)
    return targets_service.missing(profile)


def _targets_render(request, profile, details, result=None, save_form=None) -> HttpResponse:
    gender = details.gender_value()
    context = {
        "profile": profile,
        "details": details,
        "ask_sex": targets_service.needs_sex_choice(gender) and bool(gender),
        "result": result,
        "save_form": save_form,
        "current": [
            (label, value, unit)
            for label, value, unit in (
                (_("Energy"), profile.target_kcal, "kcal"),
                (_("Protein"), profile.target_protein, "g"),
                (_("Carbs"), profile.target_carbs, "g"),
                (_("Fat"), profile.target_fat, "g"),
            )
            if value
        ],
        "nhs_url": targets_service.NHS_URL,
        "nav_active": "ask-foodo",
    }
    if details.is_bound and details.is_valid():
        v = details.values()
        sex = details.cleaned_data.get("sex") or "between"
        context["carry"] = [(f"d-{n}", details.cleaned_data[n]) for n in details.asked]
        context["carry"].append(("d-sex", sex))
        context["basis"] = ", ".join(
            str(part)
            for part in (
                v["age"],
                P.GENDERS.get(v["gender"], "")
                if v["gender"] in ("male", "female")
                else dict(SEX_CHOICES).get(sex, ""),
                f"{float(v['height']):g} cm",
                f"{float(v['weight']):g} kg",
                P.ACTIVITY_LEVELS[v["activity_level"]][0],
                P.GOALS[v["goal"]][0],
            )
            if part
        ).lower()
    if is_htmx(request):
        return render(request, "myola/partials/targets_calc.html", context)
    return render(request, "myola/targets_calc_page.html", context)


def _estimate(details, profile):
    v = details.values()
    sex = v["gender"] if v["gender"] in ("male", "female") else details.cleaned_data.get("sex")
    return targets_service.estimate(
        age=v["age"],
        sex=sex or "between",
        height=v["height"],
        weight=v["weight"],
        activity_level=v["activity_level"],
        goal=v["goal"],
        conditions=profile.conditions,
    )


@login_required
@require_http_methods(["GET", "POST"])
def targets_calculate(request: HttpRequest) -> HttpResponse:
    """Step 1 asks only for the missing details; step 2 shows the four estimated targets,
    editable. Nothing is saved here."""
    profile = _targets_profile(request)
    asked = _asked(request, profile)
    data = request.POST if request.method == "POST" else None
    details = TargetsDetailsForm(profile, asked, data)
    if data is None and asked:
        return _targets_render(request, profile, details)
    if data is None:
        details = TargetsDetailsForm(profile, [], {"d-sex": request.GET.get("sex", "between")})
    if not details.is_valid():
        return _targets_render(request, profile, details)
    result = _estimate(details, profile)
    save_form = TargetsSaveForm(
        initial={
            "target_kcal": result.kcal,
            "target_protein": result.protein,
            "target_carbs": result.carbs,
            "target_fat": result.fat,
        }
    )
    return _targets_render(request, profile, details, result, save_form)


@login_required
@require_POST
def targets_save(request: HttpRequest) -> HttpResponse:
    """Save the (possibly edited) targets, and the details typed in step 1."""
    profile = _targets_profile(request)
    details = TargetsDetailsForm(profile, _asked(request, profile), request.POST)
    save_form = TargetsSaveForm(request.POST)
    if not (details.is_valid() and save_form.is_valid()):
        result = _estimate(details, profile) if details.is_valid() else None
        return _targets_render(request, profile, details, result, save_form)
    values = {name: details.cleaned_data[name] for name in details.asked}
    values.update(save_form.cleaned_data)
    for name, value in values.items():
        setattr(profile, name, value)
    profile.save(update_fields=[*values, "updated_at"])
    messages.success(request, _("Targets saved."))
    url = reverse("settings-edit") + "#targets"
    if is_htmx(request):
        response = HttpResponse(status=204)
        response["HX-Redirect"] = url
        return response
    return redirect(url)


# ---------- Progress ----------
CHARTS = (
    # key, title, icon, unit
    ("energy", gettext_lazy("Energy"), "flame", "kcal"),
    ("proteins", gettext_lazy("Protein"), "egg", "g"),
    ("carbohydrates", gettext_lazy("Carbs"), "wheat", "g"),
    ("fat", gettext_lazy("Fat"), "droplet", "g"),
    ("water", gettext_lazy("Water"), "glass-water", "ml"),
    ("activity", gettext_lazy("Activity"), "activity", "min"),
)


def _progress_context(request, profile, span: int, weight_form=None, weight_saved="") -> dict:
    today = local_today(profile)
    data = progress_service.series(request.user, today, span)
    show_energy = profile.energy_visible
    charts = []
    for key, title, icon, unit in CHARTS:
        if key == "energy" and not show_energy:
            continue
        chart = progress_service.bar_chart(data, key, unit)
        chart.update(title=title, icon=icon, diary=key not in ("water", "activity"))
        charts.append(chart)
    weights = data["weights"]
    return {
        "span": span,
        "ranges": progress_service.RANGES,
        "data": data,
        "charts": charts,
        "show_energy": show_energy,
        "track_weight": profile.track_weight,
        "weights": weights,
        "weight_first": weights[0] if weights else None,
        "weight_last": weights[-1] if weights else None,
        "weight_form": weight_form or WeightForm(),
        "weight_saved": weight_saved,
        "today": today,
        "nav_active": "progress",
    }


@login_required
@require_http_methods(["GET"])
def progress(request: HttpRequest) -> HttpResponse:
    """Trends over 7 or 30 days (``?range=7|30``) and the optional weight log."""
    profile = get_profile(request.user)
    span = progress_service.parse_range(request.GET.get("range"))
    context = _progress_context(request, profile, span)
    if is_htmx(request):
        response = render(request, "myola/partials/progress_body.html", context)
        response["HX-Push-Url"] = f"{reverse('progress')}?range={span}"
        return response
    return render(request, "myola/progress.html", context)


def _weight_done(request, profile, span: int, text: str = "", form=None) -> HttpResponse:
    if is_htmx(request):
        context = _progress_context(request, profile, span, weight_form=form, weight_saved=text)
        return render(request, "myola/partials/progress_weight.html", context)
    if text:
        messages.success(request, text)
    return redirect(f"{reverse('progress')}?range={span}#weight")


@login_required
@require_POST
def weight_save(request: HttpRequest) -> HttpResponse:
    """Save today's weight; a second entry the same day replaces the first."""
    profile = get_profile(request.user)
    span = progress_service.parse_range(request.POST.get("range"))
    form = WeightForm(request.POST)
    if not form.is_valid():
        if is_htmx(request):
            return _weight_done(request, profile, span, form=form)
        context = _progress_context(request, profile, span, weight_form=form)
        return render(request, "myola/progress.html", context)
    progress_service.save_weight(request.user, local_today(profile), form.cleaned_data["kg"])
    return _weight_done(request, profile, span, _("Weight saved for today."))


@login_required
@require_POST
def weight_delete(request: HttpRequest, pk: int) -> HttpResponse:
    profile = get_profile(request.user)
    span = progress_service.parse_range(request.POST.get("range"))
    get_object_or_404(WeightEntry, pk=pk, user=request.user).delete()
    return _weight_done(request, profile, span, _("Weight entry deleted."))
