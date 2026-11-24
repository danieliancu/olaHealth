"""Forms of My Track: notes, memories and Ask Foodo."""

from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from catalog.models import Product
from myola.models import Note
from myola.services import chat
from myola.services.memories import looks_medical

NOTE_KIND_LABELS = {
    Note.Kind.GENERAL: _("Other"),
    Note.Kind.TASTE: _("Taste"),
    Note.Kind.PREFER: _("Why I prefer it"),
    Note.Kind.AVOID: _("Why I avoid it"),
}


class NoteForm(forms.Form):
    text = forms.CharField(label=_("Note"), max_length=2000, widget=forms.Textarea)
    # The new-note form shows "Type" as the empty first option: no choice means a plain note.
    kind = forms.ChoiceField(
        label=_("Type"), choices=list(NOTE_KIND_LABELS.items()), required=False
    )
    product = forms.UUIDField(label=_("Product (optional)"), required=False)
    # The new-note form: one field to type a product's name or pick it from the list.
    product_name = forms.CharField(max_length=200, required=False)

    def clean_kind(self) -> str:
        return self.cleaned_data.get("kind") or Note.Kind.GENERAL

    def clean_product(self):
        pk = self.cleaned_data.get("product")
        if not pk:
            return None
        product = Product.objects.filter(pk=pk, current_revision__isnull=False).first()
        if product is None:
            raise forms.ValidationError(_("This product is not available."))
        return product

    def clean(self):
        data = super().clean()
        name = (data.pop("product_name", "") or "").strip()
        if data.get("product") is None and name and "product" not in self.errors:
            product = (
                Product.objects.filter(name__iexact=name, current_revision__isnull=False)
                .order_by("-updated_at")
                .first()
            )
            if product is None:
                self.add_error("product_name", _("Pick a product from the list."))
            else:
                data["product"] = product
        return data


class MemoryForm(forms.Form):
    text = forms.CharField(label=_("Something Foodo should remember"), max_length=300)

    def clean_text(self) -> str:
        text = self.cleaned_data["text"].strip()
        if looks_medical(text):
            raise forms.ValidationError(
                _(
                    "Foodo doesn't keep diagnoses, symptoms or medication. "
                    "Please remember food preferences only."
                )
            )
        return text


class ChatForm(forms.Form):
    context = forms.CharField(max_length=60)
    text = forms.CharField(max_length=300, required=False)
    intent = forms.ChoiceField(choices=[(i, i) for i in chat.INTENTS], required=False)

    def clean_context(self) -> str:
        value = self.cleaned_data["context"]
        try:
            chat.product_id(value)
        except ValueError as exc:
            raise forms.ValidationError(_("Unknown conversation.")) from exc
        return value

    def clean(self):
        data = super().clean()
        context, intent = data.get("context"), data.get("intent")
        if context and intent:
            # A suggested question: the label is the question.
            label = dict(chat.suggestions_for(context)).get(intent)
            if label:
                data["text"] = label
        if not (data.get("text") or "").strip() and "text" not in self.errors:
            raise forms.ValidationError(_("Type a question."))
        return data
