"""Learn: publication dates taken from when the articles were created."""

from django.db import migrations
from django.db.models import F


def forwards(apps, schema_editor):
    Article = apps.get_model("core", "LearnArticle")
    Article.objects.update(published_at=F("created_at"))


def backwards(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [("core", "0009_learn_published_at")]
    operations = [migrations.RunPython(forwards, backwards)]
