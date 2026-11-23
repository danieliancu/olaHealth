from __future__ import annotations

from django import forms
from django.utils.html import format_html
from django.utils.text import slugify

from contributions.models import Contribution
from core.models import LearnArticle, LearnTopic, MagazineCover
from staff.models import Announcement, SiteSettings


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


def model_choices(provider: str, current: str = "") -> list[tuple[str, str]]:
    """The models of ``provider`` for the Model menu; the saved one is kept even if not listed."""
    from ai.providers.catalog import PROVIDERS, models_for

    names, _live = models_for(provider)
    info = PROVIDERS.get(provider)
    default = info.default_model if info else ""
    ordered = ([default] if default in names else []) + [n for n in names if n != default]
    if current and current not in ordered:
        ordered.insert(0, current)  # the saved model (callers pass it only for its own provider)
    return [(n, f"{n} (default)" if n == default else n) for n in ordered]


def models_note(provider: str) -> str:
    from ai.providers.catalog import PROVIDERS, models_for

    info = PROVIDERS.get(provider)
    _names, live = models_for(provider)
    if live:
        return "Models offered by this provider right now."
    if info and not info.configured:
        need = info.env_key or info.base_url_env
        return f"Suggested models. Set {need} on the server to see this provider's full list."
    return "Suggested models (the provider's list could not be read)."


