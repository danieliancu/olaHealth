"""ODbL export of published derived data (gzipped JSON Lines).

Contains only published revisions. Excluded: drafts, unapproved
contributions, sessions, tokens, private images, OCR output, moderation data, logs.
"""

from __future__ import annotations

import gzip
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from catalog.models import Product

LICENSE_NOTICE = {
    "data_license": "Open Database License (ODbL) 1.0, https://opendatacommons.org/licenses/odbl/1-0/",
    "contents_license": "Database Contents License (DbCL) 1.0",
    "images": "Each image lists its own license and attribution.",
    "attribution": (
        "Contains information from Open Food Facts (https://openfoodfacts.org), made available "
        "under the ODbL, and corrections from FoodoTrack contributors."
    ),
}


def _public_sources(field_sources: dict[str, Any]) -> dict[str, Any]:
    allowed = {"source", "off_last_modified", "approved_at"}
    return {
        k: {kk: vv for kk, vv in (v or {}).items() if kk in allowed}
        for k, v in field_sources.items()
    }


def product_record(p: Product) -> dict[str, Any]:
    rev = p.current_revision
    return {
        "id": str(p.pk),
        "identifiers": [
            {"value": i.normalized, "kind": i.kind, "format": i.detected_format}
            for i in p.identifiers.all()
        ],
        "off_code": p.off_code,
        "revision": rev.number,
        "revision_created_at": rev.created_at.isoformat(),
        "data": rev.data,
        "field_sources": _public_sources(rev.field_sources),
        "indicators": [
            {
                "indicator": s.indicator,
                "origin": s.origin,
                "data_status": s.data_status,
                "current": not s.invalidated_by_local_change,
                "value": s.value,
                "algorithm_version": s.algorithm_version,
                "warnings": s.warnings,
            }
            for s in rev.assessments.all()
        ],
        "images": [
            {
                "kind": img.kind,
                "url": img.remote_url or reverse("public-image", args=[img.pk]),
                "license": img.license,
                "attribution": img.attribution,
                "source": img.source,
            }
            for img in p.images.all()
            if img.is_active
            and (
                img.source == "off" or (img.contribution_image and img.contribution_image.is_public)
            )
        ],
    }


def iter_records():
    qs = (
        Product.objects.filter(current_revision__isnull=False)
        .select_related("current_revision")
        .prefetch_related(
            "identifiers", "images__contribution_image", "current_revision__assessments"
        )
        .order_by("pk")
    )
    for product in qs.iterator(chunk_size=200):
        yield product_record(product)


def write_export(directory: Path | None = None) -> str:
    directory = Path(directory or settings.EXPORT_ROOT)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = timezone.now().strftime("%Y%m%d")
    target = directory / f"olahealth-catalogue-{stamp}.jsonl.gz"
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    count = 0
    with os.fdopen(fd, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb") as gz:
        meta = {"_meta": {**LICENSE_NOTICE, "generated_at": timezone.now().isoformat()}}
        gz.write((json.dumps(meta, ensure_ascii=False) + "\n").encode())
        for record in iter_records():
            gz.write((json.dumps(record, ensure_ascii=False, default=str) + "\n").encode())
            count += 1
    os.replace(tmp, target)
    latest = directory / "olahealth-catalogue-latest.jsonl.gz"
    tmp_latest = directory / ".latest.tmp"
    tmp_latest.write_bytes(target.read_bytes())
    os.replace(tmp_latest, latest)
    for old in sorted(directory.glob("olahealth-catalogue-2*.jsonl.gz"))[:-7]:
        old.unlink()
    return f"{target.name}: {count} products"
