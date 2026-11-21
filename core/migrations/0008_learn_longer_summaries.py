"""Longer summaries for the seeded Learn articles (only where the original one is unchanged)."""

from django.db import migrations

SUMMARIES = {
    "read-a-label": (
        "Where to look first, and what the per 100 g column is for.",
        "A label holds more than you need at once. Three places answer most questions: the "
        "ingredients list, the nutrition table per 100 g and the allergens. Here is how to "
        "read them in under a minute, and when to trust the package over any app.",
    ),
    "hidden-sugar": (
        "Syrups, dextrose and friends: how sugar shows up in the ingredients.",
        "Syrups, dextrose and friends: sugar goes by many names, and each one can sit lower in "
        "the list than the total really deserves. Learn the names to look for, how to add them "
        "up in your head, and how to tell added sugar from the sugar a food naturally has.",
    ),
    "nutri-score": (
        "What the A to E letter measures, and what it does not.",
        "What the A to E letter measures, how it weighs sugars, salt and fat against fibre, "
        "protein and vegetables, and why it works best for comparing two similar products. "
        "Also what it cannot tell you: processing, additives, or your own allergies and diet.",
    ),
    "nova": (
        "The four processing groups and how to recognise group 4.",
        "NOVA sorts foods by how they are made, not by their nutrients. Meet the four groups, "
        "learn the ingredients that give an ultra-processed product away, and see why the "
        "group describes a product rather than judging you.",
    ),
    "additives": (
        "What E numbers are, why they are listed and where to read more.",
        "What an E number really is, why additives must appear on the label and what the main "
        "families do, from colours to emulsifiers. A calm guide that shows how many codes are "
        "familiar things, such as E300, which is simply vitamin C.",
    ),
    "allergens-traces": (
        "Bold allergens, precautionary labels and why we never say “safe”.",
        "The 14 major allergens are highlighted in bold in the ingredients, and “may contain” "
        "is a precaution about traces. Here is how to read both, what AskFoodo shows for your "
        "own restrictions, and why we never call a product “safe”.",
    ),
}


def forwards(apps, schema_editor):
    Article = apps.get_model("core", "LearnArticle")
    for slug, (old, new) in SUMMARIES.items():
        Article.objects.filter(slug=slug, summary=old).update(summary=new)


def backwards(apps, schema_editor):
    Article = apps.get_model("core", "LearnArticle")
    for slug, (old, new) in SUMMARIES.items():
        Article.objects.filter(slug=slug, summary=new).update(summary=old)


class Migration(migrations.Migration):
    dependencies = [("core", "0007_learn_pull_quotes_seed")]
    operations = [migrations.RunPython(forwards, backwards)]
