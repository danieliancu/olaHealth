"""Diary foods copy the product name and brand; decode HTML entities copied from Open
Food Facts before the importer did (Biscuits &quot;lotus&quot;)."""

import html

from django.db import migrations
from django.db.models import Q


def unescape(apps, schema_editor):
    DiaryEntry = apps.get_model("myola", "DiaryEntry")
    for e in DiaryEntry.objects.filter(Q(name__contains="&") | Q(brand__contains="&")).iterator():
        e.name, e.brand = html.unescape(e.name), html.unescape(e.brand)
        e.save(update_fields=["name", "brand"])


class Migration(migrations.Migration):
    dependencies = [("myola", "0005_water_and_weigh_in_reminders")]
    operations = [migrations.RunPython(unescape, migrations.RunPython.noop)]
