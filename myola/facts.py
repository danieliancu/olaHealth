"""A small, source-neutral view of a product used by the restriction rules.

It reads the same ``revision.data`` shape the catalogue stores (see ``off/mapping.py``), so the
V2 rules can run on real products without changes to this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from catalog.nutrition import to_decimal

PER_100 = ("per_100g", "per_100ml")


@dataclass(frozen=True)
class Value:
    nutrient: str
    value: Decimal
    unit: str
    basis: str
    preparation: str


@dataclass
class ProductFacts:
    id: str
    name: str
    data: dict[str, Any]
    labels: list[str] = field(default_factory=list)  # label tags (e.g. en:vegan), from the source
    analysis: list[str] = field(default_factory=list)  # ingredients analysis tags, from the source
    brand: str = ""
    quantity: str = ""
    image: str = ""
    url: str = ""

    @property
    def categories(self) -> list[str]:
        return list(self.data.get("categories") or [])

    @property
    def countries(self) -> list[str]:
        return list(self.data.get("countries") or [])

    def section(self, key: str) -> dict[str, Any]:
        section = self.data.get(key) or {}
        return {"status": section.get("status") or "unknown", "items": section.get("items") or []}

    @property
    def ingredients_text(self) -> str:
        return ((self.data.get("ingredients") or {}).get("text") or "").lower()

    def per_100(self) -> tuple[str | None, dict[str, Value]]:
        """Declared per-100 values as sold, on a single basis (g or ml). Never converted."""
        facts = (self.data.get("nutrition") or {}).get("facts") or []
        for basis in PER_100:
            values = {}
            for f in facts:
                if (
                    f.get("basis") == basis
                    and f.get("preparation") == "as_sold"
                    and f.get("status") == "declared"
                ):
                    number = to_decimal(f.get("value"))
                    if number is not None:
                        values[f["nutrient"]] = Value(
                            f["nutrient"], number, f.get("unit") or "g", basis, "as_sold"
                        )
            if values:
                return basis, values
        return None, {}
