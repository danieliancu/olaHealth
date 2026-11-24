"""The wording of answers written from data (``myola.services.rag``), in English and Romanian.

These answers carry the values and product names of the database. They are written here in
the question's language instead of being translated by the AI provider, which once turned
"less sugar" into "mai puțină zaharină" (a sweetener). Other languages get the English text,
translated like the app's rule answers.
"""

from __future__ import annotations

import re
from decimal import Decimal

LANGS = ("en", "ro")

NUTRIENT = {
    "en": {
        "salt": "salt", "sugars": "sugar", "saturated-fat": "saturated fat", "fat": "fat",
        "energy-kcal": "energy (kcal)", "fiber": "fibre", "proteins": "protein",
        "foodo": "Foodo Score",
    },
    "ro": {
        "salt": "sare", "sugars": "zahăr", "saturated-fat": "grăsimi saturate",
        "fat": "grăsimi", "energy-kcal": "energie (kcal)", "fiber": "fibre",
        "proteins": "proteine", "foodo": "Foodo Score",
    },
}  # fmt: skip
# "less salt", "mai puțină sare"…: (lower, higher) per nutrient.
CRITERION = {
    "en": {
        "foodo": (None, "a higher Foodo Score"),
    },
    "ro": {
        "salt": ("mai puțină sare", "mai multă sare"),
        "sugars": ("mai puțin zahăr", "mai mult zahăr"),
        "saturated-fat": ("mai puține grăsimi saturate", "mai multe grăsimi saturate"),
        "fat": ("mai puține grăsimi", "mai multe grăsimi"),
        "energy-kcal": ("mai puține calorii", "mai multe calorii"),
        "fiber": ("mai puține fibre", "mai multe fibre"),
        "proteins": ("mai puține proteine", "mai multe proteine"),
        "foodo": (None, "un Foodo Score mai mare"),
    },
}
BASIS = {
    "en": {"per_100g": "per 100 g", "per_100ml": "per 100 ml"},
    "ro": {"per_100g": "la 100 g", "per_100ml": "la 100 ml"},
}
COUNTRY_RO = {
    "en:united-kingdom": "Regatul Unit", "en:romania": "România", "en:germany": "Germania",
    "en:france": "Franța", "en:belgium": "Belgia", "en:netherlands": "Țările de Jos",
    "en:italy": "Italia", "en:spain": "Spania", "en:united-states": "SUA",
    "en:bulgaria": "Bulgaria", "en:croatia": "Croația", "en:canada": "Canada",
    "en:switzerland": "Elveția", "en:austria": "Austria", "en:poland": "Polonia",
    "en:hungary": "Ungaria", "en:ireland": "Irlanda", "en:portugal": "Portugalia",
}  # fmt: skip

