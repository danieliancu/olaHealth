# Architecture

Modular Django monolith. One implementation of each business rule (a service function),
used by the HTML views, the JSON API, the admin and the background jobs.

```
off/            Open Food Facts adaptor: codes.py (normalisation), client.py (httpx), mapping.py
catalog/        Product, ProductIdentifier, ProductRevision, NutrientFact, ProductImage,
                LookupState, ReviewFlag; services: lookup, importer (reconcile), revisions,
                search; views, API (DRF), export, admin
assessments/    AssessmentSnapshot; rules.py (interpretation of OFF indicators), compare.py
contributions/  Contribution, ContributionImage, OCRRun, ModerationAudit; services: drafts,
                ocr, moderation; nutrition_parser + versioned dictionary; validation; admin
core/           cross-process rate limits and leases (PostgreSQL), image validation,
                barcode decoding (zxing-cpp), security headers, health endpoints, UI tags
```

## Decisions and assumptions

* **OFF client: httpx, not the official SDK.** `openfoodfacts` 5.4.1 depends on
  `requests`, `pydantic` and `tqdm` and exposes a single timeout; it does not surface
  `Retry-After` or a typed error taxonomy. We need connect/read timeouts, a host
  allow-list, a cross-process rate gate and six explicit error kinds
  (`invalid_code, not_found, timeout, rate_limited, upstream_unavailable,
  invalid_response`), so `off/client.py` is a ~150-line adaptor. API v2, `fields=` limited.
* **OFF limits:** the docs state 15 product reads/min/IP. Default shared budget:
  `OFF_RATE_LIMIT_PER_MINUTE=12`, counted in PostgreSQL across all web and worker processes.
  `Retry-After` is honoured when ≤ `OFF_RETRY_AFTER_CAP` (5 s), otherwise reported as
  `rate_limited`. At most 2 retries for timeouts / 5xx.
* **No Redis.** Coordination uses two tables:
  * `core.RateCounter` – fixed windows incremented by one `INSERT … ON CONFLICT DO UPDATE`.
  * `core.Lease` – expiring leases acquired by one conditional upsert. A lease guards each
    OFF fetch, so concurrent scans of the same code make one request. No transaction or
    row lock is held during HTTP calls; the import runs afterwards in a short transaction.
* **Negative cache** only for explicit OFF "not found" (status 0), `OFF_NEGATIVE_TTL_MINUTES`.
* **Freshness:** `OFF_FRESH_TTL_HOURS` (7 days). A stale product is shown immediately with
  its date while a deduplicated background refresh is queued. Errors never remove data;
  the product shows "Last refresh from Open Food Facts failed".
* **Barcodes:** the original value and symbology are stored. Local identity = GS1 GTIN-14
  (left-padded) for codes with a valid check digit (UPC-E expanded to UPC-A). OFF queries use
  the documented OFF normalisation (strip leading zeros, pad ≤7 digits to 8, 9–12 digits to
  13). Check digits are verified only for 8/12/13/14-digit GTINs; a failing check digit is
  rejected unless the user confirms ("look it up anyway", stored as *unverified*). GS1
  restricted-circulation prefixes (020–029, 040–049, 200–299, 980–999, RCN-8) are flagged
  *restricted* and scoped by context (`off`, `country:<tag>`, `unscoped`, `demo`); they are
  never merged into a global identity. An identifier that already belongs to another
  product raises an `identity_conflict` review flag instead of merging.
* **Revisions:** `ProductRevision` is immutable (save/delete refuse). It stores the
  canonical data, per-field provenance, the mapped OFF data and raw OFF payload used as
  source. A content hash (data + indicators + OFF source) avoids identical revisions, so
  re-import is idempotent. Fields corrected by approved contributions are listed in
  `Product.locked_fields` and are never overwritten by re-imports; a different OFF value
  raises one `data_conflict` flag.
* **Unknown ≠ zero:** an unknown nutrient has no `NutrientFact`; "not on the label" is a
  separate status; `0` is a value. Allergens/traces: `listed`, `none_declared` (OFF
  `en:none`), or `unknown` (empty) – never "allergen free".
* **Units:** Decimal everywhere. Only mg→g is converted. No kJ↔kcal, salt↔sodium,
  g↔ml, or as sold↔prepared conversion. OFF `_100g` values are classified as per 100 g or
  per 100 ml from the product quantity unit; if unclear: "per 100 g or ml (not stated)".
* **Queries:** denormalised columns on `Product` (name, brand, categories/countries as
  arrays with GIN indexes, Nutri-Score, NOVA, completeness) for search, filters and
  DB-side sorting. Search: `UPPER(search_text)` GIN trigram index used by `LIKE` and
  word-similarity, plus a similarity ≥ 0.35 fallback for typos.

## Contributions and OCR

Photos → validation and re-encoding (Pillow: real type, ≤ 10 MB, ≤ 40 MP, min side 200,
decompression bombs rejected, EXIF orientation applied then all metadata dropped, JPEG
re-encode, no SVG) → private storage → `OCRRun` queued (ingredients / nutrition photos)
→ worker: moderate preprocessing (grayscale, resize, autocontrast 1 %) → Tesseract
`eng+ron`, `--psm 6`, timeout → words with boxes/confidence stored → nutrition parser
proposes mappings → user checks/edits values (step "Check") → validation → consent →
submission → moderation.

* OCR runs are unique per (image, engine, version, languages) and claimed with a
  conditional UPDATE, so duplicate tasks are no-ops. Engine errors are retried once
  automatically; timeouts are not. Users can retry a failed run (rate-limited). OCR failure
  never blocks manual entry. Worker: 1 process (`Q_WORKERS`), `OMP_THREAD_LIMIT=1`,
  container memory limit `WORKER_MEM_LIMIT` (768 MB), task timeout.
* Parser: dictionary `contributions/dictionaries/nutrients.v1.json` (EN/RO labels, basis
  markers). Unknown labels are returned as *unmapped*; only the first column is proposed;
  "may contain" is not interpreted. `k]` is accepted as an OCR misreading of `kJ`.
* Approval: transaction + product row lock. If the product changed since the
  contribution's base revision **in a field the contribution touches**, the contribution
  becomes `conflict` (no last-write-wins); a moderator can re-base it with a reason.
* Anonymous drafts are reached by a 43-character random token (unique index lookup, no
  enumeration). Abuse limits per session and per IP for lookup, uploads, scan photos,
  contributions and OCR retries.

## Security

CSRF on all forms; CSP without inline scripts (`wasm-unsafe-eval` for the decoder,
images only from self and the OFF image hosts); `Permissions-Policy` (camera self only);
`X-Frame-Options: DENY`; private files never under a public URL (draft photos via token,
staff via permission, public only after approval); no arbitrary URL fetching (only the
configured OFF base URL, redirects not followed, image URLs allow-listed); secrets from the
environment; secure cookies, HSTS and SSL redirect when `DEBUG=0`; `/healthz` and `/readyz`
return only a status. No GPS, no medical profiles, no accounts.

## Retention

* Drafts idle for 30 days are deleted with their photos.
* Photos of rejected contributions are deleted 30 days after rejection.
* Orphan files (no database row, older than 1 h) are deleted daily.
* Scan-from-photo images are processed in memory and never stored.

## Reuse by future clients

Native apps or a magazine can reuse `/api/v1/lookup/`, `/api/v1/products/`,
`/api/v1/products/<id>/` and `/api/v1/compare/`. They call the same services as the web UI.
