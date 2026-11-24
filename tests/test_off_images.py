"""OFF photo URLs from the API fields or from the dump's ``images`` metadata (off/images.py),
and the trimmed payload kept with each revision (off/fields.py)."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest

from off.fields import STORED_FIELDS, trim_payload
from off.images import built_urls, image_urls, product_path, selected_revisions, trim_images

LINES = Path(__file__).parent / "fixtures" / "off" / "dump_lines.jsonl"
HOSTS = ["images.openfoodfacts.org", "static.openfoodfacts.org"]
URL = re.compile(
    r"^https://images\.openfoodfacts\.org/images/products/"
    r"\d{3}/\d{3}/\d{3}/\d+/(front|ingredients|nutrition)_[a-z]{2,3}\.\d+\.400\.jpg$"
)


def dump_lines() -> dict[str, dict]:
    """Real lines of the OFF JSONL dump, keyed by what each one illustrates."""
    out = {}
    for line in LINES.read_text(encoding="utf-8").splitlines():
        data = json.loads(line)
        out[data.pop("_fixture")] = data
    return out


@pytest.mark.parametrize(
    "code, path",
    [
        ("0000101209159", "000/010/120/9159"),
        ("3017620422003", "301/762/042/2003"),
        ("00000758", "000/000/000/0758"),  # EAN-8, padded to 13 as OFF stores it
        ("012345678905", "001/234/567/8905"),  # UPC-12
        ("12345678901234", "123/456/789/01234"),
        ("abc", "abc"),
    ],
)
def test_product_path(code, path):
    assert product_path(code) == path


def test_current_metadata_shape():
    p = dump_lines()["new_format"]
    assert "selected" in p["images"]
    urls = built_urls(p)
    assert "front" in urls and all(URL.match(u) for u in urls.values())
    lc, rev = selected_revisions(p)["front"]
    assert urls["front"].endswith(f"/front_{lc}.{rev}.400.jpg")


def test_older_metadata_shape():
    p = dump_lines()["old_format"]
    assert "selected" not in p["images"]
    assert any(k.startswith("front_") for k in p["images"])
    urls = built_urls(p)
    assert "front" in urls and all(URL.match(u) for u in urls.values())


def test_short_code_photos():
    p = dump_lines()["ean8"]
    assert len(p["code"]) == 8
    padded = p["code"].zfill(13)
    path = f"{padded[:3]}/{padded[3:6]}/{padded[6:9]}/{padded[9:]}"
    assert all(f"/products/{path}/" in u for u in built_urls(p).values())


def test_no_photo_information_is_none_not_empty():
    p = dump_lines()["no_images"]
    assert "images" not in p
    assert image_urls(p, HOSTS) is None
    assert image_urls({**p, "images": {}}, HOSTS) == {}


def test_direct_urls_win_and_hosts_are_checked():
    p = dump_lines()["new_format"]
    direct = "https://images.openfoodfacts.org/images/products/000/011/230/2660/front_en.99.400.jpg"
    urls = image_urls({**p, "image_front_url": direct}, HOSTS)
    assert urls["front"] == direct
    evil = image_urls({**p, "image_front_url": "https://evil.example/x.jpg"}, HOSTS)
    assert evil["front"] == built_urls(p)["front"]  # direct URL refused, metadata kept
    assert image_urls(p, ["static.openfoodfacts.org"]) == {}


def test_language_order():
    images = {
        "selected": {
            "front": {lc: {"imgid": "1", "rev": r} for lc, r in (("fr", 3), ("en", 4), ("de", 5))}
        }
    }
    p = {"code": "3017620422003", "images": images}
    assert selected_revisions({**p, "lc": "de"})["front"] == ("de", 5)
    assert selected_revisions({**p, "lc": "ro", "lang": "fr"})["front"] == ("fr", 3)
    assert selected_revisions({**p, "lc": "ro"})["front"] == ("en", 4)
    only = {"selected": {"front": {"it": {"rev": "2"}, "es": {"rev": "7"}}}}
    assert selected_revisions({"code": "1", "images": only})["front"] == ("es", 7)


def test_unusable_revisions_are_ignored():
    images = {"selected": {"front": {"en": {"rev": None}, "fr": {"rev": "x"}}}, "front_de": {}}
    assert built_urls({"code": "3017620422003", "images": images}) == {}


def test_trimmed_images_give_the_same_urls():
    for name, p in dump_lines().items():
        trimmed = {**p, "images": trim_images(p.get("images"))} if "images" in p else p
        assert built_urls(trimmed) == built_urls(p), name


def test_trimmed_payload_keeps_only_stored_fields():
    for name, p in dump_lines().items():
        original = copy.deepcopy(p)
        trimmed = trim_payload(p)
        assert p == original, name  # input untouched
        assert set(trimmed) <= set(STORED_FIELDS), name
        assert "nutriscore_data" not in trimmed and "ingredients" not in trimmed
        assert len(json.dumps(trimmed)) < len(json.dumps(p)) / 2, name


# --- Nutrition in the current OFF schema (``nutrition.input_sets``, empty ``nutriments``) ---


def _set(source, per="100g", preparation="as_sold", **nutrients):
    return {"source": source, "per": per, "preparation": preparation, "nutrients": nutrients}


def _facts(payload):
    from off.mapping import map_product

    return {
        (f["nutrient"], f["basis"], f["preparation"]): (f["status"], f["value"], f["modifier"])
        for f in map_product(payload)["nutrition"]["facts"]
    }


def test_dump_lines_in_the_current_schema_keep_their_nutrition():
    from off.mapping import map_product

    current = [p for p in dump_lines().values() if not p.get("nutriments") and p.get("nutrition")]
    assert current, "the fixture must include current-schema lines"
    for p in current:
        declared = [s for s in p["nutrition"].get("input_sets", []) if s["source"] == "packaging"]
        if declared:
            assert map_product(p)["nutrition"]["status"] == "available", p["code"]


def test_input_sets_declared_values_units_and_bases():
    payload = {
        "code": "3017620422003",
        "quantity": "400 g",
        "nutriments": {},
        "nutrition": {
            "input_sets": [
                _set("estimate", fiber={"value": 9, "unit": "g", "modifier": "~"}),
                _set(
                    "packaging",
                    fat={"value": 1.5, "unit": "g"},
                    salt={"value": 740, "unit": "mg"},
                    sugars={"value": 0.5, "unit": "g", "modifier": "<"},
                    **{
                        "energy-kj": {"value": 161, "unit": "kJ"},
                        "energy-kcal": {"value_computed": 38.3, "unit": "kcal"},
                        "vitamin-c": {"value": 3, "unit": "mg"},
                    },
                ),
                _set("manufacturer", fat={"value": 9, "unit": "g"}),
                _set("packaging", per="serving", proteins={"value": 2, "unit": "g"}),
                _set("packaging", preparation="prepared", fat={"value": 3, "unit": "g"}),
            ]
        },
    }
    facts = _facts(payload)
    assert facts[("fat", "per_100g", "as_sold")] == ("declared", "1.5", None)  # packaging wins
    assert facts[("salt", "per_100g", "as_sold")] == ("declared", "0.74", None)  # mg -> g
    assert facts[("sugars", "per_100g", "as_sold")] == ("declared", "0.5", "<")
    assert facts[("energy-kj", "per_100g", "as_sold")] == ("declared", "161", None)
    assert ("energy-kcal", "per_100g", "as_sold") not in facts  # computed is not declared
    assert ("fiber", "per_100g", "as_sold") not in facts  # estimates are never declared
    assert facts[("proteins", "per_serving", "as_sold")] == ("declared", "2", None)
    assert facts[("fat", "per_100g", "prepared")] == ("declared", "3", None)


def test_nutriments_keep_priority_over_input_sets():
    payload = {
        "code": "3017620422003",
        "quantity": "400 g",
        "nutriments": {"fat_100g": 7},
        "nutrition": {"input_sets": [_set("packaging", fat={"value": 1, "unit": "g"})]},
    }
    assert _facts(payload) == {("fat", "per_100g", "as_sold"): ("declared", "7", None)}


def test_unspecified_and_no_nutrition_on_packaging():
    from off.mapping import map_product

    s = _set("packaging", fat={"value": 1, "unit": "g"})
    s["unspecified_nutrients"] = ["fiber"]
    payload = {"code": "1", "quantity": "1 l", "nutrition": {"input_sets": [s]}}
    facts = _facts(payload)
    assert facts[("fiber", "per_100ml", "as_sold")] == ("not_on_label", None, None)
    none = {"code": "1", "nutrition": {"no_nutrition_data_on_packaging": True, "input_sets": []}}
    assert map_product(none)["nutrition"] == {"status": "not_on_package", "facts": []}


def test_trimmed_nutrition_maps_the_same():
    from off.mapping import map_product

    for name, p in dump_lines().items():
        assert map_product(trim_payload(p)) == map_product(p), name
