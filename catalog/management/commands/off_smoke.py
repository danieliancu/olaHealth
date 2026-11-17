"""Limited smoke test against the real Open Food Facts API (read-only, <= 5 codes).

Reports external vs local lookup latency and the number of OFF requests for repeated
scans. Products fetched are real OFF products and stay in the local catalogue.
"""

from __future__ import annotations

import statistics
import time

from django.core.management.base import BaseCommand, CommandError

from catalog.models import LookupState, Product
from catalog.services.lookup import lookup

DEFAULT_CODES = ["3017620422003", "5449000000996", "3274080005003"]


class Command(BaseCommand):
    help = "Look up a few real barcodes on OFF, then repeat locally; print timings."

    def add_arguments(self, parser) -> None:
        parser.add_argument("codes", nargs="*", default=DEFAULT_CODES)
        parser.add_argument("--repeat", type=int, default=5)
        parser.add_argument("--fresh", action="store_true", help="delete local copies first")

    def handle(self, *args, codes, repeat, fresh, **options) -> None:
        if len(codes) > 5:
            raise CommandError("At most 5 codes: respect the OFF rate limit.")
        for code in codes:
            if fresh:
                Product.objects.filter(off_code=code).delete()
                LookupState.objects.filter(off_code=code).delete()
            t0 = time.perf_counter()
            first = lookup(code, "ean13", schedule=False)
            external_ms = (time.perf_counter() - t0) * 1000
            local = []
            for _ in range(repeat):
                t0 = time.perf_counter()
                r = lookup(code, "ean13", schedule=False)
                local.append((time.perf_counter() - t0) * 1000)
                assert r.status == first.status
            state = LookupState.objects.filter(off_code=code).first()
            self.stdout.write(
                f"{code}: first={first.status}/{first.source or first.error} {external_ms:.0f} ms; "
                f"{repeat} repeats local median {statistics.median(local):.1f} ms; "
                f"OFF requests total for this code: {state.off_requests if state else 0}"
            )
