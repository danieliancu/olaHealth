"""Index the Magazine for Ask Foodo from local content (``core.passages``).

python manage.py rebuild_passages          # what changed, plus missing embeddings
python manage.py rebuild_passages --all    # drop every passage and index again
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from core import passages


class Command(BaseCommand):
    help = "Index published Magazine articles for Ask Foodo (passages, full text, embeddings)."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--all", action="store_true", help="Drop every passage first")

    def handle(self, *args, **options) -> None:
        report = passages.rebuild() if options["all"] else passages.refresh_all()
        self.stdout.write(
            "Published articles {articles}; rebuilt {rebuilt}; passages {passages}; "
            "embedded now {embedded}; without embedding {without_embedding}.".format(**report)
        )
