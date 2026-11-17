"""Nutrient vocabulary and value helpers shared by import, contributions and comparison."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

BASES = ("per_100g", "per_100ml", "per_100_unspecified", "per_serving")
PREPARATIONS = ("as_sold", "prepared")

# Fact status: a value is declared (possibly 0), declared as not on the label,
# or simply unknown (no fact stored at all). Unknown is never 0.
DECLARED = "declared"
NOT_ON_LABEL = "not_on_label"


@dataclass(frozen=True)
class Nutrient:
    key: str
    label: str
    unit: str  # canonical unit
    allowed_units: tuple[str, ...]


NUTRIENTS: tuple[Nutrient, ...] = (
    Nutrient("energy-kj", "Energy (kJ)", "kJ", ("kJ",)),
    Nutrient("energy-kcal", "Energy (kcal)", "kcal", ("kcal",)),
    Nutrient("fat", "Fat", "g", ("g", "mg")),
    Nutrient("saturated-fat", "of which saturates", "g", ("g", "mg")),
    Nutrient("carbohydrates", "Carbohydrate", "g", ("g", "mg")),
    Nutrient("sugars", "of which sugars", "g", ("g", "mg")),
    Nutrient("fiber", "Fibre", "g", ("g", "mg")),
    Nutrient("proteins", "Protein", "g", ("g", "mg")),
    Nutrient("salt", "Salt", "g", ("g", "mg")),
    Nutrient("sodium", "Sodium", "g", ("g", "mg")),
)
NUTRIENT_BY_KEY = {n.key: n for n in NUTRIENTS}
CORE_NUTRIENTS = (
    "energy-kcal",
    "fat",
    "saturated-fat",
    "carbohydrates",
    "sugars",
    "proteins",
    "salt",
)

BASIS_LABELS = {
    "per_100g": "per 100 g",
    "per_100ml": "per 100 ml",
    "per_100_unspecified": "per 100 g or ml (not stated by the source)",
    "per_serving": "per serving",
}
PREPARATION_LABELS = {"as_sold": "as sold", "prepared": "as prepared"}


def to_decimal(value: object) -> Decimal | None:
    """Parse a numeric value. Returns ``None`` for anything that is not a finite number."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, float):
        value = repr(value)
    try:
        result = Decimal(str(value).strip().replace(",", "."))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def decimal_str(value: Decimal) -> str:
    """Canonical plain string (no exponent, no trailing zeros)."""
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def to_canonical_unit(value: Decimal, unit: str, nutrient: Nutrient) -> Decimal:
    """Convert mg to g for mass nutrients. No other conversions (no g<->ml, no kJ<->kcal)."""
    if unit == nutrient.unit:
        return value
    if unit == "mg" and nutrient.unit == "g":
        return value / Decimal(1000)
    raise ValueError(f"Unit {unit} is not valid for {nutrient.key}")
