from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _


class DeleteAccountForm(forms.Form):
    """Account deletion is confirmed with the current password and a typed word."""

    password = forms.CharField(
        label=_("Your password"),
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )
    confirm = forms.CharField(
        label=_("Type DELETE to confirm"),
        max_length=10,
        widget=forms.TextInput(attrs={"autocomplete": "off", "autocapitalize": "characters"}),
    )

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_password(self) -> str:
        password = self.cleaned_data["password"]
        if not self.user.check_password(password):
            raise forms.ValidationError(_("The password is not correct."))
        return password

    def clean_confirm(self) -> str:
        value = self.cleaned_data["confirm"].strip()
        if value.upper() != "DELETE":
            raise forms.ValidationError(_("Type DELETE to confirm."))
        return value
