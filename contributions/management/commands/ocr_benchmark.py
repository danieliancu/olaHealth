"""OCR latency and memory on the fixture labels (or given images)."""

from __future__ import annotations

import resource
import time
from pathlib import Path

import pytesseract
from django.conf import settings
from django.core.management.base import BaseCommand
from PIL import Image

from contributions.nutrition_parser import parse_nutrition_text
from contributions.services.ocr import engine_version, preprocess

FIXTURES = Path(settings.BASE_DIR) / "tests" / "fixtures" / "ocr"


class Command(BaseCommand):
    help = "Measure Tesseract OCR latency and peak memory (RSS) on sample images."

    def add_arguments(self, parser) -> None:
        parser.add_argument("images", nargs="*")
        parser.add_argument("--runs", type=int, default=3)

    def handle(self, *args, images, runs, **options) -> None:
        paths = [Path(p) for p in images] or sorted(FIXTURES.glob("*.jpg"))
        self.stdout.write(f"tesseract {engine_version()}, lang={settings.OCR_LANGUAGES}")
        for path in paths:
            timings = []
            proposals = 0
            for _ in range(runs):
                with Image.open(path) as img:
                    img.load()
                    prepared = preprocess(img.convert("RGB"))
                t0 = time.perf_counter()
                text = pytesseract.image_to_string(
                    prepared,
                    lang=settings.OCR_LANGUAGES,
                    config="--psm 6",
                    timeout=settings.OCR_TIMEOUT_SECONDS,
                )
                timings.append((time.perf_counter() - t0) * 1000)
                proposals = len(parse_nutrition_text(text).proposals)
            self.stdout.write(
                f"{path.name}: {prepared.width}x{prepared.height}px, runs={runs}, "
                f"min {min(timings):.0f} ms, max {max(timings):.0f} ms, proposals={proposals}"
            )
        # ru_maxrss is in KiB on Linux. Children = tesseract subprocesses.
        own = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        child = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024
        self.stdout.write(f"peak RSS: python {own:.0f} MiB, tesseract {child:.0f} MiB")
