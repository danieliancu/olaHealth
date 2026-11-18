import gzip
import json

import pytest
from django.urls import reverse

from assessments.services.compare import ComparisonError, compare
from catalog.export import write_export
from catalog.models import Product
from catalog.services.importer import import_off_product
from catalog.services.search import SearchParams, search_products
from contributions.services import drafts

pytestmark = pytest.mark.django_db


def _nutriments(**values):
    return {f"{k}_100g": v for k, v in values.items()}


@pytest.fixture
def catalogue(off_payload):
    """Four products imported from (simulated) Open Food Facts payloads."""
    base = off_payload()
    yogurt = off_payload(
        product_name="Plain yogurt",
        categories_tags=["en:dairies", "en:fermented-foods", "en:yogurts"],
        countries_tags=["en:romania", "en:moldova"],
        nutriscore_grade="a",
        nutriscore_version="2023",
        nutriments={
            **_nutriments(
                **{
                    "energy-kcal": 61,
                    "fat": 3.2,
                    "saturated-fat": 2.1,
                    "carbohydrates": 4.7,
                    "sugars": 4.7,
                    "proteins": 3.5,
                    "salt": 0.1,
                }
            ),
            "fiber_modifier": "-",
        },
    )
    oats = off_payload(
        code="3000000000014",
        product_name="Oat biscuits",
        quantity="300 g",
        categories_tags=["en:snacks", "en:sweet-snacks", "en:biscuits-and-cakes", "en:biscuits"],
        countries_tags=["en:romania"],
        nutriscore_grade="d",
        nutriscore_version="2023",
        nutriscore={"2023": {"grade": "d"}},
        nutriments=_nutriments(
            **{
                "energy-kcal": 468,
                "fat": 19,
                "saturated-fat": 8.1,
                "carbohydrates": 64,
                "sugars": 21,
                "fiber": 5.2,
                "proteins": 7.4,
                "salt": 0.6,
            }
        ),
    )
    wholegrain = off_payload(
        code="4000000000020",
        product_name="Wholegrain biscuits",
        quantity="250 g",
        categories_tags=["en:snacks", "en:sweet-snacks", "en:biscuits-and-cakes", "en:biscuits"],
        countries_tags=["en:france"],
        nutriscore_grade="c",
        nutriscore_version="2021",
        nutriscore={"2021": {"grade": "c"}},
        nutriments=_nutriments(
            **{
                "energy-kcal": 441,
                "fat": 15,
                "sugars": 16,
                "carbohydrates": 66,
                "proteins": 8.9,
                "salt": 0.5,
            }
        ),
    )
    juice = off_payload(
        code="5900000000008",
        product_name="Orange juice",
        quantity="1 l",
        product_quantity_unit="ml",
        categories_tags=["en:beverages", "en:fruit-juices", "en:orange-juices"],
        countries_tags=["en:france"],
        nutriscore_grade="unknown",
        nutriscore={},
        nutriments=_nutriments(
            **{
                "energy-kcal": 45,
                "fat": 0,
                "carbohydrates": 10.4,
                "sugars": 10.4,
                "proteins": 0.7,
                "salt": 0,
            }
        ),
    )
    assert base["code"] == yogurt["code"]
    products = [import_off_product(x) for x in (yogurt, oats, wholegrain, juice)]
    return {
        p.name: Product.objects.select_related("current_revision").get(pk=p.pk) for p in products
    }


def test_compare_requires_2_to_3(catalogue):
    products = list(catalogue.values())
    with pytest.raises(ComparisonError):
        compare(products[:1])
    with pytest.raises(ComparisonError):
        compare(products + products[:1])


def test_incompatible_bases_are_grouped_separately(catalogue):
    c = compare([catalogue["Plain yogurt"], catalogue["Orange juice"]])
    assert len(c.groups) == 1  # one table, the basis is shown per column
    g = c.groups[0]
    assert g.column_bases == ["per 100 g", "per 100 ml"] and g.mixed_bases
    assert c.differences == []  # nothing compared across g and ml
    assert any("no category in common" in w for w in c.warnings)


def test_zero_is_a_value_and_missing_is_last(catalogue):
    c = compare([catalogue["Oat biscuits"], catalogue["Plain yogurt"]])
    g = next(g for g in c.groups if g.basis == "per_100")
    fiber = next(r for r in g.rows if r.nutrient == "fiber")
    assert fiber.cells[0].display == "5.2 g"
    assert fiber.cells[1].state == "not_on_label"  # declared absent, distinct from unknown
    sugars = next(d for d in c.differences if d.startswith("Sugars"))
    assert sugars.index("Oat biscuits") < sugars.index("Plain yogurt")
    assert "winner" not in " ".join(c.differences).lower()


