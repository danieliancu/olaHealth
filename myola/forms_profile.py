"""Forms for onboarding, the profile page, settings and the weight log.

Every form edits one ``Profile`` and is built with ``Form(profile, data=None)``: unbound, it
shows the saved answers; ``save()`` writes only its own fields.

``RestrictionsForm`` is also rendered by ``templates/partials/restrictions_picker.html`` (see the
contract at the top of that template).
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from functools import cache
from zoneinfo import available_timezones

from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from myola import preferences as P
from myola.models import Profile

EXCLUSIVE_RESTRICTIONS = {"none"}
EXCLUSIVE_CONDITIONS = {"none", "prefer_not"}
DEFAULT_TIMEZONE = "Europe/London"


def _choices(mapping: dict) -> list[tuple[str, str]]:
    return [(k, v[0] if isinstance(v, tuple) else v) for k, v in mapping.items()]


@cache
def timezone_choices() -> list[tuple[str, str]]:
    names = sorted(
        z for z in available_timezones() if "/" in z and not z.startswith(("Etc/", "SystemV/"))
    )
    if "UTC" not in names:
        names.append("UTC")
    return [(z, z.replace("_", " ")) for z in names]


class OneDecimalField(forms.DecimalField):
    """A number with at most one decimal (accepts a comma); more decimals are rounded."""

    def to_python(self, value):
        if isinstance(value, str):
            value = value.strip().replace(",", ".")
        if value in self.empty_values:
            return None
        try:
            return Decimal(str(value)).quantize(Decimal("0.1"))
        except (InvalidOperation, ValueError):
            raise forms.ValidationError(self.error_messages["invalid"], code="invalid") from None


def _range_messages(message) -> dict:
    return {"invalid": message, "min_value": message, "max_value": message}


class ProfileForm(forms.Form):
    """Base: initial values from the profile, ``save()`` writes ``cleaned_data`` back."""

    def __init__(self, profile: Profile, *args, **kwargs):
        self.profile = profile
        if "initial" not in kwargs:
            kwargs["initial"] = {name: getattr(profile, name) for name in self.base_fields}
        super().__init__(*args, **kwargs)

    def _write(self, values: dict) -> Profile:
        for name, value in values.items():
            setattr(self.profile, name, value)
        self.profile.save(update_fields=[*values, "updated_at"])
        return self.profile

    def save(self) -> Profile:
        return self._write(dict(self.cleaned_data))

    def option_list(self, name: str, icons: dict | None = None) -> list[dict]:
        """Choices of a field with their checked state (bound data first, then the profile)."""
        value = self[name].value()
        chosen = {str(v) for v in value} if isinstance(value, list | tuple) else {str(value)}
        return [
            {
                "key": key,
                "label": label,
                "icon": (icons or {}).get(key, ""),
                "checked": key in chosen,
                "id": f"{self.id_prefix}-{name}-{key}".replace(":", "-"),
            }
            for key, label in self.fields[name].choices
            if key != ""
        ]

    id_prefix = "p"


class GoalForm(ProfileForm):
    goal = forms.ChoiceField(
        label=_("Your goal"),
        choices=_choices(P.GOALS),
        widget=forms.RadioSelect,
        error_messages={"required": _("Choose one goal to continue.")},
    )

    def __init__(self, profile: Profile, *args, required: bool = True, **kwargs):
        super().__init__(profile, *args, **kwargs)
        self.fields["goal"].required = required

    @property
    def goal_options(self) -> list[dict]:
        return self.option_list("goal", {k: v[1] for k, v in P.GOALS.items()})


class AboutYouForm(ProfileForm):
    gender = forms.ChoiceField(
        label=_("What is your gender?"),
        choices=_choices(P.GENDERS),
        required=False,
        widget=forms.RadioSelect,
    )
    age = forms.IntegerField(
        label=_("How old are you?"),
        required=False,
        min_value=13,
        max_value=120,
        error_messages=_range_messages(_("Enter an age between 13 and 120, or leave it empty.")),
    )
    height = OneDecimalField(
        label=_("What is your height?"),
        required=False,
        min_value=Decimal(100),
        max_value=Decimal(250),
        error_messages=_range_messages(
            _("Enter a height between 100 and 250 cm, or leave it empty.")
        ),
    )
    weight = OneDecimalField(
        label=_("What is your current weight?"),
        required=False,
        min_value=Decimal(25),
        max_value=Decimal(400),
        error_messages=_range_messages(
            _("Enter a weight between 25 and 400 kg, or leave it empty.")
        ),
    )

    @property
    def gender_options(self) -> list[dict]:
        return self.option_list("gender")


class RestrictionsForm(ProfileForm):
    """Restrictions and allergies. "None" is exclusive; nut and seafood types are kept only with
    their restriction (none chosen means all are checked, see ``derive_prefs``); notes are kept
    only with "Religious restriction" or "Other" and never create a rule."""

    restrictions = forms.MultipleChoiceField(
        label=_("Restrictions and allergies"),
        choices=_choices(P.RESTRICTIONS),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    nut_types = forms.MultipleChoiceField(
        choices=_choices(P.NUT_TYPES), required=False, widget=forms.CheckboxSelectMultiple
    )
    seafood_types = forms.MultipleChoiceField(
        choices=_choices(P.SEAFOOD_TYPES), required=False, widget=forms.CheckboxSelectMultiple
    )
    religious_note = forms.CharField(
        label=_("Religious restriction – note (optional)"), max_length=300, required=False
    )
    other_note = forms.CharField(label=_("Other – note (optional)"), max_length=300, required=False)
    consent = forms.BooleanField(
        label=_(
            "I agree that AskFoodo stores my restrictions and allergies in my account to check "
            "products for me. I can delete them at any time."
        ),
        required=False,
    )

    def __init__(self, profile: Profile, *args, id_prefix: str = "r", **kwargs):
        self.id_prefix = id_prefix
        kwargs.setdefault("auto_id", f"{id_prefix}-%s")
        if "initial" not in kwargs:
            kwargs["initial"] = {
                name: getattr(profile, name) for name in self.base_fields if name != "consent"
            }
            kwargs["initial"]["consent"] = profile.restrictions_consent_at is not None
        super().__init__(profile, *args, **kwargs)

    def clean_restrictions(self) -> list[str]:
        chosen = self.cleaned_data["restrictions"]
        if EXCLUSIVE_RESTRICTIONS & set(chosen) and len(chosen) > 1:
            raise forms.ValidationError(
                _("“None” cannot be combined with other restrictions."), code="exclusive"
            )
        return [k for k in P.RESTRICTIONS if k in chosen]

    def clean(self):
        data = super().clean()
        chosen = set(data.get("restrictions") or [])
        if "nuts" not in chosen:
            data["nut_types"] = []
        if "seafood" not in chosen:
            data["seafood_types"] = []
        if "religious" not in chosen:
            data["religious_note"] = ""
        if "other" not in chosen:
            data["other_note"] = ""
        if chosen - EXCLUSIVE_RESTRICTIONS and not data.get("consent"):
            self.add_error(
                "consent",
                _("Tick the box so AskFoodo can keep your restrictions and allergies."),
            )
        return data

    def save(self) -> Profile:
        values = {k: v for k, v in self.cleaned_data.items() if k != "consent"}
        sensitive = set(values.get("restrictions") or []) - EXCLUSIVE_RESTRICTIONS
        # A ticked box is kept (and stays ticked) even before any restriction is chosen.
        values["restrictions_consent_at"] = (
            (self.profile.restrictions_consent_at or timezone.now())
            if sensitive or self.cleaned_data.get("consent")
            else None
        )
        return self._write(values)

    @property
    def restriction_options(self) -> list[dict]:
        return self.option_list("restrictions", {k: v[1] for k, v in P.RESTRICTIONS.items()})

    @property
    def nut_options(self) -> list[dict]:
        return self.option_list("nut_types")

    @property
    def seafood_options(self) -> list[dict]:
        return self.option_list("seafood_types")


class ConditionsForm(ProfileForm):
    """Health conditions: stored only with explicit consent. Without consent nothing from this
    form is kept (and an earlier consent is withdrawn)."""

    conditions = forms.MultipleChoiceField(
        label=_("Health conditions"),
        choices=_choices(P.CONDITIONS),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    consent = forms.BooleanField(
        label=_(
            "I agree that AskFoodo stores these health answers in my account. "
            "I can delete them at any time."
        ),
        required=False,
    )

    def __init__(self, profile: Profile, *args, **kwargs):
        if "initial" not in kwargs:
            kwargs["initial"] = {
                "conditions": profile.conditions,
                "consent": profile.health_consent_at is not None,
            }
        super().__init__(profile, *args, **kwargs)
        self.discarded = False

    def clean_conditions(self) -> list[str]:
        chosen = self.cleaned_data["conditions"]
        if EXCLUSIVE_CONDITIONS & set(chosen) and len(chosen) > 1:
            raise forms.ValidationError(
                _("“None” and “Prefer not to answer” cannot be combined with other answers."),
                code="exclusive",
            )
        return [k for k in P.CONDITIONS if k in chosen]

    def save(self) -> Profile:
        conditions = self.cleaned_data["conditions"]
        if self.cleaned_data["consent"]:
            return self._write(
                {
                    "conditions": conditions,
                    "health_consent_at": self.profile.health_consent_at or timezone.now(),
                }
            )
        self.discarded = bool(conditions)
        return self._write({"conditions": [], "conditions_note": "", "health_consent_at": None})

    @property
    def condition_options(self) -> list[dict]:
        return self.option_list("conditions")


def _target(label, maximum: int) -> forms.IntegerField:
    message = _("Enter a whole number from 1 to %(max)s, or leave it empty.") % {"max": maximum}
    return forms.IntegerField(
        label=label,
        required=False,
        min_value=1,
        max_value=maximum,
        error_messages=_range_messages(message),
    )


class SettingsForm(ProfileForm):
    target_kcal = _target(_("Energy"), 10000)
    target_protein = _target(_("Protein"), 1000)
    target_carbs = _target(_("Carbs"), 2000)
    target_fat = _target(_("Fat"), 1000)
    show_energy = forms.BooleanField(label=_("Show calories"), required=False)
    show_targets = forms.BooleanField(label=_("Show my targets on Today"), required=False)
    track_weight = forms.BooleanField(label=_("Track my weight in Progress"), required=False)
    use_notes = forms.BooleanField(
        label=_("Use my Notes to personalise Foodo’s replies"), required=False
    )
    ai_use_preferences = forms.BooleanField(
        label=_("Use my diets and allergies in AI answers"), required=False
    )
    ai_use_my_data = forms.BooleanField(
        label=_("Let Ask Foodo use my diary, lists, notes and memories"), required=False
    )
    timezone = forms.ChoiceField(label=_("Time zone"), choices=timezone_choices)
    notify_reminders = forms.BooleanField(label=_("Daily reminders"), required=False)
    notify_achievements = forms.BooleanField(label=_("New achievements"), required=False)
    notify_contributions = forms.BooleanField(
        label=_("Answers to my product corrections"), required=False
    )
    notify_announcements = forms.BooleanField(label=_("News from AskFoodo"), required=False)
    notify_water = forms.BooleanField(label=_("Water reminders"), required=False)
    weigh_day = forms.TypedChoiceField(
        label=_("Weekly weigh-in reminder on"),
        coerce=int,
        required=False,
        empty_value=0,
        choices=[
            (0, _("Monday")),
            (1, _("Tuesday")),
            (2, _("Wednesday")),
            (3, _("Thursday")),
            (4, _("Friday")),
            (5, _("Saturday")),
            (6, _("Sunday")),
        ],
    )

    def __init__(self, profile: Profile, *args, **kwargs):
        super().__init__(profile, *args, **kwargs)
        if profile.eating_disorder:
            # Calories stay hidden everywhere with this answer: the switch cannot turn them on.
            self.fields["show_energy"].disabled = True
            self.initial["show_energy"] = False
        if profile.timezone not in dict(timezone_choices()):
            self.initial["timezone"] = DEFAULT_TIMEZONE


class WeightForm(forms.Form):
    kg = OneDecimalField(
        label=_("Weight in kg"),
        min_value=Decimal(25),
        max_value=Decimal(400),
        error_messages={
            **_range_messages(_("Enter a weight between 25 and 400 kg.")),
            "required": _("Enter a weight between 25 and 400 kg."),
        },
    )


# ---------- Daily targets: Calculate automatically ----------
DETAIL_FIELDS = ("age", "gender", "height", "weight", "activity_level", "goal")
SEX_CHOICES = [
    ("between", _("In between (average)")),
    ("female", _("Female")),
    ("male", _("Male")),
]


class TargetsDetailsForm(forms.Form):
    """Only the details the estimate still needs (``asked``), plus the equation to use when
    the gender is not male or female. Nothing is saved by this form."""

    prefix = "d"
    age = AboutYouForm.base_fields["age"]
    gender = forms.ChoiceField(label=_("Gender"), choices=_choices(P.GENDERS))
    height = AboutYouForm.base_fields["height"]
    weight = AboutYouForm.base_fields["weight"]
    activity_level = forms.ChoiceField(
        label=_("How active are you?"), choices=_choices(P.ACTIVITY_LEVELS)
    )
    goal = forms.ChoiceField(label=_("Your goal"), choices=_choices(P.GOALS))
    sex = forms.ChoiceField(
        label=_("Which equation should we use?"), choices=SEX_CHOICES, initial="between"
    )

    def __init__(self, profile: Profile, asked: list[str], *args, **kwargs):
        self.profile = profile
        self.asked = [name for name in asked if name in DETAIL_FIELDS]
        kwargs.setdefault("initial", {n: getattr(profile, n) for n in self.asked})
        super().__init__(*args, **kwargs)
        for name in list(self.fields):
            if name not in (*self.asked, "sex"):
                del self.fields[name]
        for name in self.asked:
            self.fields[name].required = True
        self.fields["sex"].required = False

    def gender_value(self) -> str:
        if "gender" in self.asked:
            return self["gender"].value() or ""
        return self.profile.gender

    def values(self) -> dict:
        """All six details: the answers given here, the profile for the rest."""
        out = {name: getattr(self.profile, name) for name in DETAIL_FIELDS}
        out.update({n: self.cleaned_data[n] for n in self.asked})
        return out

    @property
    def gender_options(self) -> list[dict]:
        return self.option_list("gender", P.GENDERS)

    @property
    def sex_options(self) -> list[dict]:
        return self.option_list("sex", dict(SEX_CHOICES))

    @property
    def activity_options(self) -> list[dict]:
        return self.option_list("activity_level", P.ACTIVITY_LEVELS)

    @property
    def goal_options(self) -> list[dict]:
        return self.option_list("goal", P.GOALS)

    def option_list(self, name: str, mapping: dict) -> list[dict]:
        value = str(self[name].value() or (self.fields[name].initial or ""))
        return [
            {
                "key": key,
                "label": v[0] if isinstance(v, tuple) else v,
                "hint": v[1] if isinstance(v, tuple) and len(v) > 2 else "",
                "checked": key == value,
                "id": f"d-{name}-{key}",
            }
            for key, v in mapping.items()
        ]


class TargetsSaveForm(forms.Form):
    prefix = "t"
    target_kcal = _target(_("Energy"), 10000)
    target_protein = _target(_("Protein"), 1000)
    target_carbs = _target(_("Carbs"), 2000)
    target_fat = _target(_("Fat"), 1000)
