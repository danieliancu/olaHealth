"""Store the current Foodo Score on revisions that need it (new rules version, old rows)."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from assessments.tasks import recompute_foodo_scores, store


class Command(BaseCommand):
    help = "Recompute stored Foodo Scores in batches (only those missing or outdated)."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--batch", type=int, default=500)
        parser.add_argument(
            "--all", action="store_true", help="Recompute every current revision, in batches"
        )

    def handle(self, *args, **options) -> None:
        batch = max(1, options["batch"])
        total = 0
        if options["all"]:
            from catalog.models import Product, ProductRevision

            current = Product.objects.filter(current_revision__isnull=False).values(
                "current_revision_id"
            )
            ids = list(
                ProductRevision.objects.filter(pk__in=current)
                .order_by("pk")
                .values_list("pk", flat=True)
            )
            for start in range(0, len(ids), batch):
                chunk = ProductRevision.objects.filter(
                    pk__in=ids[start : start + batch]
                ).prefetch_related("assessments")
                total += store(chunk)
        else:
            while updated := recompute_foodo_scores(batch):
                total += updated
        self.stdout.write(self.style.SUCCESS(f"{total} stored Foodo Scores updated."))
