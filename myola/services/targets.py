"""Daily targets worked out automatically (Settings → Daily targets → Calculate automatically).

An estimate from age, sex, height, weight, activity level and goal, built only on published
references. Nothing is saved here: the user sees the values, may change them and saves.

* Resting energy: Mifflin-St Jeor (1990), the most reliable of the common equations within
  10% of measured values in validation studies:
  men ``10 x kg + 6.25 x cm - 5 x age + 5``, women ``... - 161``.
* Daily energy = resting energy x physical activity level (PAL), the FAO/WHO/UNU and SACN
  (2011) approach; see ``preferences.ACTIVITY_LEVELS`` (1.4 to 2.0).
* Goal: losing weight is about 600 kcal a day less (NHS, "Lose weight"), never below resting
  energy and never with a BMI under 18.5; recomposition 300 less; muscle first 250 more.
* Protein: 0.75 g per kg (UK reference nutrient intake, COMA 1991), 1.6 g per kg for the
  muscle goals (ISSN position stand 2017: 1.4-2.0 g/kg for exercising people). With a BMI of 30
  or more, the weight at BMI 25 is used (reference weight).
* Fat: 30% of energy (UK advice: at most 35%; the label reference intake of 70 g is ~31%).
* Carbohydrates: the rest of the energy (SACN 2015: about 50%).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal

from django.utils.translation import gettext as _

from myola import preferences as P

FIELDS = ("age", "gender", "height", "weight", "activity_level", "goal")
ADULT_AGE = 18
GOAL_KCAL = {"lose_weight": -600, "muscle_and_fat": -300, "muscle_first": 250, "eat_healthier": 0}
PROTEIN_G_PER_KG = {"muscle_and_fat": 1.6, "muscle_first": 1.6}
PROTEIN_DEFAULT_G_PER_KG = 0.75
FAT_SHARE = 0.30
UNDERWEIGHT_BMI = 18.5
REFERENCE_BMI = 25
HIGH_BMI = 30
# Sex in the resting-energy equation: men +5, women -161, "in between" the average of both.
SEX_CONSTANT = {"male": 5, "female": -161, "between": -78}
CHECK_WITH_GP = {"diabetes", "hypertension", "other"}
NHS_URL = "https://www.nhs.uk/better-health/lose-weight/calorie-counting/"


@dataclass
class Estimate:
    kcal: int = 0
    protein: int = 0
    carbs: int = 0
    fat: int = 0
    resting: int = 0
    notes: list[str] = field(default_factory=list)
    refused: str = ""  # why no estimate is given (shown instead of values)


def missing(profile) -> list[str]:
    """The details still needed, in the order they are asked."""
    return [name for name in FIELDS if getattr(profile, name) in (None, "")]


def needs_sex_choice(gender: str) -> bool:
    """Non-binary, other or prefer not to say: the user picks the equation for this estimate."""
    return gender not in ("male", "female")


def _round(value: float, step: int) -> int:
    """To the nearest ``step``, halves up (52.5 g → 55 g with steps of 5)."""
    return int(step * math.floor(value / step + 0.5))


def estimate(
    *,
    age: int,
    sex: str,
    height: Decimal | float,
    weight: Decimal | float,
    activity_level: str,
    goal: str,
    conditions: list[str] | tuple = (),
) -> Estimate:
    """Daily kcal, protein, carbs and fat for these details (``sex``: male, female, between)."""
    if age < ADULT_AGE:
        return Estimate(refused=_("Automatic targets are for adults (18 and over)."))
    height_cm, weight_kg = float(height), float(weight)
    pal = P.ACTIVITY_LEVELS[activity_level][2]
    resting = 10 * weight_kg + 6.25 * height_cm - 5 * age + SEX_CONSTANT.get(sex, -78)
    kcal = resting * pal
    bmi = weight_kg / (height_cm / 100) ** 2
    notes: list[str] = []
    change = GOAL_KCAL.get(goal, 0)
    if change < 0 and bmi < UNDERWEIGHT_BMI:
        change = 0
        notes.append(
            _(
                "Your BMI is under 18.5, so these targets keep your weight steady. "
                "Talk to your GP before trying to lose weight."
            )
        )
    kcal = max(kcal + change, resting) if change < 0 else kcal + change
    protein_weight = (
        REFERENCE_BMI * (height_cm / 100) ** 2 if bmi >= HIGH_BMI else weight_kg
    )  # reference weight for protein with a high BMI
    protein = protein_weight * PROTEIN_G_PER_KG.get(goal, PROTEIN_DEFAULT_G_PER_KG)
    kcal = _round(kcal, 10)
    protein = _round(protein, 5)
    fat = _round(kcal * FAT_SHARE / 9, 5)
    carbs = _round(max(kcal - protein * 4 - fat * 9, 0) / 4, 5)
    if set(conditions or ()) & CHECK_WITH_GP:
        notes.append(
            _("Because of your health answers, check these targets with your GP or a dietitian.")
        )
    return Estimate(
        kcal=kcal, protein=protein, carbs=carbs, fat=fat, resting=_round(resting, 10), notes=notes
    )