def test_nutriscore_versions_are_not_ranked_together(catalogue):
    c = compare([catalogue["Oat biscuits"], catalogue["Wholegrain biscuits"]])
    assert [n["version"] for n in c.nutriscore] == ["2023", "2021"]
    assert "different Nutri-Score algorithm versions" in c.nutriscore_note
    assert not any("different categories" in w for w in c.warnings)  # same leaf category


def test_search_and_filters(catalogue):
    def names(**kw):
        return [p.name for p in search_products(SearchParams(**kw))]

    assert set(names(q="biscuit")) == {"Oat biscuits", "Wholegrain biscuits"}
    assert names(q="yoghurt") == ["Plain yogurt"]  # trigram tolerance
    assert names(category="en:yogurts") == ["Plain yogurt"]
    assert names(nutriscore="a") == ["Plain yogurt"]
    assert names(sort="nutriscore")[-1] == "Orange juice"  # unknown last
    assert names(nutriscore="none") == ["Orange juice"]


def test_facets_show_only_what_matches(catalogue):
    from catalog.models import Category, Country
    from catalog.services.search import facets

    Category.objects.create(tag="en:biscuits", name="Biscuits")
    Category.objects.create(tag="en:unused-category", name="Never used")
    Country.objects.create(tag="en:france", name="France", code="FR")
    Country.objects.create(tag="en:japan", name="Japan", code="JP")

    f = facets(SearchParams())
    countries = {o.value: o.count for o in f["country"]}
    assert countries == {"en:romania": 2, "en:moldova": 1, "en:france": 2}  # no Japan
    assert "en:unused-category" not in {o.value for o in f["category"]}
    assert next(o for o in f["category"] if o.value == "en:biscuits").label == "Biscuits"
    assert {o.value for o in f["nutriscore"]} == {"a", "c", "d", "none"}  # no B / E

    # Each facet narrows with the other filters, but keeps its own choices.
    f = facets(SearchParams(country="en:france"))
    assert {o.value for o in f["nutriscore"]} == {"c", "none"}
    assert {o.value for o in f["category"]} >= {"en:biscuits", "en:orange-juices"}
    assert "en:yogurts" not in {o.value for o in f["category"]}
    assert {o.value for o in f["country"]} == {"en:romania", "en:moldova", "en:france"}
    assert next(o for o in f["country"] if o.value == "en:france").selected


def test_products_page_hides_empty_filters(client, catalogue):
    html = client.get(reverse("products") + "?country=en:france").content.decode()
    assert 'value="en:france" selected' in html
    assert 'value="a"' not in html  # no Nutri-Score A product in France
    assert "Japan" not in html


def test_main_pages_render_with_bounded_queries(client, catalogue, django_assert_max_num_queries):
    real = catalogue["Plain yogurt"]
    ids = ",".join(str(p.pk) for p in list(catalogue.values())[:3])
    pages = [
        (reverse("home"), 8),
        (reverse("scan"), 8),
        (reverse("products"), 16),
        (reverse("products") + "?q=biscuit&sort=nutriscore&country=en:france", 16),
        (reverse("product", args=[real.pk]), 16),
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
    assert "https://world.openfoodfacts.org" in html  # attribution kept in the footer (ODbL)
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


def test_export_contains_only_published_public_data(off_payload, admin_user, tmp_path):
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
    assert [r["id"] for r in records] == [str(p.pk)]
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


def test_same_basis_products_are_compared_in_a_mixed_table(catalogue):
    c = compare([catalogue["Oat biscuits"], catalogue["Orange juice"], catalogue["Plain yogurt"]])
    assert len(c.groups) == 1 and c.groups[0].mixed_bases
    sugars = [d for d in c.differences if d.startswith("Sugars")]
    assert len(sugars) == 1 and "per 100 g" in sugars[0] and "Orange juice" not in sugars[0]


def test_chart_data_only_for_same_basis_and_colour_follows_column(catalogue):
    c = compare([catalogue["Orange juice"], catalogue["Oat biscuits"], catalogue["Plain yogurt"]])
    assert len(c.charts) == 1  # the juice (per 100 ml) is not charted against grams
    chart = c.charts[0]
    assert chart["products"] == ["Oat biscuits", "Plain yogurt"]
    assert chart["slots"] == [1, 2]  # colour slot = the product's column, not its rank
    sugars = next(p for p in chart["panels"] if p["label"] == "Sugars")
    assert sugars["values"] == [21.0, 4.7] and sugars["unit"] == "g"
