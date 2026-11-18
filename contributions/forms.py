from __future__ import annotations

from decimal import Decimal
from typing import Any

from django import forms
from django.utils.translation import gettext_lazy as _

from catalog.nutrition import NUTRIENTS, decimal_str, to_canonical_unit
from contributions.models import Contribution, ContributionImage
from off.mapping import sort_facts

NUTRITION_BASES = [
    ("per_100g", _("per 100 g")),
    ("per_100ml", _("per 100 ml")),
    ("per_serving", _("per serving")),
]


def _all_languages() -> list[tuple[str, str]]:
    """Every language Django knows (about 100), by English name. OCR reads only English
    and Romanian; other languages are recorded and typed in by hand."""
    from django.conf.locale import LANG_INFO

    langs = {
        code: info["name"]
        for code, info in LANG_INFO.items()
        if isinstance(info, dict) and "name" in info and len(code) <= 8
    }
    return sorted(langs.items(), key=lambda kv: kv[1].lower())


LANG_CHOICES = [("", _("Not specified")), *_all_languages(), ("other", _("Other"))]


class PhotoForm(forms.Form):
    photo = forms.FileField(
        label=_("Photo"),
        widget=forms.ClearableFileInput(attrs={"accept": "image/jpeg,image/png,image/webp"}),
    )
    kind = forms.ChoiceField(
        label=_("What does the photo show?"), choices=ContributionImage.Kind.choices
    )
    lang = forms.ChoiceField(label=_("Language on the label"), choices=LANG_CHOICES, required=False)


class StartForm(forms.Form):
    problem_type = forms.ChoiceField(choices=Contribution.ProblemType.choices)
    product = forms.UUIDField(required=False)
    code = forms.CharField(required=False, max_length=32)
    format = forms.CharField(required=False, max_length=16)


def _split_items(text: str) -> list[str]:
    parts = [p.strip().lower() for p in text.replace(";", ",").split(",")]
    return sorted({p for p in parts if p})[:40]


