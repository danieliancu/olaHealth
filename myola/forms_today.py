"""Forms of the Today page: food diary entries, quick add, water and activity."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from django import forms
from django.utils.translation import gettext_lazy as _

from myola.models import ACTIVITY_CHOICES, Meal

UNIT_CHOICES = [("g", "g"), ("ml", "ml")]


def _number(label, *, min_value, max_value, required=True, **attrs):
    return forms.DecimalField(
        label=label,
        required=required,
        min_value=Decimal(min_value),
        max_value=Decimal(max_value),
        max_digits=9,
        decimal_places=2,
        widget=forms.NumberInput(attrs={"inputmode": "decimal", "step": "any", **attrs}),
    )


class DayMixin:
    """``date`` may never be in the user's future (``today`` comes from their timezone)."""

    def __init__(self, *args, today: dt.date, **kwargs):
        self.today = today
        super().__init__(*args, **kwargs)
        if "date" in self.fields:
            self.fields["date"].widget.attrs["max"] = today.isoformat()

    def clean_date(self):
        day = self.cleaned_data["date"]
        if day > self.today:
            raise forms.ValidationError(_("Choose today or an earlier day."))
        return day


def _date_field():
    return forms.DateField(label=_("Day"), widget=forms.DateInput(attrs={"type": "date"}))


def _amount_field(label=None):
    return _number(label or _("Amount"), min_value="0.1", max_value=10000, min="0.1", max="10000")


class EntryForm(DayMixin, forms.Form):
    """Edit or move a diary entry. The unit is fixed for foods from the catalogue."""

    amount = _amount_field()
    unit = forms.ChoiceField(label=_("Unit"), choices=UNIT_CHOICES, required=False)
    meal = forms.ChoiceField(label=_("Meal"), choices=Meal.choices)
    date = _date_field()


class AddFoodForm(DayMixin, forms.Form):
    """“How much did you have?” for a catalogue product."""

    amount = _amount_field(_("How much did you have?"))
    unit = forms.ChoiceField(label=_("Unit"), choices=UNIT_CHOICES, required=False)
    meal = forms.ChoiceField(label=_("Meal"), choices=Meal.choices)
    date = _date_field()
    portion = forms.CharField(required=False, max_length=20, widget=forms.HiddenInput)
    scanned = forms.CharField(required=False, max_length=32, widget=forms.HiddenInput)


class QuickAddForm(DayMixin, forms.Form):
    """A food typed in by hand (no catalogue product)."""

    name = forms.CharField(label=_("Food"), max_length=200)
    amount = _amount_field()
    unit = forms.ChoiceField(label=_("Unit"), choices=UNIT_CHOICES, initial="g")
    kcal_100 = _number(_("Energy (kcal)"), min_value=0, max_value=1000, required=False, min="0")
    protein_100 = _number(_("Protein (g)"), min_value=0, max_value=100, required=False, min="0")
    carbs_100 = _number(_("Carbs (g)"), min_value=0, max_value=100, required=False, min="0")
    fat_100 = _number(_("Fat (g)"), min_value=0, max_value=100, required=False, min="0")
    basis_known = forms.BooleanField(
        label=_("These values are per 100 g or 100 ml"), required=False, initial=True
    )
    meal = forms.ChoiceField(choices=Meal.choices, widget=forms.HiddenInput)
    date = forms.DateField(widget=forms.HiddenInput)


class WaterForm(DayMixin, forms.Form):
    ml = forms.IntegerField(
        label=_("Other amount in ml"),
        min_value=1,
        max_value=5000,
        widget=forms.NumberInput(
            attrs={"inputmode": "numeric", "min": "1", "max": "5000", "placeholder": _("Other")}
        ),
    )
    date = forms.DateField(widget=forms.HiddenInput)


class ActivityForm(DayMixin, forms.Form):
    kind = forms.ChoiceField(label=_("Type of activity"), choices=ACTIVITY_CHOICES)
    minutes = forms.IntegerField(
        label=_("Minutes"),
        min_value=1,
        max_value=1440,
        widget=forms.NumberInput(
            attrs={"inputmode": "numeric", "min": "1", "max": "1440", "placeholder": "30"}
        ),
    )
    date = forms.DateField(widget=forms.HiddenInput)
