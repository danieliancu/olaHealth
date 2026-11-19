from __future__ import annotations

from django import forms

from contributions.models import Contribution
from staff.models import SiteSettings


class ContributionFilterForm(forms.Form):
    STATUS_CHOICES = [
        ("submitted", "Waiting"),
        ("conflict", "Conflict"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
        ("all", "All"),
    ]
    AGE_CHOICES = [
        ("", "Any age"),
        ("1", "Older than 1 day"),
        ("7", "Older than 7 days"),
        ("30", "Older than 30 days"),
    ]
    status = forms.ChoiceField(choices=STATUS_CHOICES, required=False)
    problem_type = forms.ChoiceField(
        choices=[("", "Any problem"), *Contribution.ProblemType.choices], required=False
    )
    age = forms.ChoiceField(choices=AGE_CHOICES, required=False, label="Age")


class DecisionForm(forms.Form):
    decision = forms.ChoiceField(
        choices=[("approve", "Approve"), ("reject", "Reject"), ("rebase", "Re-base")]
    )
    reason = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3}),
        required=False,
        max_length=2000,
        help_text="Required to reject or re-base. Stored in the audit log.",
    )


class FlagFilterForm(forms.Form):
    status = forms.ChoiceField(
        choices=[("open", "Open"), ("resolved", "Closed"), ("all", "All")], required=False
    )
    kind = forms.ChoiceField(
        choices=[
            ("", "Any kind"),
            ("data_conflict", "Data conflict"),
            ("identity_conflict", "Identity conflict"),
            ("unusual_value", "Unusual value"),
        ],
        required=False,
    )


class FlagActionForm(forms.Form):
    action = forms.ChoiceField(
        choices=[("keep", "Keep"), ("accept_off", "Accept"), ("close", "Close")]
    )
    note = forms.CharField(required=False, max_length=500, label="Note (optional)")


class UserFilterForm(forms.Form):
    q = forms.CharField(required=False, max_length=200, label="Email")
    status = forms.ChoiceField(
        choices=[
            ("", "All accounts"),
            ("active", "Active"),
            ("inactive", "Deactivated"),
            ("unverified", "Email not confirmed"),
            ("staff", "Staff"),
        ],
        required=False,
    )


class DeleteUserForm(forms.Form):
    confirm = forms.CharField(
        max_length=254,
        label="Type the account's email address to confirm",
        widget=forms.TextInput(attrs={"autocomplete": "off", "spellcheck": "false"}),
    )


class SiteSettingsForm(forms.ModelForm):
    class Meta:
        model = SiteSettings
        fields = [
            "ai_enabled",
            "ai_provider",
            "ai_model",
            "ai_daily_messages_per_user",
            "ai_monthly_budget_usd",
            "maintenance_message",
        ]
        labels = {
            "ai_enabled": "AI assistant enabled",
            "ai_provider": "AI provider",
            "ai_model": "Model",
            "ai_daily_messages_per_user": "Daily messages per user",
            "ai_monthly_budget_usd": "Monthly budget (USD)",
            "maintenance_message": "Maintenance message",
        }
        help_texts = {
            "maintenance_message": "Shown to everyone when not empty.",
            "ai_monthly_budget_usd": "The assistant stops answering when this month's cost "
            "reaches the budget.",
        }

    def clean_ai_model(self) -> str:
        return self.cleaned_data["ai_model"].strip()

    def clean_ai_monthly_budget_usd(self):
        value = self.cleaned_data["ai_monthly_budget_usd"]
        if value is not None and value < 0:
            raise forms.ValidationError("The budget cannot be negative.")
        return value