class SiteSettingsForm(forms.ModelForm):
    class Meta:
        model = SiteSettings
        fields = [
            "ai_enabled",
            "ai_provider",
            "ai_model",
            "ai_daily_messages_per_user",
            "ai_monthly_budget_gbp",
            "ai_send_ingredients",
            "ai_general_questions",
            "maintenance_message",
            "catalog_mode",
        ]
        labels = {
            "ai_enabled": "AI assistant enabled",
            "ai_provider": "AI provider",
            "ai_model": "Model",
            "ai_daily_messages_per_user": "Daily messages per user",
            "ai_monthly_budget_gbp": "Monthly budget (£)",
            "ai_send_ingredients": "Product chat: also send the ingredient list and additives",
            "ai_general_questions": "General chat: answer general food and nutrition questions",
            "maintenance_message": "Maintenance message",
            "catalog_mode": "Product catalogue",
        }
        help_texts = {
            "maintenance_message": "Shown to everyone when not empty.",
            "catalog_mode": "Automatic: the local catalogue is used first once an import has "
            "been verified. API only: keep fetching each product from Open Food Facts.",
            "ai_monthly_budget_gbp": "The assistant stops answering when this month's cost "
            "reaches the budget.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from django.urls import reverse

        from ai.providers.catalog import CHOICES, PROVIDERS

        provider = (
            self.data.get("ai_provider") if self.is_bound else None
        ) or self.instance.ai_provider
        if provider not in PROVIDERS:
            provider = "anthropic"
        self.fields["ai_provider"] = forms.ChoiceField(
            label="AI provider",
            choices=CHOICES,
            initial=provider,
            help_text="Each provider uses its own key from the server environment.",
            widget=forms.Select(
                attrs={
                    # Changing the provider reloads the list of its models (htmx).
                    "hx-get": reverse("staff:ai-models"),
                    "hx-target": "#id_ai_model",
                    "hx-swap": "outerHTML",
                    "hx-trigger": "change",
                }
            ),
        )
        self.fields["ai_model"] = forms.ChoiceField(
            label="Model",
            choices=model_choices(
                provider, self.instance.ai_model if provider == self.instance.ai_provider else ""
            ),
        )
        # In a span so the note changes with the menu (staff/partials/ai_model_select.html).
        self.fields["ai_model"].help_text = format_html(
            '<span id="ai-model-note">{}</span>', models_note(provider)
        )

    def clean_ai_model(self) -> str:
        return self.cleaned_data["ai_model"].strip()

    def clean_ai_monthly_budget_gbp(self):
        value = self.cleaned_data["ai_monthly_budget_gbp"]
        if value is not None and value < 0:
            raise forms.ValidationError("The budget cannot be negative.")
        return value


class AnnouncementForm(forms.ModelForm):
    class Meta:
        model = Announcement
        fields = ["title", "body", "url"]
        labels = {
            "title": "Title",
            "body": "Message (optional)",
            "url": "Link inside the app (optional)",
        }
        help_texts = {
            "url": "A path such as /learn/ or /products/. Links to other sites are dropped."
        }
        widgets = {"body": forms.Textarea(attrs={"rows": 3, "maxlength": 500})}

    def clean_url(self) -> str:
        from myola.services.notifications import safe_url

        url = (self.cleaned_data.get("url") or "").strip()
        if url and not safe_url(url):
            raise forms.ValidationError("Use a path inside the app, starting with /.")
        return url


# ---------- Learn CMS ----------
MARKUP_HELP = (
    "Blank line = new paragraph · <code>## Heading</code> · <code>- bullet</code> · "
    "<code>1. numbered</code> · <code>**bold**</code> · <code>*italic*</code> · "
    "<code>[text](/path)</code> or <code>[text](https://…)</code>. HTML is not allowed."
)
MAX_IMAGE_BYTES = 15 * 1024 * 1024  # re-encoded to a small WebP on upload


class LearnArticleForm(forms.ModelForm):
    class Meta:
        model = LearnArticle
        fields = [
            "title", "slug", "topic", "summary", "pull_quote", "body", "minutes",
            "image", "image_alt",
            "seo_title", "meta_description", "canonical_url", "noindex",
            "published", "published_at", "editors_pick", "order",
        ]  # fmt: skip
        labels = {
            "slug": "Web address (slug)",
            "minutes": "Reading time (minutes)",
            "pull_quote": "Pull quote (highlighted sentence)",
            "image_alt": "Image description (alt text)",
            "seo_title": "SEO title",
            "meta_description": "Meta description",
            "canonical_url": "Canonical URL",
            "noindex": "Hide from search engines (noindex)",
            "published_at": "Publication date",
            "editors_pick": "Editor’s pick",
        }
        help_texts = {
            "slug": "Leave empty to make it from the title. Changing it breaks old links.",
            "seo_title": "Search result title, max 70 characters. Empty: the article title.",
            "meta_description": "Search result snippet, max 160 characters. Empty: the summary.",
            "pull_quote": "Shown large next to the text when the article has no linked products.",
        }
        widgets = {
            "body": forms.Textarea(
                attrs={"rows": 18, "data-rich": "1", "data-rich-html": "body-html"}
            ),
            "summary": forms.Textarea(attrs={"rows": 3, "maxlength": 400}),
            "pull_quote": forms.Textarea(attrs={"rows": 2, "maxlength": 220}),
            "seo_title": forms.TextInput(attrs={"maxlength": 70}),
            "meta_description": forms.Textarea(attrs={"rows": 2, "maxlength": 160}),
            "published_at": forms.DateTimeInput(
                attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["slug"].required = False
        self.fields["published_at"].required = False

    def clean_published_at(self):
        from django.utils import timezone

        return self.cleaned_data.get("published_at") or self.instance.published_at or timezone.now()

    def clean_slug(self) -> str:
        slug = slugify(self.cleaned_data.get("slug") or self.data.get("title", ""))[:80]
        if not slug:
            raise forms.ValidationError("Add a title or a slug.")
        clash = LearnArticle.objects.filter(slug=slug).exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError("Another article already uses this address.")
        return slug

    def clean_image(self):
        """Any photo can be uploaded as is: it is checked, stripped of metadata (incl. GPS) and
        saved as a WebP no wider than the article photo is ever shown (core.images)."""
        image = self.cleaned_data.get("image")
        if not image or not hasattr(image, "content_type"):
            return image  # unchanged or cleared
        if getattr(image, "size", 0) > MAX_IMAGE_BYTES:
            raise forms.ValidationError("The image is larger than 15 MB.")
        from django.core.files.base import ContentFile

        from core.images import LEARN_MAX_WIDTH, ImageRejected, upload_to_webp

        try:
            data = upload_to_webp(image, LEARN_MAX_WIDTH, MAX_IMAGE_BYTES)
        except ImageRejected as exc:
            raise forms.ValidationError(str(exc)) from exc
        stem = (
            slugify(self.data.get("slug") or self.data.get("title") or "article")[:60] or "article"
        )
        return ContentFile(data, name=f"{stem}.webp")


class LearnTopicForm(forms.ModelForm):
    class Meta:
        model = LearnTopic
        fields = ["title", "key", "icon", "order"]
        labels = {"key": "Key (in the address, e.g. allergens)"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["key"].required = False
        from core.templatetags.ui import ICONS

        # Every icon in the app's set, shown as a visual grid (partials/form_fields.html).
        self.fields["icon"].widget = forms.RadioSelect(choices=[(n, n) for n in sorted(ICONS)])
        self.fields["icon"].icon_picker = True
        self.fields["icon"].help_text = "Pick the icon shown next to the topic."

    def clean_key(self) -> str:
        key = slugify(self.cleaned_data.get("key") or self.data.get("title", ""))[:40]
        if not key:
            raise forms.ValidationError("Add a title or a key.")
        return key

    def clean_icon(self) -> str:
        from core.templatetags.ui import ICONS

        icon = self.cleaned_data.get("icon", "")
        if icon not in ICONS:
            raise forms.ValidationError(
                "Unknown icon. Try book-open, scan-barcode, leaf, flask-conical…"
            )
        return icon


def _slider(low: int, high: int, key: str) -> forms.NumberInput:
    return forms.NumberInput(
        attrs={"type": "range", "min": low, "max": high, "step": 1, "data-photo": key}
    )


class MagazineCoverForm(forms.ModelForm):
    """The Magazine cover (one issue): month and year, photo, and where the photo sits."""

    month = forms.DateField(
        label="Month and year",
        input_formats=["%Y-%m", "%Y-%m-%d"],
        widget=forms.DateInput(attrs={"type": "month"}, format="%Y-%m"),
        help_text="The issue’s month, kept with the cover (not shown on the page).",
    )

    class Meta:
        model = MagazineCover
        fields = [
            "month", "image", "photo_size", "photo_x", "photo_y",
            "mobile_zoom", "mobile_x", "mobile_y",
        ]  # fmt: skip
        labels = {
            "image": "Cover photo",
            "photo_size": "Size",
            "photo_x": "Left / right",
            "photo_y": "Up / down",
            "mobile_zoom": "Size",
            "mobile_x": "Left / right",
            "mobile_y": "Up / down",
        }
        help_texts = {
            "photo_size": "Height of the photo, % of the cover's height.",
            "photo_x": "Its left edge from the middle of the screen, % of the cover's height "
            "(minus: further left).",
            "photo_y": "Its top edge, % of the cover's height (minus: higher).",
            "mobile_zoom": "Height of the photo in the phone cover card, % of the card's height "
            "(100: the whole photo).",
            "mobile_x": "Its left edge, % of the card's width (minus: further left).",
            "mobile_y": "Its top edge, % of the card's height (minus: higher).",
        }
        widgets = {
            "photo_size": _slider(40, 300, "h"),
            "photo_x": _slider(-300, 200, "x"),
            "photo_y": _slider(-100, 100, "y"),
            "mobile_zoom": _slider(40, 300, "mz"),
            "mobile_x": _slider(-200, 100, "mx"),
            "mobile_y": _slider(-200, 100, "my"),
        }

    def clean_month(self):
        return self.cleaned_data["month"].replace(day=1)

    def clean_image(self):
        """Checked, stripped of metadata and saved as a WebP no wider than the cover is shown."""
        image = self.cleaned_data.get("image")
        if not image or not hasattr(image, "content_type"):
            return image  # unchanged or cleared
        if getattr(image, "size", 0) > MAX_IMAGE_BYTES:
            raise forms.ValidationError("The image is larger than 15 MB.")
        from django.core.files.base import ContentFile

        from core.images import ImageRejected, upload_to_webp

        try:
            data = upload_to_webp(image, COVER_MAX_WIDTH, MAX_IMAGE_BYTES)
        except ImageRejected as exc:
            raise forms.ValidationError(str(exc)) from exc
        return ContentFile(data, name="cover.webp")


COVER_MAX_WIDTH = 3200  # wide enough for a 3:1 cover photo on large screens
