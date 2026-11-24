# Local Open Food Facts catalogue

AskFoodo works today in **API mode**: a scanned or typed barcode is looked up locally, and a
product not yet known is fetched from the Open Food Facts product API and saved
(`catalog/services/lookup.py`). This page describes how the app moves to a **local catalogue**
built from an OFF dump, without breaking the API mode on the way.

## Modes

| | API mode (today) | Local catalogue |
|---|---|---|
| Barcode lookup | local first, else OFF API, then saved | same |
| Product missing locally | OFF API, then saved | same |
| Stale local copy | shown, refresh queued in the background (rate-limited) | same |
| OFF API down | local copy shown, nothing deleted | same |
| Alternatives | local products + OFF search (cached 24 h) | local catalogue only |
| Periodic refresh (`refresh_stale_products`) | stale products, in small batches | products fetched through the API only; dump products wait for the next dump or a view |

The mode is decided by `catalog/services/catalog_mode.py`:

* **local** when staff Settings → *Product catalogue* is **Automatic** (default) **and** at
  least one catalogue import has status `verified`;
* **API** otherwise. A running, failed or rejected import never switches the mode, and an
  earlier verified import stays valid when a later one fails.
* **Force the API mode:** staff Settings → *Product catalogue* → **API only** (audited). This
  is also the way back if something is wrong with the local catalogue.

The staff dashboard shows the mode, why, and the latest import with its checks.

## The dump

Source: the official Open Food Facts JSONL export
(`https://static.openfoodfacts.org/data/openfoodfacts-products.jsonl.gz`), one product object
per line with the same field names as the product API. Data: Open Food Facts contributors,
Open Database License (ODbL) 1.0; product photos: CC BY-SA.

Fields read (the same as the API, `off/client.py` `PRODUCT_FIELDS`):

* identity: `code`, `last_modified_t`, `lang`, `lc`;
* product: `product_name`, `product_name_en`, `generic_name`, `brands`, `quantity`,
  `product_quantity`, `product_quantity_unit`, `serving_size`, `categories_tags`,
  `countries_tags`;
* ingredients and allergens: `ingredients_text`, `ingredients_lang`, `allergens_tags`,
  `traces_tags`, `additives_tags`, `ingredients_analysis_tags`;
* nutrition: `nutriments`, `nutrition_data_per`, `nutrition_data_prepared_per`,
  `no_nutrition_data`, `nutrient_levels`;
* indicators: `nutriscore_grade`, `nutriscore_score`, `nutriscore_version`, `nutriscore`
  (per version), `nova_group`, `nova_groups_tags`, `misc_tags`,
  `data_quality_warnings_tags`, `states_tags`;
* photos: `image_front_url`, `image_ingredients_url`, `image_nutrition_url`.

OFF leaves a field out of a product when it has no value (for example no NOVA group), so a
missing field on one line is normal; a field missing from the whole dump is not.

### How the dump differs from the API

The dump is written in OFF's current product schema (`schema_version` 1003/1004); the API
(v2) still answers in the older one. Three differences matter:

* **Photos.** The dump has no `image_front_url` / `image_ingredients_url` /
  `image_nutrition_url`. `off/images.py` builds the same URLs from the `images` metadata,
  in either of its two shapes (`images.selected.<kind>.<lc>.rev`, current;
  `images.<kind>_<lc>.rev`, older):
  `https://images.openfoodfacts.org/images/products/<path>/<kind>_<lc>.<rev>.400.jpg`, where
  `<path>` is the code left-padded to 13 digits and split 3/3/3/rest. The language is the
  product's `lc`, then `lang`, then `en`, then the first available. Direct URLs win when
  present (API).
