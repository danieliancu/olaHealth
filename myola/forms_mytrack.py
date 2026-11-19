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
    kind = forms.ChoiceField(
        label=_("Type"), choices=list(NOTE_KIND_LABELS.items()), initial=Note.Kind.GENERAL
    )
    product = forms.UUIDField(label=_("Product (optional)"), required=False)

    def clean_product(self):
        pk = self.cleaned_data.get("product")
        if not pk:
            return None
        product = Product.objects.filter(pk=pk, current_revision__isnull=False).first()
        if product is None:
            raise forms.ValidationError(_("This product is not available."))
        return product


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
