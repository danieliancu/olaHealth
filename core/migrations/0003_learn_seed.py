"""Seed the Learn CMS with the first topics and articles (previously static templates)."""

from django.db import migrations

TOPICS = [
    ("labels", "Reading the label", "scan-barcode", 1),
    ("scores", "Scores and processing", "chart-line", 2),
    ("ingredients", "Ingredients and additives", "flask-conical", 3),
    ("allergens", "Allergens", "shield-alert", 4),
]

ARTICLES = [
    {
        "slug": "read-a-label", "topic": "labels", "order": 1, "minutes": 4, "featured": True,
        "title": "How to read a food label in one minute",
        "summary": "Where to look first, and what the per 100 g column is for.",
        "body": """A label has more on it than you need at once. Three places answer most questions.

## 1. The ingredients list
Ingredients are listed from the largest amount to the smallest. If sugar, salt or a fat is among the first three, it is a large part of the product. A short list of words you recognise usually means a simpler food.

## 2. The nutrition table, per 100 g
Compare products using the **per 100 g** (or per 100 ml) column, not the per-portion one: portions differ between brands, 100 g does not. AskFoodo shows the same per 100 g values, so a comparison is fair.

## 3. Allergens
The 14 major allergens are highlighted inside the ingredients list, usually in bold. A separate line such as “may contain nuts” is a precaution about traces (see [the article on allergens](/learn/allergens-traces/)).

## Then scan it
Scanning the barcode opens the same values in a calmer layout, with Nutri-Score, processing and your own restrictions checked against the ingredients. When the app and the package disagree, trust the package.""",
    },
    {
        "slug": "hidden-sugar", "topic": "labels", "order": 2, "minutes": 3, "featured": False,
        "title": "Spotting sugar under other names",
        "summary": "Syrups, dextrose and friends: how sugar shows up in the ingredients.",
        "body": """The nutrition table shows **total sugars** per 100 g. The ingredients list shows *where* they come from, and sugar has many names.

## Names to recognise
- Syrups: glucose syrup, glucose-fructose syrup, corn syrup, rice syrup, agave syrup.
- Words ending in “-ose”: dextrose, fructose, maltose, sucrose.
- Others: honey, invert sugar, maltodextrin, fruit juice concentrate.

## Why it matters
When several of these appear separately, each can sit lower in the list even though together they are a large share. Adding them up in your head gives a truer picture.

## Natural sugars
Plain milk and whole fruit contain sugars of their own. The table cannot tell them apart from added sugar, but the ingredients list can: if none of the names above appears, the sugar comes from the food itself.""",
    },
    {
        "slug": "nutri-score", "topic": "scores", "order": 1, "minutes": 4, "featured": True,
        "title": "Nutri-Score, explained",
        "summary": "What the A to E letter measures, and what it does not.",
        "body": """Nutri-Score is a five-letter scale, from **A** (dark green) to **E** (red), printed on the front of many packages in Europe.

## What it measures
It weighs, per 100 g, things to limit (energy, sugars, saturated fat, salt) against things to favour (fibre, protein, fruit, vegetables, legumes, nuts). The result is one letter.

## How to use it
It works best for **comparing similar products**: two breakfast cereals, two yoghurts, two sauces. It is less useful between different kinds of food: an olive oil and a soft drink are not alternatives to each other.

## What it does not say
- It does not look at processing or additives (see [NOVA](/learn/nova/)).
- It does not know your allergies, diet or health conditions.
- It does not judge a whole meal or a whole day.

When a product has no official score, AskFoodo says so instead of guessing.""",
    },
    {
        "slug": "nova", "topic": "scores", "order": 2, "minutes": 4, "featured": False,
        "title": "NOVA and ultra-processed food",
        "summary": "The four processing groups and how to recognise group 4.",
        "body": """NOVA sorts foods into four groups by **how they are processed**, not by their nutrients.

1. **Unprocessed or minimally processed:** fruit, vegetables, eggs, plain milk, dried beans.
2. **Processed culinary ingredients:** oils, butter, sugar, salt.
3. **Processed foods:** foods from groups 1 and 2 combined, such as cheese, bread, canned vegetables.
4. **Ultra-processed foods:** industrial recipes with ingredients you rarely use at home.

## Recognising group 4
Look for ingredients that a home kitchen would not have: flavourings, emulsifiers, sweeteners, colours, modified starches, protein isolates. Their presence, not the length of the list, is what places a product in group 4.

## Keep it in proportion
NOVA describes a product; it is not a grade for you. It is one more piece of information next to Nutri-Score and the label itself.""",
    },
    {
        "slug": "additives", "topic": "ingredients", "order": 1, "minutes": 3, "featured": True,
        "title": "E numbers without the panic",
        "summary": "What E numbers are, why they are listed and where to read more.",
        "body": """An **E number** is a code for an additive that has been assessed and authorised for use in the European Union. The “E” stands for Europe.

## Why they are listed
Additives keep food safe for longer, hold textures together, or change colour and taste. They must be named on the label, either by code (E330) or by name (citric acid).

## Families at a glance
- E100–E199: colours
- E200–E299: preservatives
- E300–E399: antioxidants and acidity regulators
- E400–E499: thickeners, stabilisers, emulsifiers
- E900 and above: sweeteners, glazing agents and others

## Reading more calmly
Many E numbers are familiar substances (E300 is vitamin C). If you want to avoid a specific additive, add a note in My Track and check the ingredients on each product page. For health questions about additives, ask a professional.""",
    },
    {
        "slug": "allergens-traces", "topic": "allergens", "order": 1, "minutes": 4, "featured": False,
        "title": "Allergens and “may contain”",
        "summary": "Bold allergens, precautionary labels and why we never say “safe”.",
        "body": """In the EU, 14 major allergens must be **emphasised in the ingredients list**, usually in bold: cereals with gluten, crustaceans, eggs, fish, peanuts, soy, milk, tree nuts, celery, mustard, sesame, sulphites, lupin and molluscs.

## “May contain”
A line like “may contain traces of nuts” is a voluntary precaution: the product does not include the allergen as an ingredient, but it may meet it during production. For a severe allergy, treat it as a real risk.

## How AskFoodo shows it
For your restrictions, the product page shows one of: contains, may contain (traces), not declared, or cannot be confirmed. It never says a product is “safe”: data can be incomplete or out of date, and recipes change.

## The rule that always applies
Read the physical package before eating, every time, especially after a product changes its look.""",
    },
]


def seed(apps, schema_editor):
    Topic = apps.get_model("core", "LearnTopic")
    Article = apps.get_model("core", "LearnArticle")
    topics = {}
    for key, title, icon, order in TOPICS:
        topics[key], _ = Topic.objects.get_or_create(
            key=key, defaults={"title": title, "icon": icon, "order": order}
        )
    for a in ARTICLES:
        data = dict(a)
        slug, topic = data.pop("slug"), data.pop("topic")
        Article.objects.get_or_create(
            slug=slug,
            defaults={**data, "topic": topics[topic], "image_static": f"img/learn/{slug}.webp"},
        )


def unseed(apps, schema_editor):
    apps.get_model("core", "LearnArticle").objects.filter(slug__in=[a["slug"] for a in ARTICLES]).delete()
    apps.get_model("core", "LearnTopic").objects.filter(key__in=[t[0] for t in TOPICS]).delete()


class Migration(migrations.Migration):
    dependencies = [("core", "0002_learn_cms")]
    operations = [migrations.RunPython(seed, unseed)]