T = {
    "en": {
        "open_first": "Open or scan a product first: I compare other products with it.",
        "no_category": "This product's category is not known, so I can't compare it with others.",
        "no_foodo": "This product has no Foodo Score, so I can't compare it with others.",
        "no_value": "This product's label gives no {what} value per 100 g or 100 ml, so I "
                    "can't compare it with others.",
        "scope": "the “{category}” category",
        "scope_any": "this category",
        "sold_in": " sold in {markets}",
        "nothing_to_compare": "There is no other product in {scope} in the catalogue to "
                              "compare with.",
        "not_enough": "Not enough data: none of the {n} other products in {scope} has a "
                      "comparable {what} value, so I can't tell which has {label}.",
        "tally": "Compared {compared} of {n} products",
        "tally_one": "Compared {compared} of 1 product",
        "tally_unknown": "; {unknown} have no comparable {what} value",
        "tally_unknown_one": "; 1 has no comparable {what} value",
        "no_match": "No match in the verified catalogue: no other product in {scope} has "
                    "{label} than this one ({ref}). {tally}.",
        "with": "With {label} than this one ({ref}), in {scope}:",
        "value": "{What}: {value} {basis} (as sold).",
        "value_serving": "The label gives {what} only per serving ({value}), not per 100 g "
                         "or 100 ml.",
        "value_none": "This product's label gives no {what} value.",
        "which_product": "For which product? Compare products or ask about a value first, "
                         "then ask for another amount.",
        "no_base": "The previous answer has no value per 100 g or 100 ml to work from.",
        "other_unit": "These values are per 100 {per}. For which amount in {per}?",
        "amount": "{What} in {size} {unit} (from the values per 100 {unit}):",
        "magazine": "From the Magazine, “{title}”: {excerpt}",
        "no_article": "The Magazine has no article that explains this.",
        "or": " or ",
        "unnamed": "Unnamed product",
        "rec_head": "“{kind}” sold in {where} with {label} in the AskFoodo catalogue:",
        "rec_most": "the most {what}",
        "rec_least": "the least {what}",
        "rec_foodo": "the highest Foodo Score",
        "rec_no_value": "No product in “{kind}” sold in {where} has a declared {what} value "
                        "per 100 g in the AskFoodo catalogue.",
        "rec_missing": "The AskFoodo catalogue has no {what} values for “{kind}” sold in "
                       "{where}, so I can't confirm which has the most.",
        "rec_category": "Open Food Facts files these as “{category}”; their total fat per "
                        "100 g (total fat, not {what}):",
        "rec_fat": "{value} total fat per 100 g",
        "rec_none": "There is no product in “{kind}” sold in {where} in the AskFoodo catalogue.",
        "rec_context": "About {what}, from your earlier message:",
    },
    "ro": {
        "open_first": "Deschide sau scanează mai întâi un produs: compar alte produse cu el.",
        "no_category": "Nu se știe categoria acestui produs, așa că nu îl pot compara cu altele.",
        "no_foodo": "Acest produs nu are Foodo Score, așa că nu îl pot compara cu altele.",
        "no_value": "Eticheta acestui produs nu dă valoarea pentru {what} la 100 g sau la "
                    "100 ml, așa că nu îl pot compara cu altele.",
        "scope": "categoria „{category}”",
        "scope_any": "această categorie",
        "sold_in": " vândută în {markets}",
        "nothing_to_compare": "Nu există alt produs în {scope} în catalog cu care să-l compar.",
        "not_enough": "Date insuficiente: niciunul dintre cele {n} alte produse din {scope} "
                      "nu are o valoare comparabilă pentru {what}, așa că nu pot spune care "
                      "are {label}.",
        "tally": "Am comparat {compared} din {n} produse",
        "tally_one": "Am comparat {compared} din 1 produs",
        "tally_unknown": "; {unknown} nu au o valoare comparabilă pentru {what}",
        "tally_unknown_one": "; unul nu are o valoare comparabilă pentru {what}",
        "no_match": "Nicio potrivire în catalogul verificat: niciun alt produs din {scope} nu "
                    "are {label} decât acesta ({ref}). {tally}.",
        "with": "Cu {label} decât acesta ({ref}), din {scope}:",
        "value": "{What}: {value} {basis} (așa cum se vinde).",
        "value_serving": "Eticheta dă {what} doar pe porție ({value}), nu la 100 g sau la "
                         "100 ml.",
        "value_none": "Eticheta acestui produs nu dă valoarea pentru {what}.",
        "which_product": "Pentru care produs? Compară mai întâi produse sau întreabă de o "
                         "valoare, apoi cere altă cantitate.",
        "no_base": "Răspunsul anterior nu are o valoare la 100 g sau la 100 ml de la care "
                   "să pornesc.",
        "other_unit": "Aceste valori sunt la 100 {per}. Pentru ce cantitate în {per}?",
        "amount": "{What} în {size} {unit} (din valorile la 100 {unit}):",
        "magazine": "Din Magazine, „{title}”: {excerpt}",
        "no_article": "Magazine nu are un articol care să explice asta.",
        "or": " sau ",
        "unnamed": "Produs fără nume",
        "rec_head": "Din „{kind}” vândute în {where}, cu {label}, în catalogul AskFoodo:",
        "rec_most": "cea mai mare cantitate de {what}",
        "rec_least": "cea mai mică cantitate de {what}",
        "rec_foodo": "cel mai mare Foodo Score",
        "rec_no_value": "Niciun produs din „{kind}” vândut în {where} nu are în catalogul "
                        "AskFoodo o valoare declarată pentru {what} la 100 g.",
        "rec_missing": "Catalogul AskFoodo nu are valori pentru {what} la „{kind}” vândute în "
                       "{where}, așa că nu pot confirma care are cel mai mult.",
        "rec_category": "Open Food Facts le încadrează la „{category}”; grăsimile lor totale "
                        "la 100 g (grăsimi totale, nu {what}):",
        "rec_fat": "{value} grăsimi totale la 100 g",
        "rec_none": "Nu există niciun produs din „{kind}” vândut în {where} în catalogul "
                    "AskFoodo.",
        "rec_context": "Despre {what}, din mesajul tău anterior:",
    },
}  # fmt: skip


