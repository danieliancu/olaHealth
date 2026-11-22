"""Apply the current OFF mapping again to products already imported, from the OFF payload kept
with their revision (no network). A new revision is published only when the result differs,
and local corrections keep winning, as in a normal import.

Used after a mapping change, e.g. per 100 g / ml worked out from the product type (off.basis):
    python manage.py remap_off --unspecified
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from catalog.models import NutrientFact, Product
from catalog.services.importer import import_off_product


class Command(BaseCommand):
    help = "Re-apply the OFF mapping to imported products from their stored OFF payload."

    def add_arguments(self, parser):
        parser.add_argument(
            "--unspecified",
            action="store_true",
            help="Only products whose values are per 100 g or ml 'not stated by the source'.",
        )

    def handle(self, *args, unspecified: bool = False, **options):
        products = Product.objects.filter(current_revision__off_payload__isnull=False)
        if unspecified:
            revisions = NutrientFact.objects.filter(basis="per_100_unspecified").values(
                "revision_id"
            )
            products = products.filter(current_revision_id__in=revisions)
        changed = total = 0
        for product in products.select_related("current_revision").iterator():
            total += 1
            before = product.current_revision_id
            payload = product.current_revision.off_payload
            if not isinstance(payload, dict) or not payload.get("code"):
                continue
            after = import_off_product(payload).current_revision_id
            changed += after != before
        self.stdout.write(f"{total} product(s) checked, {changed} updated.")
