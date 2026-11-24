"""Dump import with real dump lines: photos from metadata, trimmed payload, re-imports and
local corrections (docs/off-local-catalogue.md)."""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime

import pytest
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.urls import reverse

from assessments.rules import derive_from_off
from assessments.score import core, score_from_off
from catalog.models import Product, ProductImage, ProductRevision
from catalog.services.catalog_import import run_jsonl
from catalog.services.importer import import_off_product
from contributions.models import Contribution, ContributionImage
from contributions.services.moderation import approve
from off.fields import STORED_FIELDS, trim_payload
from off.images import built_urls
from off.mapping import map_product
from tests.test_off_images import dump_lines

pytestmark = pytest.mark.django_db

EXPORTED = datetime(2026, 11, 20, tzinfo=UTC)


def write(path, payloads):
    path.write_text("\n".join(json.dumps(p, ensure_ascii=False) for p in payloads) + "\n")
    return path


def active(product, kind=None):
    qs = product.images.filter(is_active=True)
    return qs.filter(kind=kind) if kind else qs


def newer(payload, **changes):
    return {
        **copy.deepcopy(payload),
        "last_modified_t": payload["last_modified_t"] + 3600,
        **changes,
    }


@pytest.fixture
def small_dump_ok(settings):
    settings.OFF_DUMP_MIN_PRODUCTS = 1


def test_trimmed_payload_maps_and_scores_like_the_full_line():
    for name, p in dump_lines().items():
        t = trim_payload(p)
        assert map_product(t) == map_product(p), name
        assert derive_from_off(t) == derive_from_off(p), name
        assert core(score_from_off(t)) == core(score_from_off(p)), name


def test_dump_import_photos_from_metadata_without_downloads(tmp_path, small_dump_ok, queued_tasks):
    lines = dump_lines()
    run = run_jsonl(write(tmp_path / "d.jsonl", list(lines.values())), exported_at=EXPORTED)
    assert run.status == "verified", run.report
    for name, p in lines.items():
        product = Product.objects.get(off_code=p["code"])
        got = {i.kind: i.remote_url for i in active(product)}
        assert got == built_urls(p), name
        assert all(i.license.startswith("CC BY-SA") for i in active(product))
        if got.get("front"):
            assert product.front_image_url  # served through our off-image view
    assert not [c for c in queued_tasks if c[0] == "catalog.tasks.cache_off_images"]


def test_stored_payload_is_trimmed(tmp_path, small_dump_ok):
    lines = dump_lines()
    run_jsonl(write(tmp_path / "d.jsonl", list(lines.values())), exported_at=EXPORTED)
    for name, p in lines.items():
        stored = Product.objects.get(off_code=p["code"]).current_revision.off_payload
        assert set(stored) <= set(STORED_FIELDS), name
        assert stored == trim_payload(p), name


def test_ingredient_language_falls_back_to_lang_without_ingredients_lang():
    for name, p in dump_lines().items():
        assert "ingredients_lang" not in p
        data = map_product(trim_payload(p))
        if data["ingredients"]["text"]:
            assert data["ingredients"]["lang"] == p.get("lang"), name


def test_reimport_of_the_same_file_changes_nothing(tmp_path, small_dump_ok):
    path = write(tmp_path / "d.jsonl", list(dump_lines().values()))
    run_jsonl(path, exported_at=EXPORTED)
    counts = (
        Product.objects.count(),
        ProductRevision.objects.count(),
        ProductImage.objects.count(),
    )
    run = run_jsonl(path, exported_at=EXPORTED)
    assert run.status == "verified" and run.failed == 0
    assert counts == (
        Product.objects.count(),
        ProductRevision.objects.count(),
        ProductImage.objects.count(),
    )


def test_missing_metadata_keeps_valid_photos():
    p = dump_lines()["new_format"]
    product = import_off_product(p, source="off_dump", queue_images=False)
    before = sorted(active(product).values_list("kind", "remote_url"))
    assert before
    without = newer(p)
    del without["images"]
    import_off_product(without, source="off_dump", queue_images=False)
    partial = newer(without, images={"selected": {}})
    import_off_product(partial, source="off_dump", queue_images=False)
    assert sorted(active(product).values_list("kind", "remote_url")) == before


def test_new_revision_of_a_photo_replaces_only_that_kind():
    p = dump_lines()["new_format"]
    product = import_off_product(p, source="off_dump", queue_images=False)
    kinds = dict(active(product).values_list("kind", "remote_url"))
    changed = newer(p)
    lc = next(iter(changed["images"]["selected"]["front"]))
    changed["images"]["selected"]["front"] = {lc: {"imgid": "1", "rev": "999"}}
    import_off_product(changed, source="off_dump", queue_images=False)
    after = dict(active(product).values_list("kind", "remote_url"))
    assert after["front"].endswith(f"front_{lc}.999.400.jpg")
    assert {k: v for k, v in after.items() if k != "front"} == {
        k: v for k, v in kinds.items() if k != "front"
    }


def test_api_refresh_without_a_photo_kind_keeps_it(off_payload):
    url = "https://images.openfoodfacts.org/images/products/301/762/042/2003/nutrition_en.5.400.jpg"
    product = import_off_product(off_payload(image_nutrition_url=url))
    assert active(product, "nutrition").get().remote_url == url
    payload = off_payload()
    payload.pop("image_nutrition_url", None)
    import_off_product(newer(payload, brands="Refreshed"))
    assert active(product, "nutrition").get().remote_url == url


def _approved_front_photo(product, moderator):
    c = Contribution.objects.create(
        product=product,
        base_revision=product.current_revision,
        problem_type=Contribution.ProblemType.PHOTOS,
        status=Contribution.Status.SUBMITTED,
        consent_publication=True,
    )
    ContributionImage.objects.create(
        contribution=c,
        kind="front",
        file=ContentFile(b"\xff\xd8\xff\xd9", name="front.jpg"),
        width=10,
        height=10,
        sha256="0" * 64,
    )
    assert approve(c.pk, moderator).status == "approved"


def test_approved_local_photo_and_correction_survive_a_dump_update(admin_user):
    p = dump_lines()["new_format"]
    product = import_off_product(p, source="off_dump", queue_images=False)
    _approved_front_photo(product, admin_user)
    c = Contribution.objects.create(
        product=product,
        base_revision=product.current_revision,
        problem_type=Contribution.ProblemType.INCORRECT_DATA,
        proposed={"name": "Local name"},
        status=Contribution.Status.SUBMITTED,
        consent_publication=True,
    )
    approve(c.pk, admin_user)
    product.refresh_from_db()
    revisions = product.revisions.count()

    changed = newer(p, product_name="Name from OFF")
    lc = next(iter(changed["images"]["selected"]["front"]))
    changed["images"]["selected"]["front"] = {lc: {"imgid": "1", "rev": "999"}}
    import_off_product(changed, source="off_dump", queue_images=False)

    product.refresh_from_db()
    assert product.name == "Local name"
    assert product.current_revision.off_data["name"] == "Name from OFF"
    assert product.revisions.count() == revisions + 1
    assert product.flags.filter(kind="data_conflict", field="name", status="open").exists()
    front = active(product, "front")
    assert [i.source for i in front] == ["contribution"]
    assert product.front_image_url == reverse("public-image", args=[front.get().id])
    assert Product.objects.filter(off_code=p["code"]).count() == 1


def test_remap_from_a_trimmed_payload_publishes_nothing():
    for p in dump_lines().values():
        import_off_product(p, source="off_dump", queue_images=False)
    revisions = ProductRevision.objects.count()
    call_command("remap_off")
    assert ProductRevision.objects.count() == revisions
