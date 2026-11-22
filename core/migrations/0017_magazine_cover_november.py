"""The Magazine cover: November 2026, with the built-in photo."""

import datetime as dt

from django.db import migrations


def forwards(apps, schema_editor):
    apps.get_model("core", "MagazineCover").objects.get_or_create(
        pk=1, defaults={"month": dt.date(2026, 11, 1)}
    )


class Migration(migrations.Migration):
    dependencies = [("core", "0016_magazine_cover")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
