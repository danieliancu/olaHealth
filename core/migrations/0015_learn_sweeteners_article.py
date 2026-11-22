"""Magazine: a new featured article in "Ingredients and additives" (sweeteners on the label)."""

from django.db import migrations
from django.utils import timezone

SLUG = "sweeteners-on-the-label"

ARTICLE = {
    "title": "Sweeteners on the label",
    "summary": (
        "Aspartame, sucralose, stevia, xylitol: how sweeteners appear in the ingredients list, "
        "what “with sweeteners” and “a source of phenylalanine” mean, and why a product can "
        "say 0 g sugars and still taste sweet."
    ),
    "pull_quote": "“0 g sugars” and sweet: the sweetness comes from somewhere. Read the list.",
    "minutes": 4,
    "featured": True,
    "order": 2,
    "image_alt": "A hand adding a sweetener tablet to a cup of tea",
    "seo_title": "Sweeteners on food labels: names, E numbers and warnings",
    "meta_description": (
        "How sweeteners appear on UK food labels: E numbers, “with sweeteners”, phenylalanine "
        "and laxative warnings, and what 0 g sugars really means."
    ),
    "body": """Sweeteners give a sweet taste with few or no calories. They are **food additives**, so they are assessed and authorised before use, and they must be named in the ingredients list, either by name (sucralose) or by E number (E955).

## The names you will meet
Most sweeteners have an E number between **E950 and E969**:
- E950 acesulfame K
- E951 aspartame
- E952 cyclamates
- E954 saccharin
- E955 sucralose
- E960 steviol glycosides (from the stevia plant)

A second family are the **polyols** (sugar alcohols), used in sugar-free sweets and chewing gum: E420 sorbitol, E421 mannitol, E953 isomalt, E965 maltitol, E966 lactitol, E967 xylitol and E968 erythritol.

## Three lines the law asks for
- **“With sweetener(s)”** next to the product name, or **“with sugar(s) and sweetener(s)”** when it contains both.
- **“Contains a source of phenylalanine”** when a product uses aspartame. This matters for people with phenylketonuria (PKU), who must limit phenylalanine.
- **“Excessive consumption may produce laxative effects”** when polyols make up more than 10% of the product.

## Why “0 g sugars” can still taste sweet
“Sugars” in the nutrition table counts sugars only, not sweeteners. A drink with 0 g sugars can be sweetened with sucralose or acesulfame K. Polyols are counted in carbohydrate and still provide some energy, about 2.4 kcal per gram (erythritol close to none), less than sugar's 4.

## Reading it calmly
Authorised sweeteners have an acceptable daily intake set by food safety experts, well above what most people eat. If you prefer to avoid one, add a note in My account, or put products with it on your Avoid list; the ingredients on each product page show which sweetener is used. For more on additive codes, see [E numbers without the panic](/learn/additives/); for sugar's many names, [Spotting sugar under other names](/learn/hidden-sugar/). For questions about your health, ask a professional.""",
}


def forwards(apps, schema_editor):
    Topic = apps.get_model("core", "LearnTopic")
    Article = apps.get_model("core", "LearnArticle")
    topic = Topic.objects.filter(key="ingredients").first()
    if topic is None:
        return
    Article.objects.get_or_create(
        slug=SLUG,
        defaults={
            **ARTICLE,
            "topic": topic,
            "image_static": "img/learn/sweeteners.webp",
            "published": True,
            "published_at": timezone.now(),
        },
    )


def backwards(apps, schema_editor):
    apps.get_model("core", "LearnArticle").objects.filter(slug=SLUG).delete()


class Migration(migrations.Migration):
    dependencies = [("core", "0014_learn_featured_help")]
    operations = [migrations.RunPython(forwards, backwards)]
