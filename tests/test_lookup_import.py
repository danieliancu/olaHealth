import threading
from datetime import timedelta
from decimal import Decimal

import httpx
import pytest
import respx
from django.db import connection
from django.utils import timezone

from catalog.models import LookupState, NutrientFact, Product, ProductRevision, ReviewFlag
from catalog.services import lookup as lookup_service
from catalog.services.importer import import_off_product
from off.client import OFFClient

URL = "https://off.test/api/v2/product/5941234567899.json"
pytestmark = pytest.mark.django_db


def make_client():
    return OFFClient(
        base_url="https://off.test", user_agent="t", sleep=lambda s: None, max_retries=0
    )


@respx.mock
def test_absent_product_is_fetched_once_and_persisted(off_json):
    route = respx.get(URL).mock(return_value=httpx.Response(200, json=off_json()))
    first = lookup_service.lookup("5941234567899", "ean13", client=make_client())
    assert first.status == "found" and first.source == "off"
    for _ in range(5):  # repeated scans are served locally
        again = lookup_service.lookup("5941234567899", "ean13", client=make_client())
        assert again.status == "found" and again.source == "local"
    assert route.call_count == 1
    assert LookupState.objects.get(off_code="5941234567899").off_requests == 1
    product = first.product
    assert product.name == "Greek style yogurt"
    assert product.current_revision.field_sources["name"]["source"] == "off"
    assert product.identifiers.get().raw_value == "5941234567899"


@respx.mock
def test_not_found_is_negatively_cached_but_errors_are_not(settings):
    settings.OFF_CIRCUIT_SECONDS = 0  # circuit breaker covered by its own test
    route = respx.get(URL).mock(return_value=httpx.Response(404, json={"status": 0}))
    assert lookup_service.lookup("5941234567899", client=make_client()).status == "not_found"
    assert lookup_service.lookup("5941234567899", client=make_client()).status == "not_found"
    assert route.call_count == 1

    LookupState.objects.all().delete()
    route.mock(side_effect=httpx.ConnectTimeout("x"))
    r = lookup_service.lookup("5941234567899", client=make_client())
    assert (r.status, r.error) == ("error", "timeout")
    lookup_service.lookup("5941234567899", client=make_client())
    assert route.call_count == 3  # timeouts are never cached


def test_invalid_code_never_calls_off():
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(URL)
        r = lookup_service.lookup("5941234567890", client=make_client())  # bad check digit
        assert r.error == "invalid_code"
        assert route.call_count == 0


@respx.mock
def test_stale_product_is_returned_and_refresh_scheduled(off_json, queued_tasks):
    respx.get(URL).mock(return_value=httpx.Response(200, json=off_json()))
    product = lookup_service.lookup("5941234567899", client=make_client()).product
    Product.objects.filter(pk=product.pk).update(off_synced_at=timezone.now() - timedelta(days=30))
    r = lookup_service.lookup("5941234567899", client=make_client())
    assert r.status == "found" and r.stale and r.refresh_scheduled
    assert r.product.name == "Greek style yogurt"
    r2 = lookup_service.lookup("5941234567899", client=make_client())
    assert r2.refresh_scheduled is False  # deduplicated
    assert [c[0] for c in queued_tasks] == ["catalog.tasks.refresh_product"]


@respx.mock
def test_refresh_failure_keeps_local_data(off_json):
    route = respx.get(URL).mock(return_value=httpx.Response(200, json=off_json()))
    product = lookup_service.lookup("5941234567899", client=make_client()).product
    revision_id = product.current_revision_id
    route.mock(return_value=httpx.Response(503))
    assert lookup_service.refresh(product, client=make_client()) == "upstream_unavailable"
    product.refresh_from_db()
    assert product.current_revision_id == revision_id
    assert product.name == "Greek style yogurt"
    assert product.sync_label == "refresh_failed"
    snapshot = product.current_revision.assessments.get(
        indicator="nutriscore", origin="off_computed"
    )
    assert snapshot.data_status == "available"  # a temporary error does not cancel it


def test_held_lease_returns_pending():
    from core.coordination import acquire_lease

    assert acquire_lease("off-lookup:5941234567899", 30)
    r = lookup_service.lookup("5941234567899", client=make_client())
    assert r.status == "pending"


@pytest.mark.django_db(transaction=True)
@respx.mock
def test_concurrent_lookups_make_a_single_off_request(off_json):
    gate = threading.Event()

    def slow_response(request):
        gate.wait(2)
        return httpx.Response(200, json=off_json())

    route = respx.get(URL).mock(side_effect=slow_response)
    results = []

    def worker():
        try:
            results.append(lookup_service.lookup("5941234567899", client=make_client()).status)
        finally:
            connection.close()

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    gate.set()
    for t in threads:
        t.join()
    assert route.call_count == 1
    assert "found" in results
    assert set(results) <= {"found", "pending"}
    assert Product.objects.count() == 1


