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

## Next stage (not done yet)

* **Bulk loading** of the full dump (millions of products): the current importer uses one
  transaction per product, which is correct but slow; a bulk path (COPY into staging tables,
  batched revisions) is needed for the full file.
* **Sync through OFF's delta exports** (daily changes) instead of re-importing everything,
  and removal of products deleted from OFF.
* **OFF API v3** for single-product reads and refreshes.
* Scheduling of regular imports and alerts on rejected ones.
