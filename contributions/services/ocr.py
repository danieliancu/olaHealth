"""Local OCR with Tesseract (eng+ron) on contribution photos, run by the worker.

* Moderate preprocessing only: grayscale, size normalisation, autocontrast with a small
  cutoff. No thresholding/sharpening that could destroy thin print.
* The OCR derivative is stored next to the re-encoded original.
* Word boxes and confidences are kept for the moderator view.
* Jobs are idempotent: a run is claimed with a conditional UPDATE, so a duplicate task
  for the same run does nothing.
"""

from __future__ import annotations

import io
import logging
import time
from datetime import timedelta
from functools import cache

import pytesseract
from django.conf import settings
from django.core.files.base import ContentFile
from django.db.models import F
from django.utils import timezone
from PIL import Image, ImageOps

from contributions.models import ContributionImage, OCRRun
from contributions.nutrition_parser import parse_nutrition_text

logger = logging.getLogger(__name__)

OCR_KINDS = {ContributionImage.Kind.NUTRITION, ContributionImage.Kind.INGREDIENTS}
STALE_RUNNING = timedelta(minutes=5)


@cache
def engine_version() -> str:
    try:
        return str(pytesseract.get_tesseract_version())
    except pytesseract.TesseractNotFoundError:
        return "unavailable"


def preprocess(image: Image.Image) -> Image.Image:
    gray = ImageOps.grayscale(image)
    longest = max(gray.size)
    target = settings.OCR_MAX_SIDE
    if longest > target:
        gray.thumbnail((target, target), Image.Resampling.LANCZOS)
    elif longest < 1000:
        factor = 1000 / longest
        gray = gray.resize(
            (round(gray.width * factor), round(gray.height * factor)), Image.Resampling.LANCZOS
        )
    return ImageOps.autocontrast(gray, cutoff=1)


def queue_ocr(image: ContributionImage) -> OCRRun | None:
    """Create (once) and enqueue the OCR run for an image. Safe to call repeatedly."""
    if image.kind not in OCR_KINDS:
        return None
    run, created = OCRRun.objects.get_or_create(
        image=image,
        engine="tesseract",
        engine_version=engine_version(),
        lang=settings.OCR_LANGUAGES,
    )
    if created:
        enqueue(run)
    return run


def enqueue(run: OCRRun) -> None:
    from django_q.tasks import async_task

    async_task(
        "contributions.tasks.run_ocr", run.pk, task_name=f"ocr-{run.pk}-{run.attempts}", group="ocr"
    )


def retry(run: OCRRun) -> bool:
    """User-initiated retry of a failed run, bounded by OCR_MAX_ATTEMPTS + 1."""
    updated = OCRRun.objects.filter(
        pk=run.pk, status=OCRRun.Status.FAILED, attempts__lte=settings.OCR_MAX_ATTEMPTS
    ).update(status=OCRRun.Status.QUEUED, error="")
    if updated:
        run.refresh_from_db()
        enqueue(run)
    return bool(updated)


def _claim(run_id: int) -> bool:
    now = timezone.now()
    claimed = OCRRun.objects.filter(pk=run_id, status=OCRRun.Status.QUEUED).update(
        status=OCRRun.Status.RUNNING, started_at=now, attempts=F("attempts") + 1
    )
    if not claimed:
        # Recover a run whose worker died (status stuck in running).
        claimed = OCRRun.objects.filter(
            pk=run_id, status=OCRRun.Status.RUNNING, started_at__lt=now - STALE_RUNNING
        ).update(started_at=now, attempts=F("attempts") + 1)
    return bool(claimed)


def run_ocr(run_id: int) -> str:
    if not _claim(run_id):
        return "skipped"
    run = OCRRun.objects.select_related("image").get(pk=run_id)
    started = time.monotonic()
    try:
        with run.image.file.open("rb") as fh, Image.open(fh) as img:
            img.load()
            prepared = preprocess(img)
        buffer = io.BytesIO()
        prepared.save(buffer, format="PNG", optimize=True)
        if not run.image.ocr_file:
            run.image.ocr_file.save(
                f"{run.image.pk}.png", ContentFile(buffer.getvalue()), save=True
            )
        data = pytesseract.image_to_data(
            prepared,
            lang=run.lang,
            config="--psm 6",
            timeout=settings.OCR_TIMEOUT_SECONDS,
            output_type=pytesseract.Output.DICT,
        )
    except RuntimeError as exc:  # pytesseract raises RuntimeError on timeout
        return _fail(run, "timeout" if "timeout" in str(exc).lower() else "engine_error", started)
    except (pytesseract.TesseractError, pytesseract.TesseractNotFoundError, OSError) as exc:
        logger.warning("OCR failed for run %s: %s", run.pk, type(exc).__name__)
        return _fail(run, "engine_error", started)

    words, text = _collect(data)
    run.text = text
    run.words = words
    run.parsed = (
        parse_nutrition_text(text).as_dict()
        if run.image.kind == ContributionImage.Kind.NUTRITION
        else {}
    )
    run.status = OCRRun.Status.DONE
    run.error = ""
    run.duration_ms = int((time.monotonic() - started) * 1000)
    run.finished_at = timezone.now()
    run.save(
        update_fields=["text", "words", "parsed", "status", "error", "duration_ms", "finished_at"]
    )
    return "done"


def _fail(run: OCRRun, error: str, started: float) -> str:
    run.refresh_from_db(fields=["attempts"])
    run.status = OCRRun.Status.FAILED
    run.error = error
    run.duration_ms = int((time.monotonic() - started) * 1000)
    run.finished_at = timezone.now()
    run.save(update_fields=["status", "error", "duration_ms", "finished_at"])
    # One automatic retry for engine errors; timeouts would most likely time out again.
    if error == "engine_error" and run.attempts < settings.OCR_MAX_ATTEMPTS:
        retry(run)
    return error


def _collect(data: dict) -> tuple[list[dict], str]:
    words = []
    lines: dict[tuple[int, int, int], list[str]] = {}
    for i, word in enumerate(data.get("text", [])):
        word = (word or "").strip()
        if not word:
            continue
        conf = float(data["conf"][i])
        words.append(
            {
                "text": word,
                "conf": round(conf, 1),
                "box": [data["left"][i], data["top"][i], data["width"][i], data["height"][i]],
            }
        )
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        lines.setdefault(key, []).append(word)
    text = "\n".join(" ".join(ws) for _k, ws in sorted(lines.items()))
    return words, text
