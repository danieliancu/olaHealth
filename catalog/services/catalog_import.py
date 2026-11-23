"""Import an Open Food Facts JSONL dump into the local catalogue, and verify it.

Each line goes through the same path as a product fetched from the API
(``catalog.services.importer.import_off_product`` with ``source="off_dump"``): the same field
normalisation, the same indicator rules and the same Foodo Score function. Approved local
corrections and the revision history are kept; a line older than the stored copy is skipped.

This importer is correct but not built for speed (one transaction per product). Loading the
full multi-million-product dump in bulk, syncing through OFF's delta exports and the API v3
migration are a separate stage: docs/off-local-catalogue.md.

The local catalogue is used first only once :func:`verify` marks an import ``verified``.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

from django.conf import settings
from django.utils import timezone

from catalog.models import CatalogImport, Product, ProductRevision

logger = logging.getLogger(__name__)

# Fields the dump must carry (docs/off-local-catalogue.md). The first two are needed on
# (almost) every line; the others must exist in the dump at all, since OFF leaves a field
# out of a product when it has no value (e.g. no NOVA group computed).
IDENTITY_FIELDS = ("code", "last_modified_t")
CONTENT_FIELDS = (
    "product_name",
    "categories_tags",
    "countries_tags",
    "ingredients_text",
    "allergens_tags",
    "traces_tags",
    "additives_tags",
    "nutriments",
    "nutriscore_grade",
    "nutriscore_score",
    "nutriscore_version",
    "nova_group",
    "nutrient_levels",
    "misc_tags",
)
REQUIRED_FIELDS = IDENTITY_FIELDS + CONTENT_FIELDS
IDENTITY_MIN_SHARE = 0.99
SCORE_SAMPLE = 50
MAX_ERRORS_KEPT = 20
SAVE_EVERY = 500


def _open(path: Path) -> IO[bytes]:
    return gzip.open(path, "rb") if path.suffix == ".gz" else path.open("rb")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _lines(path: Path) -> Iterator[bytes]:
    with _open(path) as fh:
        for line in fh:
            if line.strip():
                yield line


def _present(value: Any) -> bool:
    return value not in (None, "", [], {})


def run_jsonl(
    path: Path | str,
    *,
    exported_at: datetime | None = None,
    country: str = "",
    limit: int | None = None,
    verify_after: bool = True,
) -> CatalogImport:
    """Import a JSONL (or .jsonl.gz) dump. Always leaves a ``CatalogImport`` that says how it
    ended; an exception or an interruption marks it ``failed``."""
    from catalog.services.importer import import_off_product, older_than_local
    from off.codes import InvalidCode, parse_code

    path = Path(path)
    if exported_at is None:
        exported_at = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    run = CatalogImport.objects.create(
        file_name=path.name[:300],
        file_sha256=file_sha256(path),
        exported_at=exported_at,
        report={
            "country": country or None,
            "limit": limit,
            "products_before": Product.objects.filter(current_revision__isnull=False).count(),
            "fields": dict.fromkeys(REQUIRED_FIELDS, 0),
            "errors": [],
        },
    )
    fields = run.report["fields"]
    errors = run.report["errors"]

    def fail(line_no: int, message: str) -> None:
        run.failed += 1
        if len(errors) < MAX_ERRORS_KEPT:
            errors.append({"line": line_no, "error": message[:300]})

    try:
        for line_no, raw in enumerate(_lines(path), start=1):
            if limit is not None and run.seen >= limit:
                break
            run.seen += 1
            try:
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    raise ValueError("line is not a JSON object")
            except ValueError as exc:
                fail(line_no, f"invalid JSON: {exc}")
                continue
            for name in REQUIRED_FIELDS:
                if name in payload:
                    fields[name] += 1
            countries = payload.get("countries_tags") or []
            if country and country not in countries:
                run.skipped += 1
                continue
            try:
                parsed = parse_code(str(payload.get("code") or ""), "manual")
            except InvalidCode:
                run.skipped += 1  # no usable barcode
                continue
            try:
                existing = (
                    Product.objects.select_related("current_revision")
                    .filter(off_code=parsed.off_code)
                    .first()
                )
                if older_than_local(existing, payload):
                    run.skipped += 1
                    continue
                import_off_product(
                    payload,
                    parsed,
                    source=ProductRevision.Source.OFF_DUMP,
                    synced_at=exported_at,
                    queue_images=False,
                )
                run.imported += 1
            except Exception as exc:  # one bad product never stops the import
                logger.warning("Catalogue import #%s, line %s: %s", run.pk, line_no, exc)
                fail(line_no, f"{type(exc).__name__}: {exc}")
            if run.seen % SAVE_EVERY == 0:
                run.save(update_fields=["seen", "imported", "skipped", "failed", "report"])
    except BaseException as exc:
        run.status = CatalogImport.Status.FAILED
        run.finished_at = timezone.now()
        run.notes = f"Stopped: {type(exc).__name__}: {exc}"[:2000]
        run.save()
        raise
    run.status = CatalogImport.Status.COMPLETED
    run.finished_at = timezone.now()
    run.save()
    if verify_after:
        verify(run)
    return run


def _check(checks: list[dict], name: str, ok: bool, detail: str) -> None:
    checks.append({"check": name, "ok": bool(ok), "detail": detail})


def verify(run: CatalogImport) -> CatalogImport:
    """Mark a finished import ``verified`` only if every check passes, else ``rejected``.

    The checks and their details are saved in ``run.report["checks"]``.
    """
    from assessments.score import core, needs_recompute, score_from_off

    run.refresh_from_db()
    checks: list[dict] = []
    report = run.report or {}
    _check(
        checks,
        "finished",
        run.status
        in (
            CatalogImport.Status.COMPLETED,
            CatalogImport.Status.REJECTED,
            CatalogImport.Status.VERIFIED,
        )
        and run.finished_at is not None,
        f"status {run.status}",
    )
    minimum = settings.OFF_DUMP_MIN_PRODUCTS
    _check(
        checks,
        "enough_products",
        run.imported >= minimum,
        f"{run.imported} imported, at least {minimum} needed",
    )
    ratio = run.failed / run.seen if run.seen else 1.0
    _check(
        checks,
        "few_failures",
        run.seen > 0 and ratio <= settings.OFF_DUMP_MAX_FAILED_RATIO,
        f"{run.failed} of {run.seen} lines failed ({ratio:.2%}); "
        f"at most {settings.OFF_DUMP_MAX_FAILED_RATIO:.2%}",
    )
    counts = report.get("fields") or {}
    missing = [f for f in CONTENT_FIELDS if not counts.get(f)]
    _check(
        checks,
        "fields_present",
        not missing,
        "every required field is in the dump"
        if not missing
        else f"never present: {', '.join(missing)}",
    )
    thin = [
        f for f in IDENTITY_FIELDS if run.seen and counts.get(f, 0) / run.seen < IDENTITY_MIN_SHARE
    ]
    _check(
        checks,
        "identity_fields",
        not thin,
        "code and last_modified_t on (almost) every line"
        if not thin
        else f"too often missing: {', '.join(thin)}",
    )
    before = int(report.get("products_before") or 0)
    after = Product.objects.filter(current_revision__isnull=False).count()
    _check(checks, "nothing_lost", after >= before, f"{before} products before, {after} after")

    revisions = ProductRevision.objects.filter(
        source=ProductRevision.Source.OFF_DUMP,
        created_at__gte=run.started_at,
        created_at__lte=run.finished_at or timezone.now(),
    )
    current_ids = Product.objects.filter(current_revision__in=revisions).values(
        "current_revision_id"
    )
    stored = list(
        ProductRevision.objects.filter(pk__in=current_ids).values_list("foodo_score", flat=True)
    )
    unscored = sum(1 for s in stored if needs_recompute(s))
    _check(
        checks,
        "scores_stored",
        unscored == 0,
        f"{len(stored) - unscored} of {len(stored)} current imported revisions have a score",
    )
    sample = (
        ProductRevision.objects.filter(pk__in=current_ids, product__locked_fields=[])
        .exclude(off_payload=None)
        .order_by("pk")[:SCORE_SAMPLE]
    )
    mismatched = [
        str(r.product_id)
        for r in sample
        if core(r.foodo_score or {}) != core(score_from_off(r.off_payload))
    ]
    _check(
        checks,
        "same_score_as_api",
        not mismatched,
        "the stored score equals the score of the same OFF data read as an API response"
        if not mismatched
        else f"different for: {', '.join(mismatched[:10])}",
    )

    passed = all(c["ok"] for c in checks)
    run.report = {**report, "checks": checks}
    run.status = CatalogImport.Status.VERIFIED if passed else CatalogImport.Status.REJECTED
    run.verified_at = timezone.now() if passed else None
    run.save()
    return run