def test_reimport_is_idempotent(off_payload):
    p = import_off_product(off_payload())
    p2 = import_off_product(off_payload())
    assert p.pk == p2.pk
    assert ProductRevision.objects.filter(product=p).count() == 1
    import_off_product(off_payload(last_modified_t=1760000999))  # only metadata changed
    assert ProductRevision.objects.filter(product=p).count() == 1
    import_off_product(off_payload(product_name="Greek yogurt 2"))
    assert ProductRevision.objects.filter(product=p).count() == 2


def test_html_entities_from_off_become_real_characters(off_payload):
    p = import_off_product(
        off_payload(product_name="Biscuits &quot;lotus&quot; maxi pack", brands="Lotus &amp; Co")
    )
    assert (p.name, p.brand) == ('Biscuits "lotus" maxi pack', "Lotus & Co")
    assert p.current_revision.data["name"] == 'Biscuits "lotus" maxi pack'


def test_revisions_are_immutable(off_payload):
    from catalog.models import ImmutableError

    rev = import_off_product(off_payload()).current_revision
    rev.data = {}
    with pytest.raises(ImmutableError):
        rev.save()


def test_zero_is_kept_and_unknown_is_absent(off_payload):
    payload = off_payload()
    payload["nutriments"].pop("salt_100g")
    payload["nutriments"]["sodium_modifier"] = "-"
    p = import_off_product(payload)
    facts = {f.nutrient: f for f in NutrientFact.objects.filter(revision=p.current_revision)}
    assert facts["fiber"].value == Decimal("0") and facts["fiber"].status == "declared"
    assert "salt" not in facts  # unknown -> no fact, never 0
    assert facts["sodium"].status == "not_on_label" and facts["sodium"].value is None


def test_empty_allergens_are_unknown_not_absent(off_payload):
    p = import_off_product(off_payload(allergens_tags=[]))
    assert p.current_revision.data["allergens"] == {"status": "unknown", "items": []}


def test_only_allow_listed_image_hosts(off_payload):
    p = import_off_product(off_payload())
    urls = list(p.images.values_list("remote_url", flat=True))
    assert urls == [off_payload()["image_front_url"]]
    assert p.front_image_url.startswith("/images/off/")  # served from the local cache


def test_locked_fields_survive_reimport_and_raise_one_conflict(off_payload):
    from catalog.services.revisions import publish_revision

    p = import_off_product(off_payload())
    data = dict(p.current_revision.data, name="Corrected name")
    sources = dict(p.current_revision.field_sources, name={"source": "contribution"})
    publish_revision(p, data, source="contribution", field_sources=sources)
    p.locked_fields = ["name"]
    p.save()
    for _ in range(2):
        import_off_product(off_payload(product_name="OFF renamed it"))
    p.refresh_from_db()
    assert p.name == "Corrected name"
    assert p.current_revision.off_data["name"] == "OFF renamed it"
    flags = ReviewFlag.objects.filter(kind="data_conflict", field="name", status="open")
    assert flags.count() == 1


def test_identifier_owned_by_another_product_is_not_merged(off_payload):
    from catalog.models import ProductIdentifier
    from off.codes import parse_code

    other = Product.objects.create(off_code="9999999999994")
    ProductIdentifier.objects.create(
        product=other,
        raw_value="5941234567899",
        normalized="05941234567899",
        off_code="9999999999994",
        kind="gtin",
    )
    p = import_off_product(off_payload(), parse_code("5941234567899"))
    assert p.pk != other.pk
    assert ProductIdentifier.objects.get(normalized="05941234567899").product_id == other.pk
    assert ReviewFlag.objects.filter(kind="identity_conflict").count() == 1


def test_explicit_none_allergens_are_not_an_allergen(off_payload):
    p = import_off_product(off_payload(allergens_tags=["en:none"]))
    assert p.current_revision.data["allergens"] == {"status": "none_declared", "items": []}


@respx.mock
def test_outage_opens_circuit_so_next_users_do_not_wait(settings):
    settings.OFF_CIRCUIT_SECONDS = 60
    route = respx.get(URL).mock(return_value=httpx.Response(504))
    r = lookup_service.lookup("5941234567899", client=make_client())
    assert (r.status, r.error) == ("error", "upstream_unavailable")
    calls = route.call_count
    r2 = lookup_service.lookup("5941234567899", client=make_client())
    assert (r2.status, r2.error) == ("error", "upstream_unavailable")
    assert route.call_count == calls  # answered at once, OFF not called again


@respx.mock
def test_not_found_does_not_open_circuit(off_json):
    respx.get(URL).mock(return_value=httpx.Response(404, json={"status": 0}))
    lookup_service.lookup("5941234567899", client=make_client())
    assert not lookup_service.off_circuit_open()
