"""Import an Open Food Facts JSONL dump (or a part of it) into the local catalogue.

    python manage.py import_off_dump openfoodfacts-products.jsonl.gz \\
        --country en:united-kingdom --exported-at 2026-11-20

The import is recorded (``CatalogImport``) and verified at the end; the local catalogue is
used first only if the verification passes. See docs/off-local-catalogue.md.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from catalog.services.catalog_import import run_jsonl


class Command(BaseCommand):
    help = "Import an Open Food Facts JSONL dump into the local catalogue, then verify it."

    def add_arguments(self, parser) -> None:
        parser.add_argument("path", help="Path to the .jsonl or .jsonl.gz dump")
        parser.add_argument("--country", default="", help="Only products sold in this country")
        parser.add_argument("--limit", type=int, default=None, help="Stop after N lines")
        parser.add_argument(
            "--exported-at",
            default="",
            help="Date of the dump (ISO 8601); defaults to the file's modification time",
        )
        parser.add_argument(
            "--no-verify", action="store_true", help="Do not verify at the end (stays completed)"
        )

    def handle(self, *args, **options) -> None:
        path = Path(options["path"])
        if not path.is_file():
            raise CommandError(f"No such file: {path}")
        exported_at = None
        if options["exported_at"]:
            try:
                exported_at = datetime.fromisoformat(options["exported_at"])
            except ValueError as exc:
                raise CommandError(f"--exported-at: {exc}") from exc
            if exported_at.tzinfo is None:
                exported_at = exported_at.replace(tzinfo=UTC)
        run = run_jsonl(
            path,
            exported_at=exported_at,
            country=options["country"],
            limit=options["limit"],
            verify_after=not options["no_verify"],
        )
        self.stdout.write(
            f"Import #{run.pk}: {run.status}. Lines {run.seen}, imported {run.imported}, "
            f"skipped {run.skipped}, failed {run.failed}."
        )
        for check in (run.report or {}).get("checks", []):
            mark = "ok  " if check["ok"] else "FAIL"
            self.stdout.write(f"  {mark} {check['check']}: {check['detail']}")
