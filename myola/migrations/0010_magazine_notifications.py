"""Learn was renamed Magazine: old "New article in Learn" notifications follow."""

from django.db import migrations


def forwards(apps, schema_editor):
    Notification = apps.get_model("myola", "Notification")
    Notification.objects.filter(title="New article in Learn").update(
        title="New article in the Magazine"
    )


def backwards(apps, schema_editor):
    Notification = apps.get_model("myola", "Notification")
    Notification.objects.filter(title="New article in the Magazine").update(
        title="New article in Learn"
    )


class Migration(migrations.Migration):
    dependencies = [("myola", "0009_ai_personal")]
    operations = [migrations.RunPython(forwards, backwards)]
