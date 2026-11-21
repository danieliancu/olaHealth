from decimal import Decimal

import httpx
import pytest
import respx
from django.urls import reverse

from catalog.countries import country_from_accept_language
from catalog.image_cache import cache_product_images
from catalog.models import ProductImage
from catalog.presenters import format_value, split_ingredients
from catalog.services.importer import import_off_product

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("0.0428", "0.04 g"),
        ("0.107", "0.11 g"),
        ("30.9", "30.9 g"),
        ("21", "21 g"),
        ("0", "0 g"),
        ("0.004", "< 0.01 g"),
        ("2.505", "2.51 g"),
    ],
)
def test_values_show_at_most_two_decimals(value, expected):
    assert format_value(Decimal(value), "g") == expected


def test_stored_values_are_not_rounded(off_payload):
    payload = off_payload()
    payload["nutriments"]["salt_100g"] = 0.0428
    p = import_off_product(payload)
    salt = p.current_revision.facts.get(nutrient="salt")
    assert salt.value == Decimal("0.0428")


def test_ingredient_list_split_percent_allergens_and_details():
    items = split_ingredients(
        "Sugar, palm oil, _hazelnuts_ 13%, skimmed _milk_ powder 8.7%, "
        "emulsifier (lecithins (_soy_)), vanillin."
    )
    assert [("".join(t for t, _a in i.parts)) for i in items] == [
        "Sugar",
        "palm oil",
        "hazelnuts",
        "skimmed milk powder",
        "emulsifier",
        "vanillin",
    ]
    assert items[2].percent == "13%" and items[2].parts == [("hazelnuts", True)]
    assert items[3].parts == [("skimmed ", False), ("milk", True), (" powder", False)]
    assert items[4].detail == "lecithins (soy)"
    assert split_ingredients("") == []


@pytest.mark.parametrize(
    ("header", "country"),
    [
        ("ro-RO,ro;q=0.9,en;q=0.8", "en:romania"),
        ("ro", "en:romania"),
        ("en-GB,en;q=0.9", "en:united-kingdom"),
        ("en", ""),
        ("", ""),
        ("*", ""),
    ],
)
def test_country_is_detected_from_browser_language(header, country):
    assert country_from_accept_language(header) == country


def test_product_page_hides_unavailable_data(client, off_payload):
    payload = off_payload()
    for key in ("nova_group", "nova_groups_tags", "nutrient_levels"):
        payload.pop(key)
    payload["nutriments"].pop("salt_100g")
    p = import_off_product(payload)
    html = client.get(reverse("product", args=[p.pk])).content.decode()
    # Only what is shown: the page data for the scripts (in <head>) carries explicit
    # "unknown" states on purpose (docs/mvp-api-contracts.md).
    html = html.split("</head>")[1]
    assert "Processing" not in html and "NOVA" not in html and "Nutrient levels" not in html
    assert "Grade B" in html
    assert ">Salt<" not in html and "unknown" not in html
    assert "Missing data" not in html and "Data: Available" not in html


@respx.mock
def test_off_photos_are_cached_and_served_locally(client, off_payload, settings):
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (900, 900), (200, 100, 50)).save(buf, "JPEG")
    url = off_payload()["image_front_url"]
    route = respx.get(url).mock(
        return_value=httpx.Response(
            200, content=buf.getvalue(), headers={"content-type": "image/jpeg"}
        )
    )
    p = import_off_product(off_payload())
    assert p.front_image_url.startswith("/images/off/")
    assert cache_product_images(p.pk) == 1
    assert cache_product_images(p.pk) == 1  # already cached, no new download
    assert route.call_count == 1
    r = client.get(p.front_image_url)
    assert r.status_code == 200 and r["Content-Type"] == "image/webp"
    with Image.open(io.BytesIO(b"".join(r.streaming_content))) as served:
        assert served.format == "WEBP" and served.width == 480  # never wider than shown


def test_disallowed_host_is_never_fetched(off_payload):
    p = import_off_product(off_payload())
    img = ProductImage.objects.create(
        product=p,
        kind="other",
        source="off",
        license="x",
        attribution="x",
        remote_url="https://evil.example.com/a.jpg",
    )
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get("https://evil.example.com/a.jpg")
        from catalog.image_cache import ensure_cached

        assert ensure_cached(img) is None
        assert route.call_count == 0


def test_decimal_comma_is_not_an_ingredient_separator():
    items = split_ingredients("Brown rice 99,7%, salt 0,3%.")
    assert [("".join(t for t, _a in i.parts), i.percent) for i in items] == [
        ("Brown rice", "99,7%"),
        ("salt", "0,3%"),
    ]
