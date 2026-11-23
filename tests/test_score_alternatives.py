import httpx
import pytest
import respx
from django.core.cache import cache
from django.urls import reverse

from assessments.score import VERSION_V1, foodo_score_v1, score_for_product
from catalog.models import Category
from catalog.services.alternatives import alternatives, reference_categories
from catalog.services.importer import import_off_product
from off.client import OFFClient, OFFError

BASE = "https://off.test"
UK = "en:united-kingdom"
CATS = ["en:dairies", "en:fermented-foods", "en:yogurts"]


# --- Foodo Score score.v1 (retired; kept for reference, see test_score_v2.py) -------------


def test_not_enough_data_without_nutriscore_and_nova():
    assert foodo_score_v1({})["status"] == "not_enough_data"
    assert foodo_score_v1({"nutriscore_grade": "unknown", "nova_group": None})["status"] == (
        "not_enough_data"
    )
    # Nutrient levels alone are not enough.
    assert foodo_score_v1({"nutrient_levels": {"sugars": "high"}})["status"] == "not_enough_data"


def test_points_are_reconstructible():
    result = foodo_score_v1(
        {
            "nutriscore_grade": "D",
            "nova_group": "4",
            "nutrient_levels": {"sugars": "high", "salt": "high", "fat": "high"},
            "additives_tags": ["en:e250"],
        }
    )
    assert result["status"] == "ok" and result["version"] == VERSION_V1 == "score.v1"
    assert result["score"] == 100 - 40 - 20 - 5 - 5 == 30
    assert result["score"] == 100 + sum(p["points"] for p in result["points"])
    assert [p["reason"] for p in result["points"]] == [
        "Nutri-Score D",
        "NOVA group 4",
        "High sugars",
        "High salt",
    ]
    assert result["missing"] == []


def test_partial_data_lists_missing_and_clamps():
    result = foodo_score_v1({"nova_group": 2})
    assert result["score"] == 100
    assert result["missing"] == ["nutriscore", "nutrient_levels", "additives"]
    worst = foodo_score_v1(
        {
            "nutriscore_grade": "e",
            "nova_group": 4,
            "nutrient_levels": {"sugars": "high", "salt": "high", "saturated-fat": "high"},
        }
    )
    assert worst["score"] == 100 - 55 - 20 - 15
    assert 0 <= worst["score"] <= 100


def test_score_for_product_uses_current_snapshots(db, off_payload):
    product = import_off_product(off_payload())  # Nutri-Score B (2023, 1 point), NOVA 1
    result = score_for_product(product)
    assert result["version"] == "score.v2" and result["status"] == "ok"
    assert (result["nutriscore"]["grade"], result["nutriscore"]["points"]) == ("b", 1)
    assert result["separate"]["nova"]["group"] == 1 and result["missing"] == []


# --- Alternatives ------------------------------------------------------------------------


def make(off_payload, code, grade, nova, sugars, **changes):
    payload = off_payload(
        code=code,
        product_name=changes.pop("name", f"Yogurt {code[-3:]}"),
        nutriscore_grade=grade,
        nova_group=nova,
        nutriscore={},
        countries_tags=changes.pop("countries_tags", [UK]),
        nutriments={"sugars_100g": sugars, "salt_100g": 0.1, "saturated-fat_100g": 2},
        nutrient_levels={"sugars": "high" if sugars > 12.5 else "low", "salt": "low"},
        **changes,
    )
    return import_off_product(payload)


def off_item(code, grade, nova, sugars, **changes):
    return {
        "code": code,
        "product_name": changes.pop("name", f"OFF yogurt {code[-3:]}"),
        "brands": "Other Dairy",
        "quantity": "500 g",
        "product_quantity_unit": "g",
        "categories_tags": CATS,
        "countries_tags": [UK],
        "ingredients_text": "Milk, cultures.",
        "allergens_tags": ["en:milk"],
        "traces_tags": [],
        "nutriments": {"sugars_100g": sugars, "salt_100g": 0.1},
        "nutriscore_grade": grade,
        "nutriscore_version": "2023",
        "nova_group": nova,
        "nutrient_levels": {"sugars": "high" if sugars > 12.5 else "low"},
        "image_front_url": "https://images.openfoodfacts.org/x/front.jpg",
        **changes,
    }


@pytest.fixture
def off_client():
    return OFFClient(base_url=BASE, user_agent="olaHealth-tests/1.0 (test)", sleep=lambda s: None)


@pytest.fixture
def reference(db, off_payload):
    cache.clear()
    # Nutri-Score D (2023), NOVA 4, high sugars.
    return make(off_payload, "5000000000001", "d", 4, 14, name="Sweet yogurt")


def search_route(mock, products):
    return mock.route(method="GET", host="off.test", path="/api/v2/search").mock(
        return_value=httpx.Response(200, json={"count": len(products), "products": products})
    )