* **Nutrition.** In the current schema `nutriments` is empty and the values are in
  `nutrition.input_sets` (one set per source, preparation and basis). `off/mapping.py` reads
  `nutriments` first (API, older lines) and otherwise the sets whose `source` is
  `packaging` (preferred) or `manufacturer`. OFF's `estimate` sets are never declared values;
  `value_computed` alone is not a declared value; `mg` becomes `g`;
  `unspecified_nutrients` become "not on the label"; `no_nutrition_data_on_packaging` means
  "no nutrition facts on the package". The per-100 basis follows the same rule as
  `nutriments` (product quantity, else product type), so the API and the dump agree.
* **`ingredients_lang`** is absent: the ingredient language falls back to `lang`, as before.

Barcodes whose check digit is wrong are skipped (counted as `skipped`), as for the API.

### What is kept per revision

`off_payload` keeps only what the app reads again (`off/fields.py` `STORED_FIELDS`): the API
field list (`off/client.py` `PRODUCT_FIELDS`) plus `images` (selected photos' `imgid`/`rev`
only) and `nutrition` (declared sets, our nutrients' `value`/`unit`/`modifier` only).
Mapping, indicators and the Foodo Score use the same trimmed payload, so `remap_off` from a
stored payload gives exactly the original import. Older revisions keep their full payload
(revisions are immutable); `remap_off` reads both.

### Photos on re-import

`importer._sync_off_images`: a payload with no photo information changes nothing; a kind
missing from the payload keeps its current photo (an OFF photo is switched off only when OFF
names another photo of the same kind); a kind with an approved local photo keeps it (OFF
photos of that kind stay off); photos switched off by staff stay off. The import never
downloads photos: they are cached when first viewed (`catalog/views.py` `off_image`).

## Import, verification, activation

```
python manage.py import_off_dump openfoodfacts-products.jsonl.gz \
    --country en:united-kingdom --exported-at 2026-11-20
python manage.py verify_catalog_import          # again, after a fix (latest import)
```

`import_off_dump` (`catalog/services/catalog_import.py`):

* records a `CatalogImport` (file name, SHA-256, dump date, counters, report);
* sends every line through the same path as an API product
  (`import_off_product(..., source="off_dump")`): the same normalisation, indicator rules
  and Foodo Score, approved local corrections and history kept, a line older than the
  stored copy skipped, no photo downloads queued (photos are cached when first viewed);
* one bad line never stops the import (counted as failed, first errors kept in the report);
  an interruption marks the import `failed`;
* verifies the import at the end.

The import is **verified** only if every check passes; otherwise it is **rejected** and the
mode does not change:

| Check | Rule |
|---|---|
| finished | the import completed |
| enough_products | at least `OFF_DUMP_MIN_PRODUCTS` imported (default 1000) |
| few_failures | failed lines ≤ `OFF_DUMP_MAX_FAILED_RATIO` of all lines (default 0.5%) |
| fields_present | every indicator and content field above appears in the dump |
| identity_fields | `code` and `last_modified_t` on at least 99% of lines |
| nothing_lost | no fewer published products than before the import |
| scores_stored | every imported current revision has a stored Foodo Score |
| same_score_as_api | on a sample, the stored score equals the score of the same OFF data read as an API response |

Once verified, the local catalogue is used at once (no restart needed).

## Pilot (2026-11-23)

`tools/off_pilot.py` (sample → run → compare → check-urls; outputs in the git-ignored
`data/openfoodfacts/pilot/`). Dump of 2026-11-23: 4,929,984 lines, 86.8 GB uncompressed
(17.6 KB per line on average), 13.8 GB gzipped.

**Sample.** 26,957 lines: 1,000 at random from each block of 250,000 lines (20 blocks, the
whole file), plus up to 3,000 Romania, 2,000 UK, 1,000 beverages and 1,000 incomplete lines.
Same file for both runs, each into a fresh database. Limits: 0.55% of the dump; timings on
Docker Desktop (Windows, bind-mounted source), not on the VPS.

| | before (old code) | after |
|---|---|---|
| imported / skipped (bad barcode) / failed | 25,985 / 972 / 0 | 25,985 / 972 / 0 |
| verification | verified | verified |
| first import | 1,293 s (20.9 lines/s) | 1,338 s (20.1 lines/s) |
| re-import of the same file | 707 s, 0 new revisions | 716 s, 0 new revisions |
| products with an OFF photo | 0 | 20,306 (front 19,683; ingredients 7,129; nutrition 12,055) |
| products with nutrition facts | 3,942 | 17,677 |
| `off_payload` per revision (JSON / stored) | 19.4 KB / 7.4 KB | 3.9 KB / 2.1 KB |
| database (minus an empty migrated one, 11 MB) | 371 MB (14.3 KB/product) | 271 MB (10.4 KB/product) |
| `pg_dump -Fc` | 83 MB | 31 MB |
| photo downloads queued / cache files written | 0 / 0 | 0 / 0 |

Compared product by product: identical Foodo Score, Nutri-Score, NOVA, revisions and
identifiers for all 25,985; identical product data except 13,735 products that gained their
nutrition (current schema) and 158 now marked "no nutrition facts on the package". A local
correction (name + nutrition) on 3 products survived a newer OFF line: values kept, one
more revision, an open `data_conflict` flag, no duplicates. 150 built photo URLs (front,
ingredients, nutrition; EAN-13 and EAN-8) all answered 200 `image/jpeg`.

**Whole-dump estimate** (×4.74 M products with a valid barcode; ±15% from the sample):
PostgreSQL ~49 GB data + indexes before any update; one `pg_dump` ~5.6 GB; a first import
~65 h at the pilot rate. Products usable in lists (name, and nutrition or photo): 72%.
Romania: 51,645 products (~0.5 GB); UK: 201,408 (~2 GB).

## Local working database (2026-11-24)

The app runs locally on **`olahealth_pilot`**: a copy of the app's database (`olahealth`:
accounts, diaries, notes, chat, contributions, Magazine articles and cover, site settings,
schedules) with the pilot sample imported into it through the validated importer.

