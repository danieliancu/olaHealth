"""Verify (again) a finished catalogue import; see docs/off-local-catalogue.md."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from catalog.models import CatalogImport
from catalog.services.catalog_import import verify
from catalog.services.catalog_mode import describe


class Command(BaseCommand):
    help = "Run the readiness checks on a catalogue import (verified or rejected)."

    def add_arguments(self, parser) -> None:
        parser.add_argument("import_id", nargs="?", type=int, help="Defaults to the latest")

    def handle(self, *args, **options) -> None:
        qs = CatalogImport.objects.all()
        run = (
            qs.filter(pk=options["import_id"]).first()
            if options["import_id"]
            else qs.order_by("-started_at", "-pk").first()
        )
        if run is None:
            raise CommandError("No such catalogue import.")
        run = verify(run)
        for check in run.report.get("checks", []):
            mark = "ok  " if check["ok"] else "FAIL"
            self.stdout.write(f"  {mark} {check['check']}: {check['detail']}")
        state = describe()
        self.stdout.write(f"Import #{run.pk}: {run.status}. Catalogue mode: {state['mode']}.")
        self.stdout.write(state["reason"])
