"""Food words of the app's languages that the OFF taxonomy does not name (for example many
Romanian names), mapped to the English word the taxonomy and product names use.

Data, not rules: ``catalog.services.concepts`` reads a question's words through this table and
then matches them against category names in every language. To add a word, add a line; forms
with and without diacritics, singular and plural, are listed explicitly.
"""

from __future__ import annotations

RO_EN: dict[str, str] = {
    # dairy and alternatives
    "lapte": "milk", "laptele": "milk", "iaurt": "yogurt", "iaurturi": "yogurt",
    "iaurtul": "yogurt", "brânză": "cheese", "branza": "cheese", "brânzeturi": "cheese",
    "branzeturi": "cheese", "unt": "butter", "untul": "butter", "smântână": "cream",
    "smantana": "cream", "frișcă": "cream", "frisca": "cream", "chefir": "kefir",
    "kefir": "kefir", "vegetal": "plant", "vegetală": "plant", "vegetala": "plant",
    "vegetale": "plant", "băutură": "drink", "bautura": "drink", "băuturi": "drink",
    "bauturi": "drink",
    # nuts, seeds, fruit, vegetables
    "cocos": "coconut", "migdale": "almond", "migdal": "almond", "ovăz": "oat", "ovaz": "oat",
    "soia": "soy", "alune": "hazelnut", "arahide": "peanut", "nuci": "nut", "nucă": "nut",
    "nuca": "nut", "fructe": "fruit", "fruct": "fruit", "legume": "vegetable",
    "portocale": "orange", "portocală": "orange", "portocala": "orange", "mere": "apple",
    "măr": "apple", "mar": "apple", "banane": "banana", "căpșuni": "strawberry",
    "capsuni": "strawberry", "struguri": "grape", "lămâie": "lemon", "lamaie": "lemon",
    "roșii": "tomato", "rosii": "tomato", "cartofi": "potato", "mazăre": "pea",
    "mazare": "pea", "fasole": "bean", "linte": "lentil", "năut": "chickpea",
    "naut": "chickpea", "măsline": "olive", "masline": "olive", "ciuperci": "mushroom",
    # fish, meat, eggs
    "pește": "fish", "peste": "fish", "pești": "fish", "pesti": "fish", "peşte": "fish",
    "ton": "tuna", "somon": "salmon", "sardine": "sardine", "macrou": "mackerel",
    "hering": "herring", "carne": "meat", "pui": "chicken", "porc": "pork", "vită": "beef",
    "vita": "beef", "șuncă": "ham", "sunca": "ham", "cârnați": "sausage",
    "carnati": "sausage", "ou": "egg", "ouă": "egg", "oua": "egg",
    # cereals, bakery, sweet
    "pâine": "bread", "paine": "bread", "cereale": "cereal", "orez": "rice",
    "paste": "pasta", "făină": "flour", "faina": "flour", "biscuiți": "biscuit",
    "biscuiti": "biscuit", "ciocolată": "chocolate", "ciocolata": "chocolate",
    "miere": "honey", "gem": "jam", "dulceață": "jam", "dulceata": "jam",
    "înghețată": "ice-cream", "inghetata": "ice-cream", "chipsuri": "crisp",
    # drinks, oils, other
    "suc": "juice", "sucuri": "juice", "apă": "water", "apa": "water", "cafea": "coffee",
    "ceai": "tea", "vin": "wine", "bere": "beer", "ulei": "oil", "uleiuri": "oil",
    "sos": "sauce", "sosuri": "sauce", "supă": "soup", "supa": "soup", "pizza": "pizza",
    "humus": "hummus", "tofu": "tofu",
}  # fmt: skip
