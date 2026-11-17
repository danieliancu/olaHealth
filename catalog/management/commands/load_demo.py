"""Fictional demo products, clearly separated from the real catalogue.

* ``is_demo=True``: hidden from search unless "Show demo products" is ticked, never
  returned by barcode lookup, excluded from the ODbL export, badged "Demo" in the UI.
* Identifiers live in the ``demo`` context, so scanning a real code never hits them.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand
from django.db import transaction

from assessments.rules import IndicatorSpec
from catalog.models import Product, ProductIdentifier
from catalog.services.revisions import publish_revision
from off.mapping import sort_facts


def fact(
    n: str, v: str | None, basis: str = "per_100g", status: str = "declared"
) -> dict[str, Any]:
    unit = {"energy-kj": "kJ", "energy-kcal": "kcal"}.get(n, "g")
    return {
        "nutrient": n,
        "basis": basis,
        "preparation": "as_sold",
        "status": status,
        "value": v,
        "unit": unit,
        "modifier": None,
    }


def data(name, brand, quantity, categories, ingredients, facts, allergens=(), traces=()):
    return {
        "name": name,
        "generic_name": None,
        "brand": brand,
        "quantity": quantity,
        "serving_size": None,
        "categories": categories,
        "countries": ["en:romania"],
        "ingredients": {"text": ingredients, "lang": "en"},
        "allergens": {"status": "listed" if allergens else "unknown", "items": list(allergens)},
        "traces": {"status": "listed" if traces else "unknown", "items": list(traces)},
        "nutrition": {"status": "available" if facts else "unknown", "facts": sort_facts(facts)},
        "packaging_nutriscore": None,
    }


ARTWORK = {
    "2000000000015": "static:img/demo/oat-biscuits",
    "2000000000022": "static:img/demo/wholegrain-biscuits",
    "2000000000039": "static:img/demo/plain-yogurt",
    "2000000000046": "static:img/demo/orange-juice",
}

DEMO = [
    (
        "2000000000015",
        data(
            "Demo oat biscuits",
            "Demo Bakery",
            "300 g",
            ["en:snacks", "en:sweet-snacks", "en:biscuits-and-cakes", "en:biscuits"],
            "Oat flakes 45%, wheat flour, sugar, palm fat, butter (milk), raising agent: E500.",
            [
                fact("energy-kj", "1960"),
                fact("energy-kcal", "468"),
                fact("fat", "19"),
                fact("saturated-fat", "8.1"),
                fact("carbohydrates", "64"),
                fact("sugars", "21"),
                fact("fiber", "5.2"),
                fact("proteins", "7.4"),
                fact("salt", "0.6"),
            ],
            allergens=["en:gluten", "en:milk"],
            traces=["en:nuts"],
        ),
        [
            IndicatorSpec("nutriscore", "available", {"grade": "d", "other_versions": {}}, "2023"),
            IndicatorSpec("nova", "available", {"group": 4}),
            IndicatorSpec(
                "nutrient_levels",
                "available",
                {
                    "levels": {
                        "fat": "high",
                        "salt": "moderate",
                        "saturated-fat": "high",
                        "sugars": "high",
                    }
                },
            ),
            IndicatorSpec(
                "ingredients_analysis",
                "available",
                {"tags": ["en:non-vegan", "en:palm-oil", "en:vegetarian"]},
            ),
            IndicatorSpec("additives", "available", {"tags": ["en:e500"]}),
        ],
    ),
    (
        "2000000000022",
        data(
            "Demo wholegrain biscuits",
            "Demo Bakery",
            "250 g",
            ["en:snacks", "en:sweet-snacks", "en:biscuits-and-cakes", "en:biscuits"],
            "Wholegrain wheat flour 60%, sugar, sunflower oil, salt.",
            [
                fact("energy-kj", "1850"),
                fact("energy-kcal", "441"),
                fact("fat", "15"),
                fact("saturated-fat", "1.6"),
                fact("carbohydrates", "66"),
                fact("sugars", "16"),
                fact("fiber", "8.0"),
                fact("proteins", "8.9"),
                fact("salt", "0.5"),
            ],
            allergens=["en:gluten"],
        ),
        [
            IndicatorSpec("nutriscore", "available", {"grade": "c", "other_versions": {}}, "2021"),
            IndicatorSpec("nova", "available", {"group": 3}),
            IndicatorSpec(
                "nutrient_levels",
                "available",
                {
                    "levels": {
                        "fat": "high",
                        "salt": "moderate",
                        "saturated-fat": "low",
                        "sugars": "high",
                    }
                },
            ),
            IndicatorSpec(
                "ingredients_analysis", "available", {"tags": ["en:palm-oil-free", "en:vegan"]}
            ),
            IndicatorSpec("additives", "available", {"tags": []}),
        ],
    ),
    (
        "2000000000039",
        data(
            "Demo plain yogurt",
            "Demo Dairy",
            "400 g",
            ["en:dairies", "en:fermented-foods", "en:yogurts"],
            "Pasteurised milk, live cultures.",
            [
                fact("energy-kcal", "61"),
                fact("fat", "3.2"),
                fact("saturated-fat", "2.1"),
                fact("carbohydrates", "4.7"),
                fact("sugars", "4.7"),
                fact("proteins", "3.5"),
                fact("salt", "0.1"),
                fact("fiber", None, status="not_on_label"),
            ],
            allergens=["en:milk"],
        ),
        [
            IndicatorSpec("nutriscore", "available", {"grade": "a", "other_versions": {}}, "2023"),
            IndicatorSpec("nova", "available", {"group": 1}),
            IndicatorSpec(
                "nutrient_levels",
                "available",
                {
                    "levels": {
                        "fat": "moderate",
                        "salt": "low",
                        "saturated-fat": "moderate",
                        "sugars": "low",
                    }
                },
            ),
            IndicatorSpec(
                "ingredients_analysis", "available", {"tags": ["en:non-vegan", "en:vegetarian"]}
            ),
            IndicatorSpec("additives", "available", {"tags": []}),
        ],
    ),
    (
        "2000000000046",
        data(
            "Demo orange juice",
            "Demo Orchard",
            "1 l",
            ["en:beverages", "en:fruit-juices", "en:orange-juices"],
            "Orange juice from concentrate.",
            [
                fact("energy-kcal", "45", "per_100ml"),
                fact("fat", "0", "per_100ml"),
                fact("carbohydrates", "10.4", "per_100ml"),
                fact("sugars", "10.4", "per_100ml"),
                fact("proteins", "0.7", "per_100ml"),
                fact("salt", "0", "per_100ml"),
            ],
        ),
        [
            IndicatorSpec(
                "nutriscore",
                "missing_data",
                {"grade": None, "other_versions": {}},
                "2023",
                ("Some nutrition facts are missing.",),
            ),
            IndicatorSpec("nova", "missing_data"),
            IndicatorSpec("nutrient_levels", "missing_data"),
            IndicatorSpec("ingredients_analysis", "available", {"tags": ["en:vegan"]}),
            IndicatorSpec("additives", "available", {"tags": []}),
        ],
    ),
]


class Command(BaseCommand):
    help = "Load (or with --clear remove) fictional demo products."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--clear", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options) -> None:
        if options["clear"]:
            n = Product.objects.filter(is_demo=True).delete()[0]
            self.stdout.write(f"Removed demo data ({n} rows).")
            return
        created = 0
        for code, content, specs in DEMO:
            ident = ProductIdentifier.objects.filter(
                normalized=code.zfill(14), context="demo"
            ).first()
            if ident:
                product = Product.objects.select_for_update().get(pk=ident.product_id)
            else:
                product = Product.objects.create(is_demo=True)
                ProductIdentifier.objects.create(
                    product=product,
                    raw_value=code,
                    detected_format="ean13",
                    normalized=code.zfill(14),
                    off_code=code,
                    kind="restricted",
                    context="demo",
                )
            # Illustrations shipped with the app (SVG, or your own .webp/.png if added).
            Product.objects.filter(pk=product.pk).update(front_image_url=ARTWORK[code])
            product.front_image_url = ARTWORK[code]
            sources = {k: {"source": "demo"} for k in content}
            if publish_revision(
                product,
                content,
                source="demo",
                field_sources=sources,
                off_specs=specs,
                off_data=content,
            ):
                created += 1
        self.stdout.write(self.style.SUCCESS(f"Demo products loaded ({created} new revisions)."))
