"""Create or update the django-q2 schedules (idempotent; run at every deploy)."""

from django.core.management.base import BaseCommand
from django_q.models import Schedule

SCHEDULES = [
    ("refresh-stale-products", "catalog.tasks.refresh_stale_products", Schedule.HOURLY),
    ("purge-coordination", "catalog.tasks.purge_coordination", Schedule.HOURLY),
    ("purge-expired-drafts", "contributions.tasks.purge_expired_drafts", Schedule.DAILY),
    ("delete-orphan-files", "contributions.tasks.delete_orphan_files", Schedule.DAILY),
    (
        "report-waiting-contributions",
        "contributions.tasks.stale_open_contributions",
        Schedule.DAILY,
    ),
    ("export-catalogue", "catalog.export.write_export", Schedule.DAILY),
    ("sync-taxonomies", "catalog.tasks.sync_taxonomies", Schedule.WEEKLY),
    ("notification-reminders", "myola.tasks.send_reminders", Schedule.HOURLY),
    ("purge-old-notifications", "myola.tasks.purge_old_notifications", Schedule.DAILY),
    ("notify-new-articles", "myola.tasks.notify_new_articles", Schedule.HOURLY),
    ("purge-old-ai-answers", "ai.tasks.purge_old_ai_answers", Schedule.DAILY),
]


class Command(BaseCommand):
    help = "Create or update periodic background jobs."

    def handle(self, *args, **options) -> None:
        for name, func, kind in SCHEDULES:
            Schedule.objects.update_or_create(
                name=name, defaults={"func": func, "schedule_type": kind, "repeats": -1}
            )
        self.stdout.write(self.style.SUCCESS(f"{len(SCHEDULES)} schedules configured."))
