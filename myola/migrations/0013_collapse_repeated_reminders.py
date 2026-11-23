"""Give existing reminders their topic and keep only the newest one per user and topic.

Same rule as ``myola.services.notifications.reminder_topic``, copied here so this migration
does not change if that function does.
"""

from django.db import migrations


def topic(kind: str, key: str) -> str:
    if kind != "reminder":
        return ""
    parts = key.split(":")
    if len(parts) != 3:
        return ""
    if parts[1] == "weigh":
        return "weigh"
    return "water" if parts[2].startswith("water-") else parts[2][:60]


def collapse(apps, schema_editor):
    Notification = apps.get_model("myola", "Notification")
    rows = Notification.objects.filter(kind="reminder", topic="").only("pk", "kind", "key")
    batch = []
    for n in rows.iterator(chunk_size=1000):
        n.topic = topic(n.kind, n.key)
        if n.topic:
            batch.append(n)
        if len(batch) >= 1000:
            Notification.objects.bulk_update(batch, ["topic"])
            batch = []
    if batch:
        Notification.objects.bulk_update(batch, ["topic"])
    seen: set[tuple[int, str]] = set()
    older: list[int] = []
    for pk, user_id, t in (
        Notification.objects.exclude(topic="")
        .order_by("user_id", "topic", "-created_at", "-pk")
        .values_list("pk", "user_id", "topic")
        .iterator(chunk_size=2000)
    ):
        if (user_id, t) in seen:
            older.append(pk)
        else:
            seen.add((user_id, t))
    for start in range(0, len(older), 1000):
        Notification.objects.filter(pk__in=older[start : start + 1000]).delete()


class Migration(migrations.Migration):
    dependencies = [("myola", "0012_notification_topic")]
    operations = [migrations.RunPython(collapse, migrations.RunPython.noop)]