```sh
# 1. copy the app's database (the originals are not touched)
docker exec olahealth-postgres-1 sh -c 'createdb -U olahealth -T template0 olahealth_pilot &&
  pg_dump -U olahealth -Fc olahealth | pg_restore -U olahealth -d olahealth_pilot --no-owner'
# 2. import the original pilot sample (26,957 lines), with the dump's export time
MSYS_NO_PATHCONV=1 docker compose -f compose.yaml -f compose.dev.yaml run --rm --no-deps   -e POSTGRES_DB=olahealth_pilot web python manage.py import_off_dump   /app/data/openfoodfacts/pilot/sample.jsonl.gz --exported-at 2026-11-23T19:42:33+00:00
# 3. use it: POSTGRES_DB=olahealth_pilot in .env, then
docker compose -f compose.yaml -f compose.dev.yaml up -d   # migrate runs at start
```

* `pg_dump | pg_restore` keeps every id and relation; a `TEMPLATE` copy is not used because
  web and worker keep connections to `olahealth`.
* `tools/off_pilot.py run` is **not** used: it drops its database and applies three artificial
  corrections (`pilot_after`). `pilot_before`, `pilot_after` and `olahealth` stay as they were.
* Media (Magazine photos, cover, cached OFF photos) live in the `private_media` volume and are
  referenced by path, so the copy shows them unchanged.
* Products that were already in the app and are also in the sample are updated in place (same
  product, one more revision when the data differ), never duplicated.

## Next stage (not done yet)

* **Bulk loading** of the full dump (millions of products): the current importer uses one
  transaction per product, which is correct but slow; a bulk path (COPY into staging tables,
  batched revisions) is needed for the full file.
* **Sync through OFF's delta exports** (daily changes) instead of re-importing everything,
  and removal of products deleted from OFF.
* **OFF API v3** for single-product reads and refreshes.
* Scheduling of regular imports and alerts on rejected ones.
