"""Daily Open Food Facts delta sync (``manage.py sync_off_delta``, scheduled daily).

OFF publishes one file per day with every product changed in that interval:
``https://static.openfoodfacts.org/data/delta/index.txt`` lists
``openfoodfacts_products_<from>_<to>.json.gz`` (JSONL, the same product objects as the full
dump; ``<from>``/``<to>`` are UTC timestamps). OFF keeps about two weeks of them.

Rules (docs/off-local-catalogue.md, "Daily sync"):

* **Where to start.** ``OffDeltaState.start_from``: the archive's verifiable provenance (the
  newest ``last_modified_t`` imported from the dump, never later than the dump's export date)
  minus ``OFF_DELTA_OVERLAP_HOURS``. Files are processed in chronological order from the one
  covering that time. If OFF's oldest file starts later, the hole is recorded in
  ``OffDeltaState.gap`` (shown in Staff) and nothing is marked synced past it.
* **Policy.** A product already in the catalogue (from the dump, the API or a contribution
  with a barcode) is always processed, also when OFF changed or removed its country. A new
  product is added only when one of its ``countries_tags`` is in
  ``OFF_DELTA_NEW_PRODUCT_COUNTRIES`` ("all": every market). Others are skipped
  (``not_in_policy``): absence from a day's file is normal.
* **Same mapping.** Each line goes through ``import_off_product(source="off_dump")``: the same
  nutrition, photos, Foodo Score and reconciliation (approved local corrections are kept). A
  line older than the stored copy is never applied (``older``); an identical one creates no
  revision (``unchanged``).
* **Complete or not at all.** A file is downloaded to a ``.part`` file, checked against
  ``Content-Length`` and read to the gzip end (its CRC is checked there). It is ``done`` only
  when every line was processed without an error; skips (invalid barcode, not in policy) are
  not errors. A failed file stops the run; the next run starts it again. ``synced_until``
  moves only along an unbroken chain of done files.
* **Safe to repeat.** One run at a time (PostgreSQL advisory lock). An interrupted file resumes
  at ``next_line`` (saved every ``CHECKPOINT`` lines); lines processed twice change nothing
  (older / same content), so the result is the same, with no duplicate product or revision.
* Never a ``CatalogImport``: a delta cannot switch the local catalogue mode.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import re
import time
import zlib
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from django.conf import settings
from django.db import connection
from django.utils import timezone

from catalog.models import OffDeltaFile, OffDeltaState, Product, ProductIdentifier

logger = logging.getLogger(__name__)

FILE_RE = re.compile(r"^openfoodfacts_products_(\d{9,11})_(\d{9,11})\.json\.gz$")
LOCK_KEY = 72_026_1124  # pg_advisory_lock key of this sync
CHECKPOINT = 500
MAX_ERRORS_KEPT = 20


class SyncError(Exception):
    pass


@dataclass(frozen=True)
class DeltaRef:
    name: str
    start_ts: int
    end_ts: int

    @property
    def url(self) -> str:
        return settings.OFF_DELTA_INDEX_URL.rsplit("/", 1)[0] + "/" + self.name


def ts(value: int | None) -> str:
    return datetime.fromtimestamp(value, tz=UTC).isoformat() if value else "-"


def policy() -> dict[str, Any]:
    countries = list(settings.OFF_DELTA_NEW_PRODUCT_COUNTRIES)
    return {
        "known_products": "always updated (any market, also when OFF changes the country)",
        "new_products": (
            "all markets"
            if "all" in countries
            else (", ".join(countries) if countries else "none (known products only)")
        ),
        "new_product_countries": countries,
    }


def _adds(countries_tags: list[str]) -> bool:
    allowed = settings.OFF_DELTA_NEW_PRODUCT_COUNTRIES
    return "all" in allowed or bool(set(allowed) & set(countries_tags or []))


# ---------- Index ----------
def parse_index(text: str) -> list[DeltaRef]:
    """The files of the index, oldest first. Unknown lines are ignored."""
    refs = {}
    for line in text.splitlines():
        m = FILE_RE.match(line.strip())
        if m:
            refs[line.strip()] = DeltaRef(line.strip(), int(m.group(1)), int(m.group(2)))
    return sorted(refs.values(), key=lambda r: (r.start_ts, r.end_ts))


def _client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": settings.OFF_USER_AGENT},
        timeout=httpx.Timeout(settings.OFF_DELTA_TIMEOUT, connect=15),
        follow_redirects=True,
    )


def fetch_index(client: httpx.Client) -> list[DeltaRef]:
    response = client.get(settings.OFF_DELTA_INDEX_URL)
    response.raise_for_status()
    return parse_index(response.text)


# ---------- Start point ----------
def provenance() -> tuple[int | None, str]:
    """``(start_from, note)`` from the archive: the newest OFF ``last_modified_t`` imported from
    a dump (a lower bound of when the dump was made), capped by the verified import's export
    date, minus the safety overlap."""
    from django.db.models import Max

    from catalog.models import CatalogImport, ProductRevision

    newest = ProductRevision.objects.filter(source=ProductRevision.Source.OFF_DUMP).aggregate(
        m=Max("off_last_modified")
    )["m"]
    imported = (
        CatalogImport.objects.filter(status=CatalogImport.Status.VERIFIED)
        .order_by("-exported_at")
        .values_list("exported_at", flat=True)
        .first()
    )
    if newest is None and imported is None:
        return None, "No verified dump import: set the start with --since."
    point = min(x for x in (newest, imported) if x is not None)
    overlap = timedelta(hours=settings.OFF_DELTA_OVERLAP_HOURS)
    note = (
        f"Newest last_modified_t imported from the dump: {newest.isoformat() if newest else '-'};"
        f" verified import exported at: {imported.isoformat() if imported else '-'}; "
        f"start = the earlier of the two minus {settings.OFF_DELTA_OVERLAP_HOURS} h overlap."
    )
    return int((point - overlap).timestamp()), note


def plan(refs: list[DeltaRef], state: OffDeltaState) -> tuple[list[DeltaRef], str]:
    """The files still to process (oldest first) and the gap, if any."""
    start = state.start_from
    done = set(
        OffDeltaFile.objects.filter(status=OffDeltaFile.Status.DONE).values_list("name", flat=True)
    )
    todo = [r for r in refs if r.end_ts > start and r.name not in done]
    gap = ""
    if refs and refs[0].start_ts > start:
        gap = (
            f"OFF's oldest delta starts at {ts(refs[0].start_ts)}, after the start point "
            f"{ts(start)}: changes between them are not in any delta. Recover by importing a "
            "newer full dump (manage.py import_off_dump) and resetting the start "
            "(manage.py sync_off_delta --reset-start)."
        )
    elif not refs:
        gap = "The delta index lists no file."
    return todo, gap


def chain_end(state: OffDeltaState) -> int | None:
    """End of the unbroken chain of done files from ``start_from`` (None: none yet)."""
    files = OffDeltaFile.objects.filter(end_ts__gt=state.start_from).order_by("start_ts")
    covered = None
    for f in files:
        if f.status != OffDeltaFile.Status.DONE:
            break
        if covered is None:
            if f.start_ts > state.start_from:
                break  # a hole before the first file
        elif f.start_ts > covered:
            break  # a hole between files
        covered = max(covered or 0, f.end_ts)
    return covered


# ---------- Download and read ----------
def download(ref: DeltaRef, client: httpx.Client, folder: Path) -> tuple[Path, int, str]:
    """``(path, bytes, sha256)``: the whole file, or SyncError."""
    folder.mkdir(parents=True, exist_ok=True)
    final = folder / ref.name
    part = folder / (ref.name + ".part")
    digest, size = hashlib.sha256(), 0
    with client.stream("GET", ref.url) as response:
        response.raise_for_status()
        expected = response.headers.get("Content-Length")
        with part.open("wb") as fh:
            for chunk in response.iter_bytes(1 << 20):
                fh.write(chunk)
                digest.update(chunk)
                size += len(chunk)
    if expected is not None and int(expected) != size:
        part.unlink(missing_ok=True)
        raise SyncError(f"incomplete download: {size} of {expected} bytes")
    part.replace(final)
    return final, size, digest.hexdigest()


def read_lines(path: Path) -> Iterator[tuple[int, bytes]]:
    """Every non-empty line with its number. A truncated or corrupt gzip raises SyncError
    (gzip checks the CRC and length at the end of the stream)."""
    try:
        with gzip.open(path, "rb") as fh:
            for number, line in enumerate(fh, start=1):
                if line.strip():
                    yield number, line
    except (EOFError, OSError, zlib.error) as exc:
        raise SyncError(f"unreadable gzip: {type(exc).__name__}: {exc}") from exc


# ---------- Processing ----------
def known_product(parsed) -> Product | None:
    product = (
        Product.objects.select_related("current_revision").filter(off_code=parsed.off_code).first()
    )
    if product is None and parsed.kind == "gtin":
        ident = ProductIdentifier.objects.filter(normalized=parsed.identity_key, context="").first()
        if ident is not None:
            product = Product.objects.select_related("current_revision").get(pk=ident.product_id)
    return product


@dataclass
class Outcome:
    kind: str  # added, updated, unchanged, skipped, error
    reason: str = ""


def process_line(raw: bytes, synced_at: datetime) -> Outcome:
    from catalog.models import ProductRevision
    from catalog.services.importer import import_off_product, older_than_local
    from off.codes import InvalidCode, parse_code

    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("not a JSON object")
    except ValueError as exc:
        return Outcome("error", f"invalid JSON: {exc}")
    try:
        parsed = parse_code(str(payload.get("code") or ""), "manual")
    except InvalidCode:
        return Outcome("skipped", "invalid_code")
    product = known_product(parsed)
    if product is None and not _adds(payload.get("countries_tags") or []):
        return Outcome("skipped", "not_in_policy")
    if product is not None and older_than_local(product, payload):
        return Outcome("unchanged", "older")
    before = product.current_revision_id if product is not None else None
    after = import_off_product(
        payload,
        parsed,
        source=ProductRevision.Source.OFF_DUMP,
        synced_at=synced_at,
        queue_images=False,
    )
    if product is None:
        return Outcome("added")
    return Outcome("updated" if after.current_revision_id != before else "unchanged", "same")


@dataclass
class FileResult:
    ref: DeltaRef
    status: str
    seconds: float = 0.0
    counts: Counter = field(default_factory=Counter)
    reasons: Counter = field(default_factory=Counter)
    errors: list = field(default_factory=list)
    message: str = ""


def process_file(
    ref: DeltaRef,
    path: Path,
    size: int,
    sha: str,
    *,
    stop_after: int | None = None,
    on_line: Callable[[int], None] | None = None,
) -> FileResult:
    """Process one downloaded file, resuming at its ``next_line``. ``stop_after`` (tests and the
    interruption check): stop after that many lines as if the process was killed."""
    row, _ = OffDeltaFile.objects.get_or_create(
        name=ref.name, defaults={"start_ts": ref.start_ts, "end_ts": ref.end_ts}
    )
    if row.status == OffDeltaFile.Status.DONE:
        return FileResult(ref, "done", message="already done")
    if row.status == OffDeltaFile.Status.FAILED or (row.sha256 and row.sha256 != sha):
        # A failed file (or one OFF replaced) starts again from the first line: lines done
        # before change nothing the second time (older or same content).
        row.next_line, row.lines = 1, 0
        row.added = row.updated = row.unchanged = row.skipped = row.errors = 0
        row.report = {}
    row.status = OffDeltaFile.Status.RUNNING
    row.size_bytes, row.sha256 = size, sha
    row.attempts += 1
    row.started_at = row.started_at or timezone.now()
    row.report = {**row.report, "policy": policy()}
    row.save()
    reasons = Counter(row.report.get("skip_reasons") or {})
    errors = list(row.report.get("errors") or [])
    synced_at = datetime.fromtimestamp(ref.end_ts, tz=UTC)
    started = time.monotonic()
    processed = 0

    def checkpoint(next_line: int) -> None:
        row.next_line = next_line
        row.report = {**row.report, "skip_reasons": dict(reasons), "errors": errors}
        row.save()

    try:
        for number, raw in read_lines(path):
            row.lines = max(row.lines, number)
            if number < row.next_line:
                continue  # done before an interruption
            outcome = process_line(raw, synced_at)
            if outcome.kind == "error":
                row.errors += 1
                if len(errors) < MAX_ERRORS_KEPT:
                    errors.append({"line": number, "error": outcome.reason[:300]})
            elif outcome.kind == "skipped":
                row.skipped += 1
                reasons[outcome.reason] += 1
            else:
                setattr(row, outcome.kind, getattr(row, outcome.kind) + 1)
                if outcome.kind == "unchanged":
                    reasons[f"unchanged_{outcome.reason}"] += 1
            processed += 1
            if on_line:
                on_line(number)
            if processed % CHECKPOINT == 0:
                checkpoint(number + 1)
            if stop_after is not None and processed >= stop_after:
                checkpoint(number + 1)
                raise KeyboardInterrupt("stopped on purpose")
    except SyncError as exc:
        row.status = OffDeltaFile.Status.FAILED
        errors.append({"line": None, "error": str(exc)[:300]})
        checkpoint(row.next_line)
        return FileResult(ref, "failed", time.monotonic() - started, message=str(exc))
    except Exception as exc:  # recorded, the run stops on this file
        row.status = OffDeltaFile.Status.FAILED
        errors.append({"line": None, "error": f"{type(exc).__name__}: {exc}"[:300]})
        checkpoint(row.next_line)
        raise
    row.status = OffDeltaFile.Status.DONE if row.errors == 0 else OffDeltaFile.Status.FAILED
    row.finished_at = timezone.now()
    row.report = {
        **row.report,
        "seconds": round((row.report.get("seconds") or 0) + time.monotonic() - started, 1),
    }
    checkpoint(row.lines + 1)
    return FileResult(
        ref,
        row.status,
        time.monotonic() - started,
        message="" if row.errors == 0 else f"{row.errors} lines failed: the file stays to retry",
    )


# ---------- One run ----------
class Locked(Exception):
    pass


def _lock() -> bool:
    with connection.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", [LOCK_KEY])
        return bool(cur.fetchone()[0])


def _unlock() -> None:
    with connection.cursor() as cur:
        cur.execute("SELECT pg_advisory_unlock(%s)", [LOCK_KEY])


def file_report(name: str) -> dict[str, Any]:
    f = OffDeltaFile.objects.get(name=name)
    return {
        "file": f.name,
        "interval": f"{ts(f.start_ts)} → {ts(f.end_ts)}",
        "status": f.status,
        "seconds": (f.report or {}).get("seconds"),
        "bytes": f.size_bytes,
        "read": f.lines,
        "added": f.added,
        "updated": f.updated,
        "unchanged": f.unchanged,
        "skipped": f.skipped,
        "skip_reasons": (f.report or {}).get("skip_reasons") or {},
        "errors": f.errors,
        "error_samples": ((f.report or {}).get("errors") or [])[:5],
        "attempts": f.attempts,
    }


def run(
    *,
    only: str | None = None,
    max_files: int | None = None,
    since: int | None = None,
    reset_start: bool = False,
    client: httpx.Client | None = None,
    folder: Path | None = None,
    keep_files: bool = False,
    stop_after: int | None = None,
) -> dict[str, Any]:
    """One sync run. Returns its report (also kept in ``OffDeltaState.last_report``)."""
    if not _lock():
        raise Locked("another delta sync is running")
    began = time.monotonic()
    folder = folder or settings.OFF_DELTA_DIR
    own_client = client is None
    client = client or _client()
    state = OffDeltaState.load()
    report: dict[str, Any] = {"started_at": timezone.now().isoformat(), "policy": policy()}
    try:
        if since is not None or reset_start or state.start_from is None:
            if since is not None:
                state.start_from, state.start_note = since, f"Set by hand: {ts(since)}."
            else:
                state.start_from, state.start_note = provenance()
            if state.start_from is None:
                raise SyncError(state.start_note)
            state.save()
        refs = fetch_index(client)
        todo, gap = plan(refs, state)
        if only:
            todo = [r for r in refs if r.name == only]
            if not todo:
                raise SyncError(f"{only} is not in the index")
        if max_files is not None:
            todo = todo[:max_files]
        report.update(
            start_from=ts(state.start_from), start_note=state.start_note, gap=gap,
            index_files=len(refs), to_process=[r.name for r in todo],
        )  # fmt: skip
        files = []
        for ref in todo:
            path, size, sha = download(ref, client, folder)
            result = process_file(ref, path, size, sha, stop_after=stop_after)
            files.append(file_report(ref.name))
            if result.status == OffDeltaFile.Status.DONE and not keep_files:
                path.unlink(missing_ok=True)
            if result.status != OffDeltaFile.Status.DONE:
                report["stopped"] = f"{ref.name}: {result.message}"
                break
        report["files"] = files
        state.gap = gap
        state.synced_until = chain_end(state)
        report["synced_until"] = ts(state.synced_until)
        report["status"] = "failed" if report.get("stopped") else "ok"
    except (SyncError, httpx.HTTPError) as exc:
        report.update(status="failed", stopped=f"{type(exc).__name__}: {exc}")
    finally:
        report["seconds"] = round(time.monotonic() - began, 1)
        state.last_run_at = timezone.now()
        state.last_report = report
        state.save()
        if own_client:
            client.close()
        _unlock()
    return report


def describe() -> dict[str, Any]:
    """For the staff dashboard."""
    state = OffDeltaState.objects.filter(pk=1).first()
    return {
        "configured": state is not None and state.start_from is not None,
        "synced_until": ts(state.synced_until) if state else "-",
        "start_from": ts(state.start_from) if state else "-",
        "gap": state.gap if state else "",
        "last_run_at": state.last_run_at if state else None,
        "last_status": (state.last_report or {}).get("status", "") if state else "",
        "policy": policy(),
        "recent": list(OffDeltaFile.objects.order_by("-start_ts")[:5]),
    }