class ContributionForm(forms.Form):
    """Editable proposal. Only fields that differ from the current data are proposed."""

    name = forms.CharField(label=_("Product name"), required=False, max_length=300)
    brand = forms.CharField(label=_("Brand"), required=False, max_length=300)
    quantity = forms.CharField(label=_("Quantity (e.g. 400 g)"), required=False, max_length=100)
    serving_size = forms.CharField(
        label=_("Serving size as printed"), required=False, max_length=100
    )
    ingredients_text = forms.CharField(
        label=_("Ingredients (as printed)"),
        required=False,
        max_length=5000,
        widget=forms.Textarea(attrs={"rows": 5}),
    )
    ingredients_lang = forms.ChoiceField(
        label=_("Language of the ingredient list"), choices=LANG_CHOICES, required=False
    )
    allergens = forms.CharField(
        label=_("Allergens highlighted on the label (comma separated)"),
        required=False,
        max_length=500,
    )
    traces = forms.CharField(
        label=_("“May contain” statement, items as printed (comma separated)"),
        required=False,
        max_length=500,
    )
    packaging_nutriscore = forms.ChoiceField(
        label=_("Nutri-Score printed on the package"),
        required=False,
        choices=[
            ("", _("None / not printed")),
            ("a", "A"),
            ("b", "B"),
            ("c", "C"),
            ("d", "D"),
            ("e", "E"),
        ],
    )
    nutrition_basis = forms.ChoiceField(
        label=_("Nutrition values are"), choices=NUTRITION_BASES, required=False, initial="per_100g"
    )
    nutrition_prepared = forms.BooleanField(
        label=_("Values are for the prepared product"), required=False
    )
    note = forms.CharField(
        label=_("Anything else the moderators should know?"),
        required=False,
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    def __init__(self, *args: Any, current: dict[str, Any] | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.current = current or {}
        for n in NUTRIENTS:
            self.fields[f"n_{n.key}"] = forms.DecimalField(
                label=n.label,
                required=False,
                min_value=Decimal(0),
                max_digits=10,
                decimal_places=3,
                localize=False,
                widget=forms.TextInput(attrs={"inputmode": "decimal", "autocomplete": "off"}),
            )
            if len(n.allowed_units) > 1:
                self.fields[f"u_{n.key}"] = forms.ChoiceField(
                    label=_("unit"),
                    choices=[(u, u) for u in n.allowed_units],
                    required=False,
                    initial=n.unit,
                )

    def nutrient_fields(self):
        for n in NUTRIENTS:
            yield (
                n,
                self[f"n_{n.key}"],
                (self[f"u_{n.key}"] if f"u_{n.key}" in self.fields else None),
            )

    def proposed(self) -> dict[str, Any]:
        d = self.cleaned_data
        cur = self.current
        out: dict[str, Any] = {}
        for key in ("name", "brand", "quantity", "serving_size"):
            value = " ".join((d.get(key) or "").split()) or None
            if value and value != cur.get(key):
                out[key] = value
        text = (d.get("ingredients_text") or "").strip() or None
        if text and text != (cur.get("ingredients") or {}).get("text"):
            out["ingredients"] = {"text": text, "lang": d.get("ingredients_lang") or None}
        for key in ("allergens", "traces"):
            items = _split_items(d.get(key) or "")
            if items and items != (cur.get(key) or {}).get("items"):
                out[key] = {"status": "listed", "items": items}
        grade = d.get("packaging_nutriscore") or ""
        current_grade = (cur.get("packaging_nutriscore") or {}).get("grade")
        if grade and grade != current_grade:
            out["packaging_nutriscore"] = {"grade": grade, "version": None}
        facts = self._facts()
        if facts:
            basis, prep = facts[0]["basis"], facts[0]["preparation"]
            existing = [
                f
                for f in (cur.get("nutrition") or {}).get("facts") or []
                if not (f["basis"] == basis and f["preparation"] == prep)
            ]
            new = {"status": "available", "facts": sort_facts(existing + facts)}
            if new != cur.get("nutrition"):
                out["nutrition"] = new
        return out

    def _facts(self) -> list[dict[str, Any]]:
        d = self.cleaned_data
        basis = d.get("nutrition_basis") or "per_100g"
        prep = "prepared" if d.get("nutrition_prepared") else "as_sold"
        facts = []
        for n in NUTRIENTS:
            value = d.get(f"n_{n.key}")
            if value is None:
                continue  # empty field = unknown, never zero
            unit = d.get(f"u_{n.key}") or n.unit
            canonical = to_canonical_unit(value, unit, n)
            facts.append(
                {
                    "nutrient": n.key,
                    "basis": basis,
                    "preparation": prep,
                    "status": "declared",
                    "value": decimal_str(canonical),
                    "unit": n.unit,
                    "modifier": None,
                }
            )
        return facts


def initial_from(current: dict[str, Any], ocr_runs: list) -> dict[str, Any]:
    """Pre-fill the form from current data, then from finished OCR proposals."""
    initial: dict[str, Any] = {
        "name": current.get("name") or "",
        "brand": current.get("brand") or "",
        "quantity": current.get("quantity") or "",
        "serving_size": current.get("serving_size") or "",
        "ingredients_text": (current.get("ingredients") or {}).get("text") or "",
        "ingredients_lang": (current.get("ingredients") or {}).get("lang") or "",
        "allergens": ", ".join((current.get("allergens") or {}).get("items") or []),
        "traces": ", ".join((current.get("traces") or {}).get("items") or []),
        "packaging_nutriscore": (current.get("packaging_nutriscore") or {}).get("grade") or "",
        "nutrition_basis": "per_100g",
    }
    for run in ocr_runs:
        if run.status != "done":
            continue
        if run.image.kind == "ingredients" and run.text and not initial["ingredients_text"]:
            initial["ingredients_text"] = run.text
            initial["ingredients_lang"] = run.image.lang or ""
        if run.image.kind == "nutrition" and run.parsed:
            if run.parsed.get("basis") in ("per_100g", "per_100ml", "per_serving"):
                initial["nutrition_basis"] = run.parsed["basis"]
            for p in run.parsed.get("proposals", []):
                if p.get("nutrient") and p.get("value") is not None:
                    initial[f"n_{p['nutrient']}"] = p["value"]
                    if p.get("unit") in ("g", "mg"):
                        initial[f"u_{p['nutrient']}"] = p["unit"]
    return initial


def initial_from_proposal(proposed: dict[str, Any]) -> dict[str, Any]:
    """Re-open the check step with values the contributor already confirmed."""
    initial: dict[str, Any] = {}
    for key in ("name", "brand", "quantity", "serving_size"):
        if proposed.get(key):
            initial[key] = proposed[key]
    if proposed.get("ingredients"):
        initial["ingredients_text"] = proposed["ingredients"].get("text") or ""
        initial["ingredients_lang"] = proposed["ingredients"].get("lang") or ""
    for key in ("allergens", "traces"):
        if proposed.get(key):
            initial[key] = ", ".join(proposed[key].get("items") or [])
    if proposed.get("packaging_nutriscore"):
        initial["packaging_nutriscore"] = proposed["packaging_nutriscore"].get("grade") or ""
    return initial


def proposal_summary(proposed: dict[str, Any]) -> list[tuple[str, str]]:
    """Readable list of the proposed changes for the final step."""
    from catalog.nutrition import BASIS_LABELS, NUTRIENT_BY_KEY
    from catalog.presenters import FIELD_LABELS, format_value

    rows: list[tuple[str, str]] = []
    for key, value in proposed.items():
        label = FIELD_LABELS.get(key, key)
        if key == "ingredients":
            rows.append((label, value.get("text") or ""))
        elif key in ("allergens", "traces"):
            rows.append((label, ", ".join(value.get("items") or [])))
        elif key == "packaging_nutriscore":
            rows.append((label, (value.get("grade") or "").upper()))
        elif key == "nutrition":
            facts = value.get("facts") or []
            text = "; ".join(
                f"{NUTRIENT_BY_KEY[f['nutrient']].label} {format_value(f['value'], f['unit'])} "
                f"({BASIS_LABELS[f['basis']]})"
                for f in facts
                if f["status"] == "declared"
            )
            rows.append((label, text))
        else:
            rows.append((label, str(value)))
    return rows
