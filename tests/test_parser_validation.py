from decimal import Decimal

import pytest

from contributions.nutrition_parser import parse_nutrition_text
from contributions.validation import validate_facts

EN_LABEL = """Nutrition information Per 100g Per 30g serving
Energy 1966kJ / 470kcal 590kJ / 141kcal
Fat 21g 6.3g
of which saturates 9.8g 2.9g
Carbohydrate 61g 18g
of which sugars 28g 8.4g
Fibre 2.5g 0.8g
Protein 6.2g 1.9g
Salt 0.48g 0.14g
Vitamin Z 12mg
"""

RO_LABEL = """Declaratie nutritionala la 100 g
Valoare energetică 1580 kJ / 377 kcal
Grăsimi 12 g
din care acizi grași saturați 4,5 g
Glucide 58,2 g
din care zaharuri 22 g
Fibre 3,1 g
Proteine 7 g
Sare 0,9 g
Poate conține urme de alune.
"""


def by_nutrient(result):
    return {p.nutrient: p for p in result.proposals}


def test_english_table_first_column_and_both_energy_units():
    r = parse_nutrition_text(EN_LABEL)
    p = by_nutrient(r)
    assert r.basis == "per_100g"
    assert (p["energy-kj"].value, p["energy-kj"].unit) == ("1966", "kJ")
    assert (p["energy-kcal"].value, p["energy-kcal"].unit) == ("470", "kcal")
    assert p["saturated-fat"].value == "9.8"
    assert p["sugars"].value == "28"
    assert p["fiber"].value == "2.5"
    assert p["salt"].other_values == ["0.14 g"]  # second column kept, not used
    assert "fat" in p and p["fat"].value == "21"


def test_unknown_labels_are_left_unmapped():
    r = parse_nutrition_text(EN_LABEL)
    assert any("Vitamin Z" in line for line in r.unmapped)
    assert all(p.nutrient for p in r.proposals)


def test_romanian_table_with_diacritics_and_decimal_comma():
    r = parse_nutrition_text(RO_LABEL)
    p = by_nutrient(r)
    assert r.basis == "per_100g"
    assert p["energy-kcal"].value == "377"
    assert p["fat"].value == "12"
    assert p["saturated-fat"].value == "4.5"
    assert p["carbohydrates"].value == "58.2"
    assert p["sugars"].value == "22"
    assert p["fiber"].value == "3.1"
    assert p["proteins"].value == "7"
    assert p["salt"].value == "0.9"


def test_may_contain_is_not_interpreted():
    r = parse_nutrition_text(RO_LABEL)
    assert not any("alune" in p.line for p in r.proposals)
    assert r.dictionary_version == "1"


def test_no_conversion_between_units():
    r = parse_nutrition_text("Energy 1500 kJ\nSodium 400 mg")
    p = by_nutrient(r)
    assert "energy-kcal" not in p  # never derived from kJ
    assert (p["sodium"].value, p["sodium"].unit) == ("400", "mg")
    assert "salt" not in p  # never derived from sodium


def fact(n, v, unit="g"):
    unit = {"energy-kj": "kJ", "energy-kcal": "kcal"}.get(n, unit)
    return {
        "nutrient": n,
        "basis": "per_100g",
        "preparation": "as_sold",
        "status": "declared",
        "value": v,
        "unit": unit,
    }


def test_sugars_above_carbohydrates_is_an_error():
    r = validate_facts([fact("carbohydrates", "10"), fact("sugars", "12")], "per_100g")
    assert any("Sugars" in e for e in r.errors)


def test_rounding_tolerance_is_accepted():
    r = validate_facts(
        [
            fact("carbohydrates", "10"),
            fact("sugars", "10.4"),
            fact("fat", "3.0"),
            fact("saturated-fat", "3.1"),
        ],
        "per_100g",
    )
    assert r.ok, r.errors


def test_saturates_above_fat_is_an_error():
    r = validate_facts([fact("fat", "2"), fact("saturated-fat", "3")], "per_100g")
    assert not r.ok


def test_negative_and_wrong_units():
    r = validate_facts(
        [
            fact("fat", "-1"),
            fact(
                "energy-kcal",
                "100",
            ),
        ],
        "per_100g",
    )
    assert any("negative" in e for e in r.errors)
    bad_unit = {**fact("proteins", "5"), "unit": "ml"}
    assert not validate_facts([bad_unit], "per_100g").ok


def test_kj_kcal_and_salt_sodium_mismatch_are_warnings_not_rewrites():
    facts = [
        fact("energy-kj", "1000"),
        fact("energy-kcal", "100"),
        fact("salt", "1"),
        fact("sodium", "1"),
    ]
    r = validate_facts(facts, "per_100g")
    assert r.ok
    assert len(r.warnings) == 2
    assert facts[0]["value"] == "1000"  # unchanged


def test_per_serving_needs_serving_size():
    f = [{**fact("fat", "3"), "basis": "per_serving"}]
    assert not validate_facts(f, "per_serving").ok
    assert validate_facts(f, "per_serving", "30 g").ok


def test_impossible_and_unusual_values():
    assert not validate_facts([fact("energy-kcal", "950")], "per_100g").ok
    r = validate_facts([fact("salt", "15")], "per_100g")
    assert r.ok and r.warnings


@pytest.mark.parametrize("value", ["0", "0.0"])
def test_zero_is_a_valid_value(value):
    assert validate_facts([fact("sugars", value), fact("carbohydrates", "0")], "per_100g").ok


def test_form_leaves_empty_fields_unknown_and_converts_mg_only():
    from contributions.forms import ContributionForm

    form = ContributionForm(
        {"nutrition_basis": "per_100g", "n_sodium": "400", "u_sodium": "mg", "n_sugars": "0"},
        current={},
    )
    assert form.is_valid(), form.errors
    facts = {f["nutrient"]: f for f in form.proposed()["nutrition"]["facts"]}
    assert facts["sodium"]["value"] == "0.4" and facts["sodium"]["unit"] == "g"
    assert facts["sugars"]["value"] == "0"
    assert "salt" not in facts and "fat" not in facts
    assert Decimal(facts["sodium"]["value"]) == Decimal("0.4")


def test_common_ocr_misreading_of_kj_is_tolerated():
    p = by_nutrient(parse_nutrition_text("Energy 1966 k] / 470 kcal"))
    assert (p["energy-kj"].value, p["energy-kj"].unit) == ("1966", "kJ")
