"""API mode now, local OFF catalogue after a verified import (docs/off-local-catalogue.md)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx
from django.core.cache import cache
from django.core.management import call_command
from django.utils import timezone

from assessments.score import core, score_for_product, score_from_off
from catalog.models import CatalogImport, Product, ProductRevision
from catalog.services import lookup as lookup_service
from catalog.services.alternatives import alternatives
from catalog.services.catalog_import import run_jsonl
from catalog.services.catalog_mode import current_mode, local_catalogue_active
from catalog.services.importer import import_off_product
from catalog.tasks import refresh_stale_products
from off.client import OFFClient
from tests.nutri import nutriscore_fields

pytestmark = pytest.mark.django_db

REAL = Path(__file__).parent / "fixtures" / "off" / "real"
EXPORTED = datetime(2026, 11, 20, tzinfo=UTC)
BASE = "https://off.test"


def real_payloads() -> list[dict]:
    out = []
    for path in sorted(REAL.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        data.pop("_fixture")
        out.append(data)
    return out


def write_jsonl(path: Path, payloads: list[dict], extra_lines: tuple[str, ...] = ()) -> Path:
    lines = [json.dumps(p, ensure_ascii=False) for p in payloads] + list(extra_lines)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def small_dump_ok(settings):
    settings.OFF_DUMP_MIN_PRODUCTS = 1


def client():
    return OFFClient(base_url=BASE, user_agent="t", sleep=lambda s: None, max_retries=0)


def set_site(**values):
    from staff.models import SiteSettings

    site = SiteSettings.load()
    for k, v in values.items():
        setattr(site, k, v)
    site.save()


# --- Mode resolution ------------------------------------------------------------------------


def test_api_mode_until_an_import_is_verified():
    assert current_mode() == "api"
    for status in ("running", "failed", "completed", "rejected"):
        CatalogImport.objects.create(status=status)
        assert current_mode() == "api", status
    CatalogImport.objects.create(status="verified", verified_at=timezone.now())
    assert current_mode() == "local" and local_catalogue_active()


def test_operator_can_force_the_api_mode():
    CatalogImport.objects.create(status="verified", verified_at=timezone.now())
    set_site(catalog_mode="api")
    assert current_mode() == "api"
    set_site(catalog_mode="auto")
    assert current_mode() == "local"


def test_a_failed_import_after_a_verified_one_changes_nothing():
    CatalogImport.objects.create(status="verified", verified_at=timezone.now())
    CatalogImport.objects.create(status="failed")
    assert current_mode() == "local"


# --- Import and verification ------------------------------------------------------------------


def test_valid_import_is_verified_and_switches_automatically(tmp_path, small_dump_ok):
    assert current_mode() == "api"
    run = run_jsonl(write_jsonl(tmp_path / "dump.jsonl", real_payloads()), exported_at=EXPORTED)
    assert run.status == "verified", run.report["checks"]
    assert run.imported == 16 and run.failed == 0 and run.seen == 16
    assert all(c["ok"] for c in run.report["checks"])
    assert len(run.file_sha256) == 64
    assert current_mode() == "local"
    product = Product.objects.get(off_code="5449000054227")
    assert product.off_sync_source == "dump" and product.off_synced_at == EXPORTED
    assert product.current_revision.source == ProductRevision.Source.OFF_DUMP
    assert product.current_revision.foodo_score["source"] == "off_dump"


def test_partial_or_bad_import_does_not_switch(tmp_path, settings):
    settings.OFF_DUMP_MIN_PRODUCTS = 100  # 14 lines are not enough
    run = run_jsonl(write_jsonl(tmp_path / "dump.jsonl", real_payloads()), exported_at=EXPORTED)
    assert run.status == "rejected"
    assert not next(c for c in run.report["checks"] if c["check"] == "enough_products")["ok"]
    assert current_mode() == "api"


def test_too_many_broken_lines_are_rejected(tmp_path, small_dump_ok):
    path = write_jsonl(tmp_path / "dump.jsonl", real_payloads()[:3], ("{not json", "[1, 2]"))
    run = run_jsonl(path, exported_at=EXPORTED)
    assert run.failed == 2 and run.imported == 3
    assert run.status == "rejected" and current_mode() == "api"
    assert len(run.report["errors"]) == 2


def test_dump_without_an_indicator_field_is_rejected(tmp_path, small_dump_ok):
    payloads = [{k: v for k, v in p.items() if k != "nova_group"} for p in real_payloads()]
    run = run_jsonl(write_jsonl(tmp_path / "dump.jsonl", payloads), exported_at=EXPORTED)
    check = next(c for c in run.report["checks"] if c["check"] == "fields_present")
    assert not check["ok"] and "nova_group" in check["detail"]
    assert run.status == "rejected"


def test_interrupted_import_is_failed(tmp_path, small_dump_ok, monkeypatch):
    def boom(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr("catalog.services.importer.import_off_product", boom)
    with pytest.raises(KeyboardInterrupt):
        run_jsonl(write_jsonl(tmp_path / "d.jsonl", real_payloads()[:2]), exported_at=EXPORTED)
    run = CatalogImport.objects.get()
    assert run.status == "failed" and "KeyboardInterrupt" in run.notes
    assert current_mode() == "api"


def test_dump_never_replaces_a_newer_copy_nor_approved_corrections(tmp_path, small_dump_ok):
    payload = real_payloads()[0]
    newer = {**payload, "last_modified_t": payload["last_modified_t"] + 1000, "brands": "Newer"}
    product = import_off_product(newer)
    revisions = product.revisions.count()
    run = run_jsonl(write_jsonl(tmp_path / "d.jsonl", [payload]), exported_at=EXPORTED)
    assert run.skipped == 1 and run.imported == 0
    product.refresh_from_db()
    assert product.brand == "Newer" and product.revisions.count() == revisions
    assert product.off_sync_source == "api"


def test_import_command_and_reverification(tmp_path, small_dump_ok):
    path = write_jsonl(tmp_path / "dump.jsonl", real_payloads())
    call_command("import_off_dump", str(path), "--exported-at", "2026-11-20")
    run = CatalogImport.objects.get()
    assert run.status == "verified" and run.exported_at == EXPORTED
    call_command("verify_catalog_import", str(run.pk))
    assert CatalogImport.objects.get().status == "verified"


def test_country_filter_and_limit(tmp_path, small_dump_ok):
    run = run_jsonl(
        write_jsonl(tmp_path / "d.jsonl", real_payloads()),
        exported_at=EXPORTED,
        country="en:united-kingdom",
        limit=5,
        verify_after=False,
    )
    assert run.seen == 5 and run.imported + run.skipped == 5
    assert run.status == "completed"


# --- Same result from the API and from the dump -------------------------------------------------


@pytest.mark.parametrize("code", ["5449000054227", "75041670", "10614594", "3017624010701"])
def test_same_score_from_api_and_dump(tmp_path, small_dump_ok, code):
    payload = next(p for p in real_payloads() if p["code"] == code)
    run_jsonl(
        write_jsonl(tmp_path / "d.jsonl", [payload]), exported_at=EXPORTED, verify_after=False
    )
    from_dump = score_for_product(Product.objects.get(off_code=code))
    Product.objects.all().delete()
    from_api = score_for_product(import_off_product(payload))
    assert core(from_dump) == core(from_api) == core(score_from_off(payload))
    assert (from_dump["source"], from_api["source"]) == ("off_dump", "off_api")


def test_nova_1_without_nutriscore_is_not_enough_data_from_both_sources(
    tmp_path, small_dump_ok, off_payload
):
    payload = off_payload(
        nutriscore_grade="unknown", nutriscore_score=None, nutriscore={}, nova_group=1
    )
    run_jsonl(
        write_jsonl(tmp_path / "d.jsonl", [payload]), exported_at=EXPORTED, verify_after=False
    )
    from_dump = score_for_product(Product.objects.get())
    assert from_dump["status"] == "not_enough_data"
    assert from_dump["separate"]["nova"]["group"] == 1
    assert from_dump["nutriscore"]["grade"] is None and "score" not in from_dump
    Product.objects.all().delete()
    assert core(score_for_product(import_off_product(payload))) == core(from_dump)


# --- Behaviour in local mode ------------------------------------------------------------------


def test_local_mode_missing_product_is_fetched_from_the_api_and_saved(off_json):
    CatalogImport.objects.create(status="verified", verified_at=timezone.now())
    url = f"{BASE}/api/v2/product/5941234567899.json"
    with respx.mock() as mock:
        route = mock.get(url).mock(return_value=httpx.Response(200, json=off_json()))
        first = lookup_service.lookup("5941234567899", client=client())
        again = lookup_service.lookup("5941234567899", client=client())
    assert (first.status, first.source) == ("found", "off")
    assert (again.status, again.source) == ("found", "local") and route.call_count == 1
    assert first.product.off_sync_source == "api"


def test_local_copy_is_kept_when_the_api_is_down(tmp_path, small_dump_ok, queued_tasks):
    run_jsonl(write_jsonl(tmp_path / "d.jsonl", real_payloads()), exported_at=EXPORTED)
    Product.objects.update(off_synced_at=timezone.now() - timedelta(days=60))  # stale
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(url__regex=r".*").mock(return_value=httpx.Response(503))
        result = lookup_service.lookup("5449000054227", client=client())
        product = result.product
        assert lookup_service.refresh(product, client=client()) == "upstream_unavailable"
    assert result.status == "found" and result.source == "local" and result.stale
    assert result.refresh_scheduled  # in the background, rate-limited
    assert route.call_count == 1  # only the explicit refresh
    product.refresh_from_db()
    assert product.name and product.current_revision.source == ProductRevision.Source.OFF_DUMP
    assert score_for_product(product)["status"] == "ok"


def test_periodic_refresh_leaves_dump_products_to_the_next_dump(
    tmp_path, small_dump_ok, monkeypatch
):
    run_jsonl(write_jsonl(tmp_path / "d.jsonl", real_payloads()[:2]), exported_at=EXPORTED)
    Product.objects.update(off_synced_at=timezone.now() - timedelta(days=60))
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(url__regex=r".*")
        assert refresh_stale_products() == {}
        assert route.call_count == 0
    # API mode again (operator): the same products are refreshed as before.
    set_site(catalog_mode="api")
    calls = []
    monkeypatch.setattr(
        "catalog.tasks.lookup_service.refresh", lambda p: calls.append(p.off_code) or "ok"
    )
    refresh_stale_products()
    assert len(calls) == 2


def test_alternatives_use_the_local_catalogue_only_once_active(off_payload):
    cache.clear()
    cats = ["en:dairies", "en:yogurts"]
    uk = ["en:united-kingdom"]
    ref = import_off_product(
        off_payload(code="5000000000001", **nutriscore_fields("d"), categories_tags=cats,
                    countries_tags=uk, product_name="Sweet yogurt")
    )  # fmt: skip
    import_off_product(
        off_payload(code="5000000000002", **nutriscore_fields("a"), categories_tags=cats,
                    countries_tags=uk, product_name="Plain yogurt")
    )  # fmt: skip
    search = f"{BASE}/api/v2/search"
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(search).mock(
            return_value=httpx.Response(200, json={"count": 0, "products": []})
        )
        api_mode = alternatives(ref, client=client())
        assert route.call_count == 1  # API mode: OFF search
        cache.clear()
        CatalogImport.objects.create(status="verified", verified_at=timezone.now())
        local_mode = alternatives(ref, client=client())
        assert route.call_count == 1  # local mode: no OFF search
    assert [a["name"] for a in api_mode] == [a["name"] for a in local_mode] == ["Plain yogurt"]
