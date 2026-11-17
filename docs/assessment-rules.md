# Assessment rules

olaHealth does **not** compute a score. It shows indicators published by Open Food Facts
and explains their state. No 0–100 score, no penalty for the number of additives, no
"safe for allergies" statements, no disease claims, no LLM-generated text.

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
