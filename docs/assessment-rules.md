# Assessment rules

AskFoodo shows indicators published by Open Food Facts and explains their state, plus the
**Foodo Score** (`score.v3`, section at the end): AskFoodo's conversion of the official
Nutri-Score to x/100, or an explicit reason why there is none. No "safe for allergies"
statements, no disease claims; Ask Foodo may use an AI model only to phrase facts computed here.

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

## Foodo Score (score.v3)

One grade for the product, "Foodo Score", shown as `x/100`: AskFoodo's conversion of the
official Nutri-Score points that Open Food Facts computed, with AskFoodo's own display bands.
100 is the most favourable end of the scale, 0 the least; 0/100 is a valid score.
Code: `assessments/foodo.py` (pure formula and official tables) and `assessments/score.py`
(statuses, stored result). Not validated clinically, scientifically or by a nutrition
professional.

### What it measures, and what it does not

* **Measures:** the official Nutri-Score result per 100 g / 100 ml (OFF's points and grade, for
  its algorithm version and calculation category), placed on 0–100 for easier reading.
* **Does not measure:** processing (NOVA), additives, allergens or diets, portion size,
  personal targets, safety or how "healthy" a food is. A difference of 10 Foodo points is not
  a clinical or percentage difference in health.

### Inputs

From the current revision (or a raw OFF product, e.g. a search result), read with the same
rules (`assessments.rules.derive_from_off`):

| Input | Where | |
|---|---|---|
| Nutri-Score points | snapshot `nutriscore.value.score` (OFF `nutriscore_score`) | formula |
| Nutri-Score grade | snapshot `nutriscore.value.grade` (OFF `nutriscore_grade`) | consistency check |
| Algorithm version | snapshot `algorithm_version` (OFF `nutriscore_version`) | table |
| Calculation category | OFF `nutriscore.<version>.data` flags, kept in `revision.off_payload` | table |

**Calculation category** (the formula's category; distinct from the commercial category used
to find alternatives), from OFF's flags:

| Version | Flag | Category |
|---|---|---|
| 2023 | `is_water` (with `is_beverage`) | water |
| 2023 | `is_beverage` | beverage |
| 2023 | `is_fat_oil_nuts_seeds` | fats, oils, nuts and seeds |
| 2023 | `is_cheese` | cheese |
| 2023 | `is_red_meat_product` | red meat products |
| 2023 | none set | general foods |
| 2021 | `is_water`, `is_beverage`, `is_fat`, `is_cheese`, none | water, beverage, fats, cheese, general |

All the version's flags must be present; two categories at once (e.g. beverage and cheese)
is a contradiction. Either gives `unsupported`.

### Official thresholds (OFF version "2023" = updated algorithm; "2021" = 2017 algorithm)

`p_min`/`p_max` are the theoretical extremes: −(maximum positive points) and the maximum
negative points.

| Version · category | p_min..p_max | A | B | C | D | E |
|---|---|---|---|---|---|---|
| 2023 · general foods | −17..55 | −17..0 | 1..2 | 3..10 | 11..18 | 19..55 |
| 2023 · cheese | −17..55 | −17..0 | 1..2 | 3..10 | 11..18 | 19..55 |
| 2023 · red meat (protein ≤ 2 points) | −12..55 | −12..0 | 1..2 | 3..10 | 11..18 | 19..55 |
| 2023 · fats, oils, nuts and seeds | −17..55 | −17..−6 | −5..2 | 3..10 | 11..18 | 19..55 |
| 2023 · beverages | −18..54 | water only | −18..2 | 3..6 | 7..9 | 10..54 |
| 2023 · water | — | A by category | | | | |
| 2021 · general foods, cheese, fats | −15..40 | −15..−1 | 0..2 | 3..10 | 11..18 | 19..40 |
| 2021 · water | — | A by category | | | | |

Component maxima behind the bounds: 2023 foods negative 0–55 (energy 10, sugars 15, saturates
10, salt 20), positive 0–17 (protein 7, fibre 5, fruit/vegetables/legumes 5); red meat protein
capped at 2; 2023 beverages negative 0–54 (adds sweeteners 4), positive 0–18 (protein 7,
fibre 5, fruit/vegetables 6); 2017 foods negative 0–40, positive 0–15 ("de −15 à +40").
Some extremes are not reachable by real foods (e.g. 55 for foods, the beverage minimum), so in
practice products rarely sit exactly at 0 or 100.

