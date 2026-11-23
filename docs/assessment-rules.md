# Assessment rules

AskFoodo shows indicators published by Open Food Facts and explains their state, plus the
**Foodo Score** (`score.v2`, section at the end): the official Nutri-Score of the product, with
its algorithm version, or an explicit reason why there is none. No "safe for allergies"
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

## Foodo Score (score.v2)

Computed in Python by `assessments/score.py` (`foodo_score`, a pure, deterministic function)
from the indicators above. Not validated clinically, scientifically or by a nutrition
professional: it passes on a public algorithm, it does not add a judgement of its own.

### What it measures, and what it does not

* **Measures:** the nutritional quality of 100 g / 100 ml of the product as rated by the
  **official Nutri-Score**, computed by Open Food Facts with the algorithm version OFF states
  (`nutriscore_version`). The headline is the grade (A–E); the number is the official
  Nutri-Score points (`nutriscore_score`, lower is better), unchanged.
* **Does not measure:** processing (NOVA), additives, allergens or diets, portion size,
  whether the product suits a person or a goal, safety, or how "healthy" a product is overall.
  It is an orientative indicator; the physical product and its label remain the reference.

### Why there is no AskFoodo 0–100 total

`score.v1` combined Nutri-Score, NOVA and nutrient levels into a 0–100 total with weights of
our own. That total was not equivalent to any official score: the weights had no public
methodology, sugars/salt/saturates were counted twice (they are already Nutri-Score
components), NOVA (processing) was mixed with nutritional quality, and unknown components
cost nothing, so missing data raised the total. No public, verifiable methodology combines
Nutri-Score, NOVA and additives into one number, so `score.v2` keeps the official
Nutri-Score as the only numeric score and shows the rest separately. A composite total would
need a published methodology and a new version.

### Inputs

Only snapshots with origin `off_computed` of the product's current revision (an OFF search
result is first read with the same rules, `assessments.rules.derive_from_off`). The
Nutri-Score printed on the package (`packaging_declared`) is never an input; it is only used
to detect a conflict (section *Indicators*).

| Input | Snapshot | Used for |
|---|---|---|
| Nutri-Score grade | `nutriscore.value.grade` (a–e) | the score |
| Nutri-Score points | `nutriscore.value.score` (OFF `nutriscore_score`) | the score (detail) |
| Nutri-Score version | `nutriscore.algorithm_version` (OFF `nutriscore_version`) | comparability |
| Grades of other versions | `nutriscore.value.other_versions` | shown as detail only |
| NOVA group | `nova.value.group` | shown separately |
| Additives identified | `additives.value.tags` | shown separately |

Nutrient levels are Nutri-Score inputs already; they stay on the product page and are not
part of the result (no double counting).

### Status: exact rules, first match wins

| # | Nutri-Score snapshot (off_computed) | `status` | Reason code |
|---|---|---|---|
| 1 | none, or `missing_data`, or a grade that is not a–e | `not_enough_data` | `nutriscore_missing` |
| 2 | invalidated by a local correction | `not_enough_data` | `nutriscore_not_current` |
| 3 | `not_applicable` | `not_applicable` | `nutriscore_not_applicable` |
| 4 | `conflicting` (package grade differs, same or unknown version) | `conflicting` | `nutriscore_conflicting` |
| 5 | grade a–e, `algorithm_version` empty | `not_enough_data` | `nutriscore_version_unknown` |
| 6 | grade a–e, version known, current | `ok` | `nutriscore_official` |

* **Minimum for a score:** rule 6, i.e. a current OFF Nutri-Score grade a–e with a known
  algorithm version. Only then are `grade`, `points` and `version` filled in; in every other
  status they are `null` and pages and the API show no grade and no number.
* If OFF gives the grade but not the points, the result is still `ok`; `points` is `null`
  and `nutriscore_points` is listed in `missing` (never 0).
* NOVA, additives and data completeness never change the status, the grade or the points.

### Comparing two scores

Two results are comparable only if both are `ok` and have the same Nutri-Score version
(`comparable_key = "nutriscore:<version>"`). A product is "better" only with a strictly better
official grade (A < B < … < E) in the same version; the points are shown but never used to
rank, because the grade thresholds differ between product groups (general foods, beverages,
fats and oils, cheese) and between versions. The 2021 and 2023 algorithms are never compared:
the same product can be B in 2021 and C in 2023. Alternatives follow this rule.

### Shown separately, never part of the score

* **NOVA** (processing, Monteiro et al.): group 1–4 with "not part of the score"; missing,
  not applicable or not current are shown as such.
* **Additives:** the E-numbers OFF identified in the ingredient list, with "listed, not
  assessed". Additives listed in the EU/UK are authorised for their uses; AskFoodo has no
  reviewed source that rates individual additives, so none is called "risky" and none costs
  points. No ingredient list or no additive analysis → "unknown", never "none"; an empty list
  with an ingredient list → "OFF identified no additives in the ingredient list".
* **Allergens and diets:** personal-fit warnings (`myola.restrictions`), computed separately
  and never in the score result; never "safe".
* **Goals, portion, targets:** the "For you" card and daily targets, computed separately.
* **Data completeness:** `Product.completeness` (section above), shown next to the score.

### Result

