"""Daily Open Food Facts delta sync (catalog/services/delta.py). The index and the files are
served by respx from bytes built here; no network."""

from __future__ import annotations

import gzip
import json

import httpx
import psycopg
import pytest
import respx
from django.db import connection

from catalog.models import CatalogImport, OffDeltaFile, OffDeltaState, Product, ProductRevision
from catalog.services import catalog_mode, delta
from catalog.services.importer import import_off_product

INDEX = "https://static.test/data/delta/index.txt"
BASE = "https://static.test/data/delta/"
UK, FR = "en:united-kingdom", "en:france"
T0 = 1_795_000_000  # an OFF timestamp (UTC seconds)
DAY = 86_400


@pytest.fixture(autouse=True)
def _settings(settings, tmp_path):
    settings.OFF_DELTA_INDEX_URL = INDEX
    settings.OFF_DELTA_DIR = tmp_path / "offdelta"
    settings.OFF_DELTA_NEW_PRODUCT_COUNTRIES = []


def ean(n: int) -> str:
    """A valid EAN-13 (check digit computed) from a 12-digit body."""
    body = f"500000{n:06d}"
    total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(body))
    return body + str((10 - total % 10) % 10)


def name(start, end):
    return f"openfoodfacts_products_{start}_{end}.json.gz"


def gz(lines):
    return gzip.compress(b"".join(json.dumps(x).encode() + b"\n" for x in lines))


def serve(mock, files, *, index_extra=""):
    """``files``: {file name: bytes}. The index lists them newest first, as OFF does."""
    names = sorted(files, reverse=True)
    mock.get(INDEX).mock(
        return_value=httpx.Response(200, text="\n".join(names) + "\n" + index_extra)
    )
    for n, body in files.items():
        mock.get(BASE + n).mock(return_value=httpx.Response(200, content=body))


@pytest.fixture
def catalogue(db, off_payload):
    """Two products already known: one from a dump, one from the API."""
    dumped = import_off_product(
        off_payload(code=ean(201), product_name="Dump yogurt", last_modified_t=T0 - DAY),
        source=ProductRevision.Source.OFF_DUMP,
    )
    api = import_off_product(
        off_payload(code=ean(202), product_name="API yogurt", last_modified_t=T0 - DAY)
    )
    return {"dumped": dumped, "api": api}


def line(off_payload, code, name_, modified, **changes):
    return off_payload(code=code, product_name=name_, last_modified_t=modified, **changes)


def revisions():
    return ProductRevision.objects.count()


def test_index_is_read_oldest_first_and_ignores_other_lines():
    refs = delta.parse_index(
        f"{name(T0 + DAY, T0 + 2 * DAY)}\n{name(T0, T0 + DAY)}\nREADME.txt\n\n"
    )
    assert [(r.start_ts, r.end_ts) for r in refs] == [(T0, T0 + DAY), (T0 + DAY, T0 + 2 * DAY)]


def test_policy_known_products_updated_new_ones_only_for_configured_markets(
    catalogue, off_payload, settings
):
    mode_before = catalog_mode.local_catalogue_active()
    imports_before = CatalogImport.objects.count()
    lines = [
        line(off_payload, ean(201), "Dump yogurt v2", T0 + 10),
        # OFF moved the API product to another country: still a product we know.
        line(off_payload, ean(202), "API yogurt v2", T0 + 20, countries_tags=[FR]),
        line(off_payload, ean(203), "New UK yogurt", T0 + 30, countries_tags=[UK]),
        line(off_payload, ean(204), "New French yogurt", T0 + 40, countries_tags=[FR]),
        line(off_payload, "12345", "Bad barcode", T0 + 50),
    ]
    settings.OFF_DELTA_NEW_PRODUCT_COUNTRIES = [UK]
    with respx.mock(assert_all_called=False) as mock:
        serve(mock, {name(T0, T0 + DAY): gz(lines)})
        report = delta.run(since=T0 + 1)
    assert report["status"] == "ok" and report["policy"]["new_products"] == UK
    f = report["files"][0]
    assert (f["read"], f["added"], f["updated"], f["unchanged"], f["skipped"], f["errors"]) == (
        5, 1, 2, 0, 2, 0,
    )  # fmt: skip
    assert f["skip_reasons"] == {"not_in_policy": 1, "invalid_code": 1}
    assert Product.objects.get(off_code=ean(202)).name == "API yogurt v2"
    assert Product.objects.get(off_code=ean(202)).countries == [FR]
    assert Product.objects.filter(off_code=ean(203)).exists()
    assert not Product.objects.filter(off_code=ean(204)).exists()
    state = OffDeltaState.load()
    assert state.synced_until == T0 + DAY and state.gap == ""
    # A delta is never a catalogue import: the mode cannot change.
    assert CatalogImport.objects.count() == imports_before
    assert catalog_mode.local_catalogue_active() == mode_before


def test_older_line_never_overwrites_a_newer_sheet(catalogue, off_payload):
    with respx.mock(assert_all_called=False) as mock:
        serve(mock, {name(T0, T0 + DAY): gz(
            [line(off_payload, ean(201), "Older name", T0 - 2 * DAY)]
        )})  # fmt: skip
        report = delta.run(since=T0 + 1)
    assert report["files"][0]["skip_reasons"] == {"unchanged_older": 1}
    assert Product.objects.get(off_code=ean(201)).name == "Dump yogurt"


