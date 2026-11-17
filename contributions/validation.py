"""Server-side checks on proposed nutrition facts.

Errors block submission; warnings are shown to the contributor and to the moderator.
Values are never rewritten or derived: kJ/kcal and salt/sodium are only cross-checked.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from catalog.nutrition import NUTRIENT_BY_KEY, to_decimal

ZERO = Decimal(0)


def rounding_tolerance(value: Decimal) -> Decimal:
    """Tolerance for label rounding (EU guidance rounds to 0.1 g below 10 g, 1 g above)."""
    return Decimal("0.5") if value >= 10 else Decimal("0.1")


@dataclass
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _values(facts: list[dict[str, Any]]) -> dict[str, Decimal]:
    out = {}
    for f in facts:
        if f.get("status") == "declared" and f.get("value") is not None:
            v = to_decimal(f["value"])
            if v is not None:
                out[f["nutrient"]] = v
    return out


def validate_facts(
    facts: list[dict[str, Any]], basis: str, serving_size: str = ""
) -> ValidationReport:
    report = ValidationReport()
    for f in facts:
        nutrient = NUTRIENT_BY_KEY.get(f.get("nutrient", ""))
        if nutrient is None:
            report.errors.append(f"Unknown nutrient {f.get('nutrient')!r}.")
            continue
        if f.get("status") != "declared":
            continue
        value = to_decimal(f.get("value"))
        if value is None:
            report.errors.append(f"{nutrient.label}: the value is not a number.")
            continue
        if value < 0:
            report.errors.append(f"{nutrient.label}: values cannot be negative.")
        if f.get("unit") != nutrient.unit:
            report.errors.append(
                f"{nutrient.label}: unit must be {nutrient.unit} (got {f.get('unit')!r})."
            )

    if basis == "per_serving" and not serving_size.strip():
        report.errors.append("Values per serving need the serving size as printed on the label.")

    v = _values(facts)

    def check_part(part: str, whole: str, part_label: str, whole_label: str) -> None:
        if part in v and whole in v and v[part] > v[whole] + rounding_tolerance(v[whole]):
            report.errors.append(
                f"{part_label} ({v[part]} g) cannot exceed {whole_label} ({v[whole]} g)."
            )

    check_part("sugars", "carbohydrates", "Sugars", "carbohydrate")
    check_part("saturated-fat", "fat", "Saturates", "fat")

    if basis == "per_100g":
        mass = sum(
            (v.get(k, ZERO) for k in ("fat", "carbohydrates", "proteins", "fiber", "salt")), ZERO
        )
        if mass > Decimal(102):
            report.errors.append(
                f"Fat, carbohydrate, protein, fibre and salt add up to {mass} g per 100 g."
            )
    if basis in ("per_100g", "per_100ml", "per_100_unspecified"):
        if v.get("energy-kcal", ZERO) > 900:
            report.errors.append("Energy above 900 kcal per 100 is not possible.")
        if v.get("energy-kj", ZERO) > 3800:
            report.errors.append("Energy above 3800 kJ per 100 is not possible.")
        if v.get("salt", ZERO) > 100 or v.get("sodium", ZERO) > 40:
            report.errors.append("Salt or sodium above 100 % of the weight is not possible.")
        if Decimal(10) < v.get("salt", ZERO) <= 100:
            report.warnings.append("Salt above 10 g per 100 is unusual; please double-check.")
        if v.get("proteins", ZERO) > 90:
            report.warnings.append("Protein above 90 g per 100 is unusual; please double-check.")

    if "energy-kj" in v and "energy-kcal" in v and v["energy-kcal"] > 0:
        ratio = v["energy-kj"] / v["energy-kcal"]
        if not Decimal("3.9") <= ratio <= Decimal("4.5"):
            report.warnings.append(
                "Energy in kJ and kcal do not match (1 kcal ≈ 4.184 kJ). Both values are kept "
                "as entered."
            )
    if "salt" in v and "sodium" in v and v["sodium"] > 0:
        ratio = v["salt"] / v["sodium"]
        if not Decimal("2.2") <= ratio <= Decimal("2.8"):
            report.warnings.append(
                "Salt and sodium do not match (salt ≈ sodium × 2.5). "
                "Both values are kept as entered."
            )
    if "energy-kcal" in v and any(k in v for k in ("fat", "carbohydrates", "proteins")):
        estimate = (
            9 * v.get("fat", ZERO)
            + 4 * v.get("carbohydrates", ZERO)
            + 4 * v.get("proteins", ZERO)
            + 2 * v.get("fiber", ZERO)
        )
        complete = all(k in v for k in ("fat", "carbohydrates", "proteins")) and estimate > 0
        tolerance = max(Decimal(20), estimate * Decimal("0.25"))
        if complete and abs(estimate - v["energy-kcal"]) > tolerance:
            report.warnings.append(
                f"Declared energy ({v['energy-kcal']} kcal) is far from what fat, carbohydrate "
                f"and protein suggest (about {estimate.quantize(Decimal(1))} kcal)."
            )
    return report