def fallback() -> str:
    """The language answers fall back to when the question's language has no templates and
    the AI cannot phrase them (``ASK_FOODO_FALLBACK_LANGUAGE``, one of ``LANGS``)."""
    from django.conf import settings

    code = getattr(settings, "ASK_FOODO_FALLBACK_LANGUAGE", "en")
    return code if code in LANGS else "en"


def lang_of(code: str | None) -> str:
    """The language code-written answers are written in: the answer's language when it has
    templates, else the fallback language (translated afterwards when the AI can)."""
    return code if code in LANGS else fallback()


def t(lang: str, key: str, **values) -> str:
    return T[lang_of(lang)][key].format(**values)


def nutrient(lang: str, key: str) -> str:
    return NUTRIENT[lang_of(lang)][key]


def criterion(lang: str, key: str, direction: str) -> str:
    lang = lang_of(lang)
    if lang == "en" and key != "foodo":
        return f"{'less' if direction == 'lower' else 'more'} {NUTRIENT['en'][key]}"
    lower, higher = CRITERION[lang][key]
    return (lower if direction == "lower" else higher) or higher


def basis(lang: str, key: str) -> str:
    return BASIS[lang_of(lang)].get(key, key)


def number(value: Decimal | str, unit: str, lang: str) -> str:
    """ "1.25 g" / "1,25 g": at most 2 decimals, no trailing zeros."""
    d = Decimal(str(value)).quantize(Decimal("0.01")).normalize()
    text = format(d, "f")
    if lang_of(lang) == "ro":
        text = text.replace(".", ",")
    return f"{text} {unit}".strip()


def market(tag: str, lang: str) -> str:
    if lang_of(lang) == "ro" and tag in COUNTRY_RO:
        return COUNTRY_RO[tag]
    from myola.services.market import name

    return str(name(tag))


def markets(tags: list[str], lang: str) -> str:
    names = [market(tag, lang) for tag in tags]
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + T[lang_of(lang)]["or"] + names[-1]


def capital(text: str) -> str:
    return text[:1].upper() + text[1:]


# ---------- Rule answers (myola.services.chat.rule_answer) ----------
# Only the answers that hold no free English text are written here; those quoting restriction
# reasons, memories or preferences keep the English text and the translation path.
RULE_LANGS_INTENTS = frozenset({None, "values", "indicators", "avoid", "scans"})

VALUE_NAMES = {
    "en": {"energy-kcal": "Energy", "sugars": "Sugars", "saturated-fat": "Saturates",
           "salt": "Salt", "proteins": "Protein", "fiber": "Fibre"},
    "ro": {"energy-kcal": "Energie", "sugars": "Zaharuri", "saturated-fat": "Grăsimi saturate",
           "salt": "Sare", "proteins": "Proteine", "fiber": "Fibre"},
}  # fmt: skip
PROCESSING = {
    "en": {1: "unprocessed or minimally processed", 2: "processed culinary ingredient",
           3: "processed food", 4: "ultra-processed"},
    "ro": {1: "neprocesat sau minim procesat", 2: "ingredient culinar procesat",
           3: "aliment procesat", 4: "ultraprocesat"},
}  # fmt: skip
SCORE_MESSAGES_RO = {
    "Not enough data": "nu sunt destule date",
    "Doesn't apply to this product": "nu se aplică acestui produs",
    "Data being checked": "datele sunt în verificare",
    "Not available for this kind of product yet": "nu este încă disponibil pentru acest tip "
                                                  "de produs",
}  # fmt: skip
SUGGESTION_LABELS_RO = {
    "values": "Care sunt valorile principale?",
    "indicators": "Ce înseamnă Foodo Score și procesarea?",
    "restrictions": "Se potrivește restricțiilor mele?",
    "remember": "Ce îți amintești?",
    "avoid": "Ce am marcat „De evitat”?",
    "scans": "Ce am scanat recent?",
    "prefs": "Care sunt preferințele mele?",
}
MONTHS_RO = ("ian.", "feb.", "mar.", "apr.", "mai", "iun.", "iul.", "aug.", "sept.", "oct.",
             "nov.", "dec.")  # fmt: skip

