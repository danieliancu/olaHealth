"""The wording of answers written from data (``myola.services.rag``), in English and Romanian.

These answers carry the values and product names of the database. They are written here in
the question's language instead of being translated by the AI provider, which once turned
"less sugar" into "mai puțină zaharină" (a sweetener). Other languages get the English text,
translated like the app's rule answers.
"""

from __future__ import annotations

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
        "tally_unknown": "; {unknown} have no comparable {what} value",
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
        "tally_unknown": "; {unknown} nu au o valoare comparabilă pentru {what}",
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


def lang_of(code: str | None) -> str:
    """The language the data answers are written in ("en" for any other language)."""
    return code if code in LANGS else "en"


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