```json
{"version": "score.v2", "status": "ok",
 "nutriscore": {"status": "available", "grade": "b", "points": 1, "version": "2023",
                "source": "off_computed", "other_versions": {"2021": "a"},
                "packaging_grade": null, "warnings": ["…"]},
 "comparable_key": "nutriscore:2023",
 "separate": {"nova": {"status": "available", "group": 3, "source": "off_computed"},
              "additives": {"status": "available", "tags": [], "count": 0,
                            "source": "off_computed", "assessed": false}},
 "missing": [], "reasons": [{"code": "nutriscore_official", "text": "…"}],
 "inputs_hash": "<sha256>"}
```

`missing` lists `nutriscore`, `nutriscore_points`, `nova` and `additives` when they have no
usable value. `inputs_hash` is a hash of exactly the inputs read (status, value, version,
current flag), so the same inputs always give the same result and hash.

### When it is computed

On every published revision (`catalog.services.revisions.publish_revision`), i.e. when an
OFF import (API or catalogue dump), an approved correction or a package grade changes the
data, and stored in `ProductRevision.foodo_score` with its provenance:

* `source`: `off_api` (product fetched from the OFF API), `off_dump` (catalogue import) or
  `contribution` (approved correction);
* `data_id`: the revision's content hash, i.e. exactly which data was scored.

API and dump data go through the same normalisation (`off.mapping.map_product`,
`assessments.rules.derive_from_off`) and the same function, so the same OFF data gives the
same result whatever its source (only `source` differs). Pages, the comparison, the API and
Ask Foodo read the stored result. A result that is missing, of another rules version or
without provenance is computed in memory when read (never written on display) and stored by
the hourly job `assessments.tasks.recompute_foodo_scores` (also
`manage.py recompute_foodo_scores [--all]`), which only writes that derived column: revision
data, approved corrections and history are untouched. Any change to these rules gets a new
version string, which makes the job recompute every stored result.

### Public display: off by default

`score.v2` is shown publicly under the name "Foodo Score" only when staff switch on
Settings → "Show the Foodo Score publicly" (`SiteSettings.foodo_score_public`, default off).

* **Off (default):** the product page shows "Nutri-Score and alternatives": the official
  Nutri-Score (grade, points, version) or why there is none, NOVA and additives separately
  with their explanations, and data completeness. The API returns `foodo_score: null` (and
  `foodo_score_public: false`) except to staff; Ask Foodo receives no Foodo Score. Staff see
  the full result on the product page (preview box) and in the staff product view.
* **On:** the same content under the name "Foodo Score", the result in the API for everyone,
  and its summary in Ask Foodo's facts.

### Before public activation

1. **Decide what "Foodo Score" means.** Either it is the official Nutri-Score shown as is
   (`score.v2`), or AskFoodo wants its own 0–100 number. There is no approved rule for a
   0–100 number: it would need a published methodology with verifiable sources for every
   weight, a new version (`score.v3`), and the same "not enough data" rules. Until then no
   number other than the official Nutri-Score points is shown.
2. **Check on real products.** Staff compare the preview with the product labels on a sample
   across categories (including beverages, fats, cheese, not-applicable products and
   products with local corrections) and record the outcome.
3. **Agree the wording** next to "Nutri-Score" in the interface and on the Sources page.
4. **Validation status.** Nothing here has been validated clinically, scientifically or by a
   nutrition professional; the public text must not say otherwise unless such a review takes
   place and is recorded.

### Personal fit stays separate

Diets and allergies ("For you", `myola.restrictions`) are warnings, never "safe". For a
signed-in user with daily targets, the "For you" card shows what one declared portion (the
serving, or one unit of a multipack; never converted between g and ml) gives towards each
target that is set (`myola.contribution`), from per-100 values on the label. The portion and
the profile never change the product's score.

### Sources

* Nutri-Score: Santé publique France, Nutri-Score specifications, and the reports of the
  Scientific Committee of the Nutri-Score on the updated algorithm for foods (2022) and for
  beverages (2023), together "Nutri-Score 2023"; the 2021 version is the earlier algorithm.
  AskFoodo does not recompute it: OFF applies the version it states.
* Open Food Facts: product data and computed indicators (`nutriscore_*`, `nova_group`,
  `additives_tags`), <https://world.openfoodfacts.org/nutriscore> and
  <https://world.openfoodfacts.org/nova>. Data under the Open Database License.
* NOVA: Monteiro C. A. et al., "Ultra-processed foods: what they are and how to identify
  them", Public Health Nutrition 22(5), 2019.
* Food additives: Regulation (EC) No 1333/2008 (retained in UK law).

### Limitations

* OFF data are crowd-sourced: values may be wrong, incomplete or out of date, and a
  recipe may change before the page is edited. The score is only as good as that data.
* OFF estimates some inputs (fruit/vegetable/legume share from the ingredients or category,
  missing fibre); its warnings are shown with the Nutri-Score.
* AskFoodo does not check OFF's arithmetic or thresholds; it relies on OFF's implementation.
* Products without a category or nutrition facts get no score (`not_enough_data`).

### Open questions (not blocking; nothing here has been validated externally)

* Wording of "Foodo Score" next to "Nutri-Score" in the interface.
* How to present products that OFF only rated with an older version.
* Any future additive or composite rule needs a published source and a new version.

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
