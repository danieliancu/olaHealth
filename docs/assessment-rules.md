# Assessment rules

FoodoTrack shows indicators published by Open Food Facts and explains their state, plus one
transparent summary, the **Foodo Score** (`score.v1`, section at the end): every deducted point
is listed with its reason. No "safe for allergies" statements, no disease claims; Ask Foodo may
use an AI model only to phrase facts computed here.

## Indicators

| Indicator | OFF fields | Data status |
|---|---|---|
| Nutri-Score | `nutriscore_grade`, `nutriscore_score`, `nutriscore_version`, `nutriscore{version}`, `misc_tags` | a–e → available; `not-applicable` → not applicable; anything else / missing → missing data; differs from a grade printed on the package (same or unknown version) → conflicting |
| NOVA | `nova_group`, `nova_groups_tags` | 1–4 → available; tag `not-applicable` → not applicable; otherwise missing |
| Nutrient levels | `nutrient_levels` | low/moderate/high per nutrient → available; otherwise missing |
| Ingredients analysis | `ingredients_analysis_tags` | at least one tag not ending in `-unknown` → available |
| Additives identified | `additives_tags`, `ingredients_text` | needs an ingredient list; an empty list is shown as "OFF identified no additives in the ingredient list" |

* The absence of a field never means "not applicable".
* The Nutri-Score algorithm version is stored and shown. Grades OFF also computed with
  another version are listed as details. Grades of different versions are never ranked
  together: catalogue sorting groups by version and a notice is shown; the comparison
  states that the versions are not comparable.
* OFF warnings in `misc_tags` (fruit/vegetable estimate from ingredients or category,
  missing fibre, missing category or nutrition data) are shown with the indicator.
* A Nutri-Score printed on the package (from a contribution) is stored as a separate
  snapshot with origin `packaging_declared` and never replaces OFF's.

## Two independent states

* **Data status:** available / missing data / not applicable / conflicting.
* **Sync status:** current / stale (older than the TTL) / refresh failed / local only.
  A temporary OFF error changes only the sync status; existing indicators stay.

## Invalidation after local corrections

Each indicator depends on canonical fields:

| Indicator | Depends on |
|---|---|
| Nutri-Score | nutrition, categories, ingredients (OFF estimates fruit/vegetable content from ingredients) |
| NOVA | ingredients, categories |
| Nutrient levels | nutrition |
| Ingredients analysis | ingredients |
| Additives | ingredients |

A snapshot stores a hash of the OFF input values of its dependencies. When a revision's
own values differ (an approved correction), the snapshot is marked **not current**: it is
struck through, labelled, excluded from the denormalised Nutri-Score/NOVA used for filters
and sorting, and shown as "not current" in comparisons. Photo, name, brand or quantity
changes invalidate nothing. A later OFF re-import recomputes the snapshots, but they stay
not current while the locked local values still differ from OFF's.

## Comparison

* 2–3 products. Nutrition is compared only within the same basis (per 100 g / per 100 ml /
  unspecified) and preparation (as sold / prepared). Per-serving values are shown but
  never compared.
* Category warnings: unknown category; different most-specific categories; no category in
  common.
* Difference sentences are deterministic: values sorted high to low, the difference
  between the highest and lowest, products without the value listed last. Zero is a
  value; unknown and "not on label" are shown as such.
* No winner, no ranking label.

## Data completeness

Share (0–100 %) of: name, brand, quantity, categories, ingredient list, front photo and the
seven core nutrients (energy kcal, fat, saturates, carbohydrate, sugars, protein, salt;
"no nutrition facts on the package" counts as complete). It measures data, not health.

## Foodo Score (score.v1)

A V2 summary (see docs/mvp-api-contracts.md §3), computed in Python by
`assessments/score.py` from the OFF indicators above. It is separate from the indicators:
they are still shown as published, and the score never replaces them.

Start at 100, then add the points below (result clamped to 0–100):

| Component | Points |
|---|---|
| Nutri-Score | A 0, B −10, C −25, D −40, E −55 |
| NOVA | 1–2 0, 3 −10, 4 −20 |
| Nutrient levels | −5 for each of sugars, salt, saturated fat at OFF level "high" |
| High-risk additives | −5 each, at most −15 |

* **Not enough data:** when both the Nutri-Score grade and the NOVA group are unknown, the
  result is `not_enough_data` and no number is shown. Otherwise each unknown component is
  listed in `missing` and scores 0 (unknown is never treated as good or bad on its own).
* **High-risk additives:** the codebase does not rate additives and OFF product data has no
  risk level, so the high-risk list is **empty in score.v1**: this component never costs
  points. A reviewed list will come with a new version.
* Only current, OFF-computed indicators are used: a snapshot invalidated by a local
  correction is ignored, a Nutri-Score printed on the package is not used.
* **No personal data:** diets, allergies, goals and health conditions never change the score.
* Result: `{"status": "ok", "score": 0–100, "version": "score.v1", "points": [{"reason",
  "points"}], "missing": [...]}`. The points list makes the score reconstructible.
* The score is computed on demand (`score_for_product`) and not stored in
  `AssessmentSnapshot`. Any change to the rule or weights gets a new version string.
