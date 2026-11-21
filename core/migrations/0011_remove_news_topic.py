"""Learn: the News topic is dropped again (when it has no articles)."""

from django.db import migrations
from django.db.models import F


def forwards(apps, schema_editor):
    Topic = apps.get_model("core", "LearnTopic")
    topic = Topic.objects.filter(key="news").first()
    if topic and not topic.articles.exists():
        topic.delete()
        Topic.objects.filter(order__gt=0).update(order=F("order") - 1)


class Migration(migrations.Migration):
    dependencies = [("core", "0010_learn_news_topic")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
