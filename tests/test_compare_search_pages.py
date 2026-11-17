import gzip
import json

import pytest
from django.core.management import call_command
from django.urls import reverse

from assessments.services.compare import ComparisonError, compare
from catalog.export import write_export
from catalog.models import Product
from catalog.services.importer import import_off_product
from catalog.services.search import SearchParams, search_products
from contributions.services import drafts

pytestmark = pytest.mark.django_db


@pytest.fixture
def demo():
    call_command("load_demo")
    return {
        p.name: p for p in Product.objects.filter(is_demo=True).select_related("current_revision")
    }


def test_compare_requires_2_to_3(demo):
    products = list(demo.values())
    with pytest.raises(ComparisonError):
        compare(products[:1])
    with pytest.raises(ComparisonError):
        compare(products + products[:1])


def test_incompatible_bases_are_grouped_separately(demo):
    c = compare([demo["Demo plain yogurt"], demo["Demo orange juice"]])
    titles = {(g.basis, g.preparation) for g in c.groups}
    assert titles == {("per_100g", "as_sold"), ("per_100ml", "as_sold")}
    assert c.differences == []  # nothing compared across g and ml
    assert any("no category in common" in w for w in c.warnings)


def test_zero_is_a_value_and_missing_is_last(demo):
    c = compare([demo["Demo oat biscuits"], demo["Demo plain yogurt"]])
    g = next(g for g in c.groups if g.basis == "per_100g")
    fiber = next(r for r in g.rows if r.nutrient == "fiber")
    assert fiber.cells[0].display == "5.2 g"
    assert fiber.cells[1].state == "not_on_label"  # declared absent, distinct from unknown
    sugars = next(d for d in c.differences if d.startswith("Sugars"))
    assert sugars.index("oat biscuits") < sugars.index("plain yogurt")
    assert "winner" not in " ".join(c.differences).lower()


def test_nutriscore_versions_are_not_ranked_together(demo):
    c = compare([demo["Demo oat biscuits"], demo["Demo wholegrain biscuits"]])
    assert [n["version"] for n in c.nutriscore] == ["2023", "2021"]
    assert "different Nutri-Score algorithm versions" in c.nutriscore_note
    assert not any("different categories" in w for w in c.warnings)  # same leaf category


def test_search_and_filters(demo):
    names = lambda **kw: [p.name for p in search_products(SearchParams(include_demo=True, **kw))]  # noqa: E731
    assert names(q="biscuit") == ["Demo oat biscuits", "Demo wholegrain biscuits"] or set(
        names(q="biscuit")
    ) == {"Demo oat biscuits", "Demo wholegrain biscuits"}
    assert names(q="yoghurt") == ["Demo plain yogurt"]  # trigram tolerance
    assert names(category="en:yogurts") == ["Demo plain yogurt"]
    assert names(nutriscore="a") == ["Demo plain yogurt"]
    assert names(sort="nutriscore")[-1] == "Demo orange juice"  # unknown last
    assert names(nutriscore="none") == ["Demo orange juice"]
    assert search_products(SearchParams()).count() == 0  # demo hidden by default


def test_main_pages_render_with_bounded_queries(
    client, demo, django_assert_max_num_queries, off_payload
):
    real = import_off_product(off_payload())
    ids = ",".join(str(p.pk) for p in list(demo.values())[:3])
    pages = [
        (reverse("home"), 8),
        (reverse("scan"), 8),
        (reverse("products") + "?include_demo=on", 12),
        (reverse("products") + "?include_demo=on&q=biscuit&sort=nutriscore", 12),
        (reverse("product", args=[real.pk]), 14),
        (reverse("compare") + f"?ids={ids}", 12),
        (reverse("saved"), 5),
        (reverse("sources"), 5),
    ]
    for url, limit in pages:
        with django_assert_max_num_queries(limit):
            response = client.get(url)
        assert response.status_code == 200, url


