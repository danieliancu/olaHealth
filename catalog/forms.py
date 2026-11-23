from django import forms
from django.utils.translation import gettext_lazy as _

from catalog.services.search import COMPLETENESS, SORTS

FORMAT_CHOICES = [
    ("manual", "manual"),
    ("ean13", "EAN-13"),
    ("ean8", "EAN-8"),
    ("upca", "UPC-A"),
    ("upce", "UPC-E"),
    ("itf14", "ITF-14"),
]


class LookupForm(forms.Form):
    code = forms.CharField(
        label=_("Barcode number"),
        max_length=32,
        widget=forms.TextInput(
            attrs={
                "inputmode": "numeric",
                "autocomplete": "off",
                "pattern": "[0-9 \\-]*",
                "placeholder": "e.g. 5941234567890",
            }
        ),
    )
    format = forms.ChoiceField(choices=FORMAT_CHOICES, required=False, widget=forms.HiddenInput)
    allow_bad_check = forms.BooleanField(required=False, widget=forms.HiddenInput)


class ScanPhotoForm(forms.Form):
    photo = forms.FileField(
        label=_("Photo of the barcode"),
        widget=forms.ClearableFileInput(attrs={"accept": "image/jpeg,image/png,image/webp"}),
    )


class SearchForm(forms.Form):
    q = forms.CharField(label=_("Name or brand"), required=False, max_length=100)
    category = forms.CharField(label=_("Category"), required=False, max_length=200)
    country = forms.CharField(label=_("Country where sold"), required=False, max_length=100)
    score = forms.ChoiceField(
        label=_("Foodo Score"),
        required=False,
        choices=[
            ("", _("Any")),
            ("80", "80–100"),
            ("60", "60–79"),
            ("40", "40–59"),
            ("20", "20–39"),
            ("0", "0–19"),
            ("none", _("No score")),
        ],
    )
    completeness = forms.ChoiceField(
        label=_("Data completeness"),
        required=False,
        choices=[
            ("", _("Any")),
            ("high", _("80% or more")),
            ("medium", _("50–79%")),
            ("low", _("Below 50%")),
        ],
    )
    sort = forms.ChoiceField(label=_("Sort by"), required=False, choices=list(SORTS.items()))

    def clean_sort(self) -> str:
        return self.cleaned_data.get("sort") or "relevance"

    def clean_completeness(self) -> str:
        value = self.cleaned_data.get("completeness") or ""
        return value if value in COMPLETENESS else ""