Sources:

* Scientific Committee of the Nutri-Score, update report 2022 (foods), annex 1.4, §2.4.3 and
  annex 2.1: <https://mpc.gouvernement.lu/dam-assets/le-minist%C3%A8re/consodur/2022-main-algorithm-report-update-final.pdf>
* Scientific Committee, 2023 report (beverages), §6.4 and §6.7: <https://edepot.wur.nl/629571>
* Draft French decree notified to the EU (2023), sections 2-1 to 2-3:
  <https://technical-regulation-information-system.ec.europa.eu/mt/notification/25002/text/D/EN>
* Nutri-Score Q&A, Santé publique France, 26 June 2024, tables 1–10:
  <https://www.blv.admin.ch/dam/fr/sd-web/xemdwgNevqQT/faq-nutri-score-en.pdf>
* 2017 algorithm: annex of the French decree of 31 October 2017 (ministry press kit):
  <https://agriculture.gouv.fr/telecharger/87510>
* Implementation cross-check: Open Food Facts `lib/ProductOpener/Nutriscore.pm`.

**Not supported** (status `unsupported`, a short message instead of a score):

* 2021 (2017 algorithm) beverages: the official 2017 table prints the B band as "Min à −1",
  which leaves 0 and 1 without a grade; the corrected 2019 text could not be read. OFF uses
  B ≤ 1, but we do not adopt an unverified bound.
* Any other version, and products whose category cannot be identified from OFF's flags.

### Formula

AskFoodo display bands: **E 0–19, D 20–39, C 40–59, B 60–79, A 80–100** (our own choice).

1. The official band is found from the points `p`, with the table of the version and category.
2. Inside it, inverse linear interpolation:
   `foodo = L + (U − L) × (p_max − p) / (p_max − p_min)`, where `L`, `U` are the ends of the
   Foodo band and `p_min`, `p_max` the lowest and highest official points of that band.
3. A band with a single possible score uses the middle of the Foodo band. Water is graded A by
   its category, so it is always (80 + 100) / 2 = **90**.
4. One rounding, at the end, to the nearest integer, halves up (exact fractions before that).

Worked example: 2023 general foods, p = 5 → C (3..10) → 40 + 19 × (10 − 5) / (10 − 3) =
53.57 → **54/100**.

### Status: exact rules, first match wins

| # | Situation | `status` | Reason code |
|---|---|---|---|
| 1 | no Nutri-Score, `missing_data`, grade not a–e | `not_enough_data` | `nutriscore_missing` |
| 2 | snapshot invalidated by a local correction | `not_enough_data` | `nutriscore_not_current` |
| 3 | Nutri-Score not applicable | `not_applicable` | `nutriscore_not_applicable` |
| 4 | package grade differs (same or unknown version) | `conflicting` | `nutriscore_conflicting` |
| 5 | algorithm version unknown | `not_enough_data` | `nutriscore_version_unknown` |
| 6 | category unknown or contradictory | `unsupported` | `category_unknown` |
| 7 | version and category not in the table | `unsupported` | `category_version_unsupported` |
| 8 | points missing | `not_enough_data` | `nutriscore_points_missing` |
| 9 | points not an integer or outside `p_min..p_max` | `inconsistent` | `points_out_of_range` |
| 10 | OFF's letter ≠ the official band of the points | `inconsistent` | `grade_mismatch` |
| 11 | otherwise | `ok` | `foodo_converted` |

A product with only a NOVA group (e.g. NOVA 1) and no valid Nutri-Score is `not_enough_data`.
Only `ok` carries a value. The interface shows a short message instead:
"Not enough data", "Doesn't apply to this product", "Data being checked" (conflicting,
inconsistent), "Not available for this kind of product yet" (unsupported).

### Result (stored)

```json
{"version": "score.v3", "status": "ok",
 "foodo": {"value": 54, "band": "C", "formula": "foodo-interp.v1"},
 "nutriscore": {"status": "available", "grade": "c", "points": 5, "version": "2023",
                "category": "general", "source": "off_computed", "other_versions": {},
                "packaging_grade": null, "warnings": []},
 "comparable_key": "nutriscore:2023:general",
 "separate": {"nova": {...}, "additives": {...}},
 "missing": [], "reasons": [{"code": "foodo_converted", "text": "..."}],
 "inputs_hash": "<sha256 of the indicators and category flags read>",
 "source": "off_api", "data_id": "<revision content hash>"}
```

