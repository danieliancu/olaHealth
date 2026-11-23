"""Download the real Open Food Facts products used by tests/test_score_v2.py.

Run by hand (never by the test suite), from the repository root:

    python tests/fixtures/off/real/fetch_real.py

Each product is fetched once with the OFF product API v2, trimmed to the fields AskFoodo
reads (no images, no contributor names) and written to ``<code>.json`` next to this script.
Data: Open Food Facts contributors, Open Database License (ODbL) 1.0 — see README.md.
Standard library only, so it runs outside the Docker image.
"""

from __future__ import annotations

import json
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = "https://world.openfoodfacts.org/api/v2/product/{code}.json?fields={fields}"
USER_AGENT = "olaHealth-tests/1.0 (fixture download)"

# code -> why it is in the set (README.md lists the same).
PRODUCTS = {
    "3033490004743": "plain skyr: Nutri-Score A in 2021 and 2023",
    "5010029000016": "breakfast cereal: A, NOVA 3",
    "5000157024671": "baked beans: A, NOVA 4 (processing independent of Nutri-Score)",
    "20994945": "cottage cheese: B in 2023, A in 2021 (versions differ)",
    "8002470031739": "frying oil (fats rules): C in 2023, D in 2021; no ingredient list",
    "3175680011480": "bread: C in 2023, B in 2021; 4 additives",
    "5449000131805": "sugar-free cola (beverage rules): C in 2023, B in 2021; 6 additives",
    "7622201693916": "soft cheese (cheese rules): D, NOVA 4",
    "3329770063341": "drinkable yogurt with sugar: D, NOVA 4",
    "5449000054227": "cola (beverage rules): E, NOVA 4",
    "3017624010701": "hazelnut spread: E; NOVA group missing",
    "3068320114453": "sparkling mineral water: A under the 2023 beverage rules",
    "75041670": "beer: Nutri-Score not applicable (alcohol)",
    "10614594": "Emmentaler: Nutri-Score unknown, no ingredient list, NOVA missing",
    "0051500241639": "crunchy peanut butter (fats, oils, nuts and seeds rules): D",
    "8710532469535": "100% peanut butter (fats, oils, nuts and seeds rules): A",
}

FIELDS = (
    "code,product_name,brands,quantity,categories_tags,countries_tags,lang,"
    "ingredients_text,allergens_tags,traces_tags,additives_tags,nutriments,"
    "nutrition_data_per,no_nutrition_data,nutriscore_grade,nutriscore_score,"
    "nutriscore_version,nutriscore,nova_group,nova_groups_tags,nutrient_levels,"
    "ingredients_analysis_tags,misc_tags,last_modified_t"
)
CORE_NUTRIMENTS = (
    "energy-kj_100g",
    "energy-kcal_100g",
    "fat_100g",
    "saturated-fat_100g",
    "carbohydrates_100g",
    "sugars_100g",
    "fiber_100g",
    "proteins_100g",
    "salt_100g",
)


def trim(product: dict) -> dict:
    out = {k: v for k, v in product.items() if k != "nutriscore"}
    nutriments = product.get("nutriments") or {}
    out["nutriments"] = {k: nutriments[k] for k in CORE_NUTRIMENTS if k in nutriments}
    # Per-version results: grade, points and the calculation-category flags (``data.is_*``)
    # the Foodo Score needs; the component breakdown is not used.
    out["nutriscore"] = {}
    for version, d in sorted((product.get("nutriscore") or {}).items()):
        if not isinstance(d, dict):
            continue
        entry = {k: d.get(k) for k in ("grade", "score") if k in d}
        data = d.get("data") if isinstance(d.get("data"), dict) else {}
        entry["data"] = {k: v for k, v in sorted(data.items()) if k.startswith("is_")}
        out["nutriscore"][version] = entry
    return out


def main() -> None:
    fetched_at = datetime.now(UTC).date().isoformat()
    for code in PRODUCTS:
        url = BASE.format(code=code, fields=FIELDS)  # fixed https host
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            payload = json.load(response)
        if payload.get("status") != 1:
            raise SystemExit(f"{code}: not found on OFF")
        product = trim(payload["product"])
        product["_fixture"] = {
            "source": f"https://world.openfoodfacts.org/product/{code}",
            "fetched_at": fetched_at,
            "license": "ODbL 1.0, Open Food Facts contributors",
        }
        path = HERE / f"{code}.json"
        path.write_text(
            json.dumps(product, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(code, product.get("nutriscore_grade"), product.get("nutriscore_version"))
        time.sleep(2)  # well under the OFF product-read limit


if __name__ == "__main__":
    main()
