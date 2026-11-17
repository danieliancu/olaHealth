from django.core.management.base import BaseCommand

from catalog.export import write_export


class Command(BaseCommand):
    help = "Write the ODbL export of published data (gzipped JSON Lines) to EXPORT_ROOT."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--dir", default=None, help="Output directory (default EXPORT_ROOT)")

    def handle(self, *args, **options) -> None:
        self.stdout.write(write_export(options["dir"]))