The original points, grade, version and category are kept (internal use, checks, API); pages
show only the Foodo value.

### Comparing

Two Foodo Scores are comparable only with the same Nutri-Score version **and** calculation
category (`comparable_key`). An alternative must have a **strictly higher** displayed value
(equal after rounding is excluded); alternatives are sorted by value, highest first, and come
only from the product's own commercial category. Without a valid score for the product, no
other product is called better. The comparison page shows each value and warns when versions
or categories differ.

### When it is computed

With each published revision (`publish_revision`: OFF import from the API or a dump, an
approved correction, a package grade), stored in `ProductRevision.foodo_score` and denormalised
in `Product.foodo_value` (lists). Pages, the comparison, alternatives, the API and Ask Foodo read
the stored result. A result missing, of another version or without provenance is computed in
memory on read and stored by the hourly job `assessments.tasks.recompute_foodo_scores`
(`manage.py recompute_foodo_scores [--all]`), which writes only those two derived columns:
revision data, corrections and history are untouched. Migration `catalog.0012` stores v3 for
current revisions. Any change to the formula or tables gets a new version string.

### Display

The Foodo Score is the only grade AskFoodo shows for a product; the Nutri-Score letter A–E is
never shown as the product's grade (no setting, no letter fallback).

* Product page: the "Foodo Score" component (large `x/100`, readable without colour, plus a
  0–100 bar) in the product card; "Processed food" stays where it is. A short message replaces
  the value when there is none. Staff also see the details (status, category, version,
  original points and letter, data id).
* Alternatives card: only its title ("Better alternatives in {country}", or "Better
  alternatives" when the country is unknown), the products and their `x/100`.
* Comparison, product lists, suggestions and Learn products: `x/100`.
* Search: filter by score range (80–100, 60–79, 40–59, 20–39, 0–19, no score) and sort by
  Foodo Score, highest first.
* Ask Foodo: score answers, product suggestions and AI facts use `x/100`, never the letter or
  raw points.
* API: `foodo_score` (the stored result) for everyone; the source fields (`nutriscore`,
  `indicators`) stay for compatibility.
* A Nutri-Score printed on the package is not shown; it only serves to detect a conflict.
* Methodology page (Sources, `#foodo-score`): source, bands, formula, worked example, the
  table, unsupported cases and what the score does not say.

### Market for alternatives

Worked out from the browser, never asked (`myola/services/market.py`): its time zone (IANA
`zone.tab`: `Europe/London` → United Kingdom, `Europe/Bucharest` → Romania), else the region of
the browser language (`en-GB`, `ro-RO`). No IP lookup, no permission prompt. The same country is
used in the title, the local filter, the OFF search and its cache key. Unknown country: no
country is named and the search covers every country. Products are "listed for this market on
Open Food Facts"; availability in shops is unknown.

### Personal fit stays separate

Diets and allergies are warnings (`myola.restrictions`) in the "For you" card, never "safe":
red for a problem, orange for a doubt. The profile never changes the score.

### Open points

1. Review the formula, the display bands and the worked examples (tests in
   `tests/test_foodo_formula.py`, real products in `tests/test_score_v3.py`).
2. Check the score on a sample of products across categories against their labels.
3. Decide what to do with 2021-version beverages (unsupported today).
4. Nothing here has been reviewed by a nutrition professional; the public text must not say
   otherwise unless such a review takes place and is recorded.

### score.v2 (retired)

The official Nutri-Score letter and version only, no number. Replaced by v3; stored v2
results are recomputed.

### score.v1 (retired)

Kept in `assessments/score.py` as `foodo_score_v1` for reference only; no page, API or AI
fact uses it. Differences:

| | score.v1 | score.v2 |
|---|---|---|
| Number | 0–100, own weights | official Nutri-Score points |
| Nutri-Score | −0…−55 by grade, all versions alike | grade + points + version; versions never compared |
| NOVA | −10 / −20 | shown separately |
| Nutrient levels | −5 per "high" (double count) | not used |
| Additives | empty list, 0 points | listed separately, never scored |
| Missing data | 0 points (looked good) | no number, explicit reason |
| Conflict with the package | scored with OFF's grade | `conflicting`, no number |
| Computed | on every display | per revision, stored |

v1 rule, for the record. Start at 100, then add the points below (result clamped to 0–100):

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
* The score was computed on demand and never stored.