T["en"].update({
    "ind_open": "Open a product and ask there: I explain that product's indicators.",
    "foodo": "Foodo Score {score}: AskFoodo's conversion of the official Nutri-Score from Open "
             "Food Facts to a 0-100 scale (100 is the most favourable), per 100 g or 100 ml.",
    "no_foodo": "No Foodo Score for this product: {why}.",
    "processing": "Processing: {label}, judged from the ingredient list. It says nothing about "
                  "sugar, fat or salt.",
    "no_processing": "The source does not say how processed it is.",
    "val_open": "Open a product and ask there: I read that product's label values.",
    "val_none": "No values per 100 g or 100 ml are recorded for this product.",
    "val_line": "{Basis}: {values}.",
    "val_missing": "Not recorded: {names}.",
    "avoid_list": "You marked as Avoid: {names}.",
    "avoid_none": "You haven't marked any product as Avoid.",
    "scans_list": "Your recent scans: {items}. A scan is not a purchase.",
    "scans_none": "No scans recorded yet.",
    "limit": "I can't answer that yet: I only use the facts shown in the app, Open Food Facts "
             "and what you saved in your account. You can also ask me to find a product by "
             "name or barcode. Try: {options}.",
    "notes": "From your notes: {notes}.",
    "unnamed_product": "Unnamed product",
    "from_type": " (from the product type)",
    "in_english": "",
})  # fmt: skip
T["ro"].update({
    "ind_open": "Deschide un produs și întreabă acolo: îți explic indicatorii acelui produs.",
    "foodo": "Foodo Score {score}: conversia făcută de AskFoodo a Nutri-Score-ului oficial din "
             "Open Food Facts pe o scară de la 0 la 100 (100 este cel mai favorabil), la 100 g "
             "sau 100 ml.",
    "no_foodo": "Acest produs nu are Foodo Score: {why}.",
    "processing": "Procesare: {label}, apreciată după lista de ingrediente. Nu spune nimic "
                  "despre zahăr, grăsimi sau sare.",
    "no_processing": "Sursa nu spune cât de procesat este.",
    "val_open": "Deschide un produs și întreabă acolo: îți citesc valorile de pe eticheta lui.",
    "val_none": "Pentru acest produs nu sunt înregistrate valori la 100 g sau la 100 ml.",
    "val_line": "{Basis}: {values}.",
    "val_missing": "Neînregistrate: {names}.",
    "avoid_list": "Ai marcat „De evitat”: {names}.",
    "avoid_none": "Nu ai marcat niciun produs „De evitat”.",
    "scans_list": "Scanările tale recente: {items}. O scanare nu înseamnă o cumpărare.",
    "scans_none": "Nu ai încă nicio scanare.",
    "limit": "Încă nu pot răspunde la asta: folosesc doar ce arată aplicația, Open Food Facts "
             "și ce ai salvat în cont. Îmi poți cere și să caut un produs după nume sau cod de "
             "bare. Încearcă: {options}.",
    "notes": "Din notițele tale: {notes}.",
    "unnamed_product": "Produs fără nume",
    "from_type": " (dedus din tipul produsului)",
    "in_english": " (citat în engleză)",
})  # fmt: skip


def quote(lang: str, text: str) -> str:
    return f"„{text}”" if lang_of(lang) == "ro" else f"“{text}”"


def day(lang: str, value) -> str:
    """ "24 Nov" / "24 nov."."""
    if lang_of(lang) == "ro":
        return f"{value.day} {MONTHS_RO[value.month - 1]}"
    from django.utils.formats import date_format

    return date_format(value, "j M")


def score_message(lang: str, message: str) -> str:
    text = (message or "Not enough data").rstrip(".")
    if lang_of(lang) == "ro":
        return SCORE_MESSAGES_RO.get(text, text)
    return text.lower()


def suggestion(lang: str, key: str, label: str) -> str:
    return SUGGESTION_LABELS_RO.get(key, label) if lang_of(lang) == "ro" else label


def localized_display(display: str, lang: str) -> str:
    """A value as the label page shows it ("1.5 g", "<0.5 g") in the answer's language."""
    if lang_of(lang) != "ro":
        return display
    return re.sub(r"(\d)\.(\d)", r"\1,\2", display)


def basis_label(lang: str, basis: str, inferred: bool) -> str:
    text = BASIS[lang_of(lang)].get(basis, "")
    return text + (T[lang_of(lang)]["from_type"] if inferred and text else "")
