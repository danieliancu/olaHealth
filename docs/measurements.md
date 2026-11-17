# Measurements (2026-11-17, local Docker on Windows 10, dev server)

Real measurements on this machine. They are indicative, not benchmarks.

## Lookup: local vs external (`manage.py off_smoke --fresh`, real OFF API)

| Code | First lookup (OFF) | 5 repeats, local median | OFF requests for 6 lookups |
|---|---|---|---|
| 3017620422003 | 443 ms | 6.0 ms | 1 |
| 5449000000996 | 367 ms | 7.7 ms | 1 |
| 3274080005003 | 391 ms | 5.7 ms | 1 |

Automated test `test_absent_product_is_fetched_once_and_persisted`: 6 scans → 1 OFF request.
`test_concurrent_lookups_make_a_single_off_request`: 4 simultaneous lookups → 1 request.

Observed separately: one OFF product image took 8.6 s to download from
`images.openfoodfacts.org` (images are loaded by the browser directly from OFF).

## OCR (`manage.py ocr_benchmark --runs 3`, Tesseract 5.3.0, eng+ron, 1 thread)

| Image | Size after preprocessing | Latency (min–max of 3) | Values proposed |
|---|---|---|---|
| label_en.jpg | 1112×632 | 778–1992 ms | 8 |
| label_ro.jpg | 1112×634 | 784–2200 ms | 8 |

Peak RSS: Python process 79 MiB, Tesseract subprocess 79 MiB.

Accuracy on the two **synthetic** labels (`pytest -m ocr`): 8 of 9 expected values correct
on each, 0 wrong values (missed values are left empty for the user). These are generated
images, not real phone photos; no claim is made about accuracy on real labels. A
benchmark on a set of real photos is needed before any engine change.

## Database queries per page (`manage.py measure_queries`, demo + 3 OFF products)

| Page | Queries |
|---|---|
| Home `/` | 0 |
| Scan `/scan/` | 0 |
| Products (list, filters, search + sort) | 4 |
| Product page | 5 |
| Compare (3 products) | 3 |
| Saved, Sources | 0 |

Upper bounds are asserted in `test_main_pages_render_with_bounded_queries`.

## Browser

Chrome headless with DevTools device emulation, 390×844 (mobile) and 1280×900 (desktop):
no horizontal overflow and no console errors on Home, Scan, Products, Product, Compare,
Saved, Sources and Contribution (steps 1–2). Scripted checks: Save → Saved list, Compare
selection from two product pages → `/compare/?ids=…`, corrupt localStorage reset, "Clear
saved products". Camera scan with Chrome's fake camera (an EAN-13 image): zxing-wasm
decoded it and the result card appeared after ~1.6 s, and the camera was stopped.
Real-device testing (iOS Safari, Android Chrome with the native BarcodeDetector) has not
been done.
