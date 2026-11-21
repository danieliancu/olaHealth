"""First pull quotes for the seeded Learn articles (only where none is set)."""

from django.db import migrations

QUOTES = {
    "read-a-label": "Compare per 100 g, never per portion: portions change, 100 g does not.",
    "hidden-sugar": "Sugar has many names. Add them up, and the label tells the truth.",
    "nutri-score": "A letter to compare similar foods, not a verdict on your plate.",
    "nova": "It describes how a food is made, not who you are.",
    "additives": "Many E numbers are familiar things with a code: E300 is vitamin C.",
    "allergens-traces": "We never say safe. The package in your hand is always the reference.",
}


def seed(apps, schema_editor):
    Article = apps.get_model("core", "LearnArticle")
    for slug, quote in QUOTES.items():
        Article.objects.filter(slug=slug, pull_quote="").update(pull_quote=quote)


class Migration(migrations.Migration):
    dependencies = [("core", "0006_learn_pull_quote")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