def test_ranking_advantages_and_trade_offs(reference, off_payload, off_client):
    better_local = make(off_payload, "5000000000002", "b", 1, 4, name="Plain yogurt")
    make(off_payload, "5000000000003", "e", 4, 20, name="Worse yogurt")
    make(off_payload, "5000000000004", "a", 1, 2, countries_tags=["en:france"])  # not UK
    with respx.mock(assert_all_called=False) as mock:
        route = search_route(
            mock,
            [
                off_item("5000000000101", "a", 1, 3, name="Best yogurt"),
                off_item("5000000000102", "e", 4, 20, name="Not better"),
                off_item("5000000000103", None, None, 1, name="No data"),
                off_item("5000000000001", "a", 1, 1),  # the reference itself
                off_item("5000000000002", "a", 1, 1),  # already known locally
            ],
        )
        result = alternatives(reference, client=off_client)
    params = route.calls[0].request.url.params
    assert params["categories_tags"] == "en:yogurts" and params["countries_tags"] == UK
    assert "nutriscore_grade" in params["fields"]
    assert [r["name"] for r in result] == ["Best yogurt", "Plain yogurt"]
    best, local = result
    assert best["source"] == "off" and best["off_code"] == "5000000000101"
    assert best["url"] == "/lookup/?code=5000000000101&via=alternative"
    assert best["grade"] == "a" and best["reference_grade"] == "d"
    assert best["nutriscore_version"] == "2023"
    assert best["advantage"] == "Nutri-Score A vs D"
    assert "NOVA group 1 vs 4" in best["advantages"]
    assert "less sugar per 100 g: 3 g vs 14 g" in best["advantages"]
    assert local["source"] == "local" and local["product_id"] == str(better_local.pk)
    assert local["grade"] == "b"
    # Saturates: 2 g in both the reference and the local product, so not mentioned.
    assert not any("saturated" in t for t in local["advantages"] + local["trade_offs"])


def test_only_a_better_grade_in_the_same_version_counts(reference, off_client):
    with respx.mock(assert_all_called=False) as mock:
        search_route(
            mock,
            [
                off_item("5000000000101", "a", 1, 3, name="Unversioned", nutriscore_version=""),
                off_item("5000000000102", "a", 1, 3, name="Old version", nutriscore_version="2021"),
                off_item("5000000000103", "d", 1, 1, name="Same grade, less sugar"),
                off_item("5000000000104", "c", 4, 20, name="Better grade, NOVA 4"),
            ],
        )
        result = alternatives(reference, client=off_client, limit=10)
    # NOVA and sugar do not rank; only a strictly better grade of the same version does.
    assert [r["name"] for r in result] == ["Better grade, NOVA 4"]


def test_trade_offs_where_the_alternative_is_worse(reference, off_client):
    with respx.mock(assert_all_called=False) as mock:
        search_route(mock, [off_item("5000000000101", "b", 1, 20, name="Sugary but better")])
        (item,) = alternatives(reference, client=off_client)
    assert "more sugar per 100 g: 20 g vs 14 g" in item["trade_offs"]


def test_off_search_is_cached(reference, off_client):
    with respx.mock(assert_all_called=False) as mock:
        route = search_route(mock, [off_item("5000000000101", "a", 1, 3)])
        first = alternatives(reference, client=off_client)
        # One search: "yogurts" (its other tags, dairies and fermented foods, are too broad).
        assert route.call_count == 1
        second = alternatives(reference, client=off_client)
    assert first == second and len(first) == 1
    assert route.call_count == 1  # served from the cache


def test_off_error_gives_local_results_only(reference, off_payload, off_client):
    make(off_payload, "5000000000002", "b", 1, 4, name="Plain yogurt")
    with respx.mock(assert_all_called=False) as mock:
        mock.route(host="off.test", path="/api/v2/search").mock(return_value=httpx.Response(503))
        result = alternatives(reference, client=off_client)
    assert [r["name"] for r in result] == ["Plain yogurt"]


def test_restrictions_and_avoid_list(reference, off_payload, off_client, member):
    from myola.models import ProductMark
    from myola.services.profiles import get_profile

    profile = get_profile(member)
    profile.restrictions = ["nuts"]
    profile.save()
    avoided = make(off_payload, "5000000000002", "b", 1, 4, name="Avoided yogurt")
    ProductMark.objects.create(user=member, product=avoided, kind=ProductMark.Kind.AVOID)
    make(off_payload, "5000000000003", "a", 1, 3, name="Nut traces", traces_tags=["en:nuts"])
    off_products = [
        off_item("5000000000101", "a", 1, 3, name="OFF ok"),
        off_item("5000000000102", "a", 1, 3, name="OFF no allergen info", allergens_tags=[]),
        off_item("5000000000103", "a", 1, 3, name="OFF peanuts", traces_tags=["en:peanuts"]),
    ]
    with respx.mock(assert_all_called=False) as mock:
        route = search_route(mock, off_products)
        anonymous = alternatives(reference, client=off_client, limit=10)
        personal = alternatives(reference, user=member, client=off_client, limit=10)
    assert {r["name"] for r in anonymous} == {
        "Avoided yogurt",
        "Nut traces",
        "OFF ok",
        "OFF no allergen info",
        "OFF peanuts",
    }
    assert [r["name"] for r in personal] == ["OFF ok"]
    # Preferences are never sent to OFF.
    for call in route.calls:
        params = dict(call.request.url.params)
        assert set(params) == {"categories_tags", "countries_tags", "page_size", "fields"}
        assert "peanuts" not in str(call.request.url)