def test_files_in_chronological_order_and_repeat_changes_nothing(catalogue, off_payload):
    first = gz([line(off_payload, ean(201), "Name day 1", T0 + 10)])
    second = gz([line(off_payload, ean(201), "Name day 2", T0 + DAY + 10)])
    files = {name(T0, T0 + DAY): first, name(T0 + DAY, T0 + 2 * DAY): second}
    with respx.mock(assert_all_called=False) as mock:
        serve(mock, files)
        report = delta.run(since=T0 + 1)
        assert [f["file"] for f in report["files"]] == sorted(files)
        assert Product.objects.get(off_code=ean(201)).name == "Name day 2"
        assert OffDeltaState.load().synced_until == T0 + 2 * DAY
        count = revisions()
        # Nothing left to do.
        assert delta.run()["files"] == []
        # The same file once more (forced): no new product or revision, the newer name stays.
        OffDeltaFile.objects.filter(name=name(T0, T0 + DAY)).delete()
        again = delta.run(only=name(T0, T0 + DAY))
    assert again["files"][0]["unchanged"] == 1 and again["files"][0]["updated"] == 0
    assert revisions() == count and Product.objects.count() == 2
    assert Product.objects.get(off_code=ean(201)).name == "Name day 2"


def test_resume_after_interruption_gives_the_same_result(catalogue, off_payload, settings):
    settings.OFF_DELTA_NEW_PRODUCT_COUNTRIES = ["all"]
    lines = [line(off_payload, ean(300 + i), f"P{i}", T0 + i) for i in range(1, 9)]
    lines.append(line(off_payload, ean(201), "Dump yogurt v3", T0 + 99))
    f = name(T0, T0 + DAY)
    with respx.mock(assert_all_called=False) as mock:
        serve(mock, {f: gz(lines)})
        with pytest.raises(KeyboardInterrupt):
            delta.run(since=T0 + 1, stop_after=4)
        row = OffDeltaFile.objects.get(name=f)
        assert row.status == "running" and row.next_line == 5
        assert OffDeltaState.load().synced_until is None  # not complete: not synced
        report = delta.run()
    assert report["files"][0]["status"] == "done"
    assert report["files"][0]["added"] == 8 and report["files"][0]["updated"] == 1
    assert Product.objects.count() == 10 and revisions() == 2 + 8 + 1
    assert OffDeltaState.load().synced_until == T0 + DAY


def test_one_run_at_a_time(catalogue):
    params = connection.get_connection_params()
    other = psycopg.connect(
        **{k: v for k, v in params.items() if k in ("dbname", "user", "password", "host", "port")}
    )
    try:
        other.execute("SELECT pg_advisory_lock(%s)", [delta.LOCK_KEY])
        with pytest.raises(delta.Locked):
            delta.run(since=T0)
    finally:
        other.close()


def test_truncated_file_fails_and_is_not_marked_synced(catalogue, off_payload):
    body = gz([line(off_payload, ean(201), "X", T0 + 10)] * 50)
    f = name(T0, T0 + DAY)
    with respx.mock(assert_all_called=False) as mock:
        serve(mock, {f: body[: len(body) // 2]})
        report = delta.run(since=T0 + 1)
        assert report["status"] == "failed" and OffDeltaFile.objects.get(name=f).status == "failed"
        assert OffDeltaState.load().synced_until is None
        serve(mock, {f: body})  # OFF serves the whole file again: retried from line 1
        report = delta.run()
    assert report["status"] == "ok" and OffDeltaState.load().synced_until == T0 + DAY


def test_empty_or_irrelevant_delta_is_fine(catalogue, off_payload):
    files = {
        name(T0, T0 + DAY): gz([]),
        name(T0 + DAY, T0 + 2 * DAY): gz(
            [line(off_payload, ean(299), "Not ours", T0 + DAY + 5, countries_tags=[FR])]
        ),
    }
    with respx.mock(assert_all_called=False) as mock:
        serve(mock, files)
        report = delta.run(since=T0 + 1)
    assert report["status"] == "ok" and [f["status"] for f in report["files"]] == ["done"] * 2
    assert OffDeltaState.load().synced_until == T0 + 2 * DAY


def test_gap_when_the_oldest_delta_starts_after_the_start_point(catalogue, off_payload):
    with respx.mock(assert_all_called=False) as mock:
        serve(mock, {name(T0 + DAY, T0 + 2 * DAY): gz([])})
        report = delta.run(since=T0)
    state = OffDeltaState.load()
    assert "oldest delta" in report["gap"] and state.gap
    assert state.synced_until is None  # never marked synced past the hole


def test_local_correction_survives_a_delta(catalogue, off_payload):
    product = catalogue["dumped"]
    product.locked_fields = ["name"]
    product.save(update_fields=["locked_fields"])
    rev = product.current_revision
    ProductRevision.objects.filter(pk=rev.pk).update(
        data={**rev.data, "name": "Local name"}, field_sources={"name": "contribution"}
    )
    with respx.mock(assert_all_called=False) as mock:
        serve(mock, {name(T0, T0 + DAY): gz(
            [line(off_payload, ean(201), "OFF name", T0 + 10)]
        )})  # fmt: skip
        delta.run(since=T0 + 1)
    product.refresh_from_db()
    assert product.current_revision.data["name"] == "Local name"


def test_start_point_from_the_archive(catalogue):
    start, note = delta.provenance()
    newest = ProductRevision.objects.filter(source="off_dump").latest("off_last_modified")
    assert start == int(newest.off_last_modified.timestamp()) - 48 * 3600
    assert "overlap" in note