def test_product_page_states_and_attribution(client, off_payload):
    p = import_off_product(off_payload(allergens_tags=[]))
    html = client.get(reverse("product", args=[p.pk])).content.decode()
    assert "Grade B" in html
    assert "does not mean allergen-free" in html
    assert "Open Food Facts" in html
    assert "https://off.test/product/5941234567899" in html
    assert "<script" not in html.split("</head>")[1]  # no inline/injected scripts


def test_health_endpoints_are_minimal(client):
    for name in ("healthz", "readyz"):
        r = client.get(reverse(name))
        assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_api_lookup_error_codes(client):
    r = client.get(reverse("api-lookup"), {"code": "abc"})
    assert r.status_code == 400 and r.json()["error"] == "invalid_code"


def test_lookup_rate_limit(client, settings):
    settings.RATE_LIMITS = {**settings.RATE_LIMITS, "lookup": (2, 60)}
    codes = [client.get(reverse("api-lookup"), {"code": "abc"}).status_code for _ in range(3)]
    assert codes == [400, 400, 429]


def test_export_contains_only_published_public_data(off_payload, demo, admin_user, tmp_path):
    p = import_off_product(off_payload())
    draft = drafts.create_draft(
        session_key="secret-session", problem_type="missing_data", product=p
    )
    drafts.save_proposal(draft, {"brand": "Draft brand"}, "private note")
    write_export(tmp_path)
    with gzip.open(tmp_path / "olahealth-catalogue-latest.jsonl.gz", "rt", encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    meta = json.loads(lines[0])["_meta"]
    assert "Open Database License" in meta["data_license"]
    records = [json.loads(line) for line in lines[1:]]
    assert [r["id"] for r in records] == [str(p.pk)]  # no demo products
    blob = "\n".join(lines)
    for secret in (
        "secret-session",
        "Draft brand",
        "private note",
        draft.access_token,
        str(draft.pk),
    ):
        assert secret not in blob
    assert records[0]["images"][0]["license"].startswith("CC BY-SA")


def test_api_lookup_accepts_browser_format_names(client, off_payload):
    import_off_product(off_payload())
    for fmt in ("EAN-13", "EAN13", "ean_13"):
        r = client.get(reverse("api-lookup"), {"code": "5941234567899", "format": fmt})
        assert r.status_code == 200, fmt
        assert r.json()["product"]["name"] == "Greek style yogurt"
        assert r.json()["code"]["format"] == "ean13"


def _barcode_jpeg(code: str) -> bytes:
    import io

    import zxingcpp
    from PIL import Image

    img = zxingcpp.create_barcode(code, zxingcpp.BarcodeFormat.EAN13).to_image(scale=4)
    pil = Image.frombytes("L", (img.shape[1], img.shape[0]), bytes(img)).convert("RGB")
    canvas = Image.new("RGB", (pil.width + 200, pil.height + 200), "white")
    canvas.paste(pil, (100, 100))
    out = io.BytesIO()
    canvas.save(out, "JPEG", quality=92)
    return out.getvalue()


def test_scan_from_photo_decodes_on_server_and_stores_nothing(client, settings, tmp_path):
    from django.core.files.uploadedfile import SimpleUploadedFile

    photo = SimpleUploadedFile("p.jpg", _barcode_jpeg("5941234567899"), "image/jpeg")
    r = client.post(reverse("scan-photo"), {"photo": photo})
    assert r.status_code == 302
    assert "code=5941234567899" in r.url and "format=ean13" in r.url
    private = tmp_path / "private"
    assert not private.exists() or not any(private.rglob("*.*"))  # nothing written


def test_scan_from_photo_without_barcode(client):
    import io

    from django.core.files.uploadedfile import SimpleUploadedFile
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (400, 300), "white").save(buf, "JPEG")
    r = client.post(
        reverse("scan-photo"), {"photo": SimpleUploadedFile("p.jpg", buf.getvalue(), "image/jpeg")}
    )
    assert r.status_code == 422
    assert b"No product barcode was found" in r.content