def test_parent_category_fallback(reference, off_client):
    Category.objects.create(tag="en:yogurts", name="Yogurts", parents=["en:dairy-desserts"])
    assert reference_categories(reference) == ["en:yogurts", "en:dairy-desserts"]
    with respx.mock(assert_all_called=False) as mock:
        route = mock.route(host="off.test", path="/api/v2/search").mock(
            side_effect=lambda request: httpx.Response(
                200,
                json={
                    "products": (
                        [
                            off_item(
                                "5000000000101",
                                "a",
                                1,
                                3,
                                name="Kefir",
                                categories_tags=[*CATS, "en:dairy-desserts"],
                            )
                        ]
                        if request.url.params["categories_tags"] == "en:dairy-desserts"
                        else []
                    )
                },
            )
        )
        result = alternatives(reference, client=off_client)
    assert [r["name"] for r in result] == ["Kefir"]
    assert [c.request.url.params["categories_tags"] for c in route.calls] == [
        "en:yogurts",
        "en:dairy-desserts",
    ]


def test_reference_without_enough_data(db, off_payload, off_client):
    cache.clear()
    product = make(off_payload, "5000000000001", None, None, 14)
    assert score_for_product(product)["status"] == "not_enough_data"
    with respx.mock(assert_all_called=False) as mock:
        route = search_route(mock, [off_item("5000000000101", "a", 1, 3)])
        assert alternatives(product, client=off_client) == []
    assert route.call_count == 0


# --- OFF search client -------------------------------------------------------------------


@respx.mock
def test_search_invalid_response():
    respx.get(f"{BASE}/api/v2/search").mock(return_value=httpx.Response(200, json={"x": 1}))
    c = OFFClient(base_url=BASE, user_agent="t", sleep=lambda s: None)
    with pytest.raises(OFFError) as exc:
        c.search("en:yogurts")
    assert exc.value.kind == "invalid_response"


def test_search_uses_its_own_gate():
    def denied():
        raise OFFError("rate_limited", "local", retry_after=30)

    c = OFFClient(base_url=BASE, user_agent="t", search_gate=denied, sleep=lambda s: None)
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(f"{BASE}/api/v2/search")
        with pytest.raises(OFFError) as exc:
            c.search("en:yogurts")
    assert exc.value.kind == "rate_limited" and route.call_count == 0


# --- Alternatives must be the same kind of food ------------------------------------------
PEANUT = ["en:plant-based-foods-and-beverages", "en:plant-based-foods", "en:spreads",
          "en:plant-based-spreads", "en:legume-butters", "en:peanut-butters"]  # fmt: skip


def test_unrelated_off_results_are_never_shown(db, off_payload, off_client):
    cache.clear()
    butter = make(off_payload, "5000000000301", "d", 4, 14, name="Crunchy peanut butter",
                  categories_tags=PEANUT)  # fmt: skip
    assert reference_categories(butter)[0] == "en:peanut-butters"
    garlic = off_item("5000000000302", "a", 1, 1, name="Chopped garlic",
                      categories_tags=["en:plant-based-foods", "en:garlic"])  # fmt: skip
    passata = off_item("5000000000303", "a", 1, 3, name="Passata",
                       categories_tags=["en:plant-based-foods", "en:tomato-sauces"])  # fmt: skip
    good = off_item("5000000000304", "b", 3, 5, name="Smooth peanut butter", categories_tags=PEANUT)
    unnamed = off_item("5000000000305", "a", 1, 3, name="", categories_tags=PEANUT)
    with respx.mock(assert_all_called=False) as mock:
        route = search_route(mock, [garlic, passata, good, unnamed])
        result = alternatives(butter, client=off_client, limit=10)
    assert [r["name"] for r in result] == ["Smooth peanut butter"]
    sent = {c.request.url.params["categories_tags"] for c in route.calls}
    assert not sent & {"en:plant-based-foods", "en:spreads", "en:plant-based-foods-and-beverages"}


def test_broad_categories_only_give_no_alternatives(db, off_payload, off_client, client):
    cache.clear()
    vague = make(off_payload, "5000000000311", "d", 4, 14, name="Mystery spread",
                 categories_tags=["en:plant-based-foods", "en:spreads"])  # fmt: skip
    assert reference_categories(vague) == []
    with respx.mock(assert_all_called=False) as mock:
        route = search_route(mock, [off_item("5000000000312", "a", 1, 1, name="Garlic")])
        assert alternatives(vague, client=off_client) == []
        html = client.get(reverse("product-alternatives", args=[vague.pk])).content.decode()
    assert not route.calls
    assert "We haven&#x27;t found a suitable alternative yet." in html or (
        "We haven't found a suitable alternative yet." in html
    )
