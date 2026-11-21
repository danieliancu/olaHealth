"""The "My Track" area is now called "My account": update the article texts that name it."""

from django.db import migrations


def rename(apps, schema_editor):
    LearnArticle = apps.get_model("core", "LearnArticle")
    for a in LearnArticle.objects.filter(body__contains="My Track"):
        a.body = a.body.replace("My Track", "My account")
        a.save(update_fields=["body"])


class Migration(migrations.Migration):
    dependencies = [("core", "0012_learn_article_notified")]
    operations = [migrations.RunPython(rename, migrations.RunPython.noop)]
